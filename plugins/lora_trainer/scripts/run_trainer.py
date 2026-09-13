"""SDXL LoRA trainer subprocess. Runs INSIDE plugins/lora_trainer/venv-torch/.

Protocol: see plugins/lora_trainer/real_trainer.py (RealLoraTrainer)."""

import json
import sys
import traceback

_pipeline = None
_torch = None

def _dev():
    """Accelerator device: CUDA > MPS (Apple Silicon) > CPU."""
    if _torch is None:
        return "cpu"
    if _torch.cuda.is_available():
        return "cuda"
    if hasattr(_torch.backends, "mps") and _torch.backends.mps.is_available():
        return "mps"
    return "cpu"

def _eprint(msg):
    print(msg, file=sys.stderr, flush=True)

def _respond(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()

def _do_load(cmd):
    model_id = cmd.get("model_id", "stabilityai/stable-diffusion-xl-base-1.0")
    global _pipeline, _torch
    if _pipeline is not None:
        return {"ok": True}
    
    _eprint(f"[run_trainer] loading {model_id}...")
    import torch
    _torch = torch
    
    if not torch.cuda.is_available() and not (
        hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
    ):
        return {"ok": False, "error": "No accelerator available — LoRA training requires CUDA or Apple MPS"}
        
    try:
        from diffusers import StableDiffusionXLPipeline
        _pipeline = StableDiffusionXLPipeline.from_pretrained(
            model_id, torch_dtype=torch.bfloat16, use_safetensors=True,
        ).to(_dev())
        _eprint(f"[run_trainer] {model_id} loaded")
    except Exception as e:
        return {"ok": False, "error": f"failed to load {model_id}: {e}"}
        
    return {"ok": True}

def _do_train(cmd):
    params = cmd.get("params", {})
    if _pipeline is None:
        return {"ok": False, "error": "model not loaded — call op=load first"}
        
    try:
        from peft import LoraConfig, get_peft_model
        
        unet = _pipeline.unet
        vae = _pipeline.vae
        text_encoder = _pipeline.text_encoder
        text_encoder_2 = _pipeline.text_encoder_2
        
        vae.requires_grad_(False)
        text_encoder.requires_grad_(False)
        text_encoder_2.requires_grad_(False)
        unet.requires_grad_(False)

        unet.to(_torch.bfloat16)
        # Gradient checkpointing trades a little compute for a lot of activation
        # memory — the difference between fitting a 16GB card and OOM.
        unet.enable_gradient_checkpointing()

        config = LoraConfig(
            r=params.get("rank", 16),
            lora_alpha=params.get("alpha", 16),
            target_modules=["to_q", "to_k", "to_v", "to_out.0"],
        )
        unet = get_peft_model(unet, config)
        
        from PIL import Image, ImageOps
        import torch.nn.functional as F
        from torchvision import transforms
        
        resolution = params.get("resolution", 1024)
        image_paths = params.get("ref_image_paths", [])
        
        if not image_paths:
            return {"ok": False, "error": "no ref_image_paths provided"}
            
        images = []
        for path in image_paths:
            img = Image.open(path).convert("RGB")
            # Center-crop to square then scale — squashing a portrait into a
            # square stretches the face/body and poisons identity. ImageOps.fit
            # crops the long edge so proportions survive.
            img = ImageOps.fit(img, (resolution, resolution), method=Image.Resampling.LANCZOS)
            img_tensor = transforms.ToTensor()(img)
            img_tensor = transforms.Normalize([0.5], [0.5])(img_tensor)
            images.append(img_tensor)
            
        images = _torch.stack(images).to(_dev(), dtype=_torch.bfloat16)

        instance_prompt = params.get("instance_prompt", "a photo")
        image_prompts = params.get("image_prompts") or []
        if not image_prompts:
            image_prompts = [instance_prompt] * len(image_paths)
        while len(image_prompts) < len(image_paths):
            image_prompts.append(image_prompts[-1] if image_prompts else instance_prompt)

        # Pre-encode per-image captions (SubjectSample prompts / .txt sidecars).
        all_prompt_embeds = []
        all_pooled_embeds = []
        for prompt in image_prompts[: len(image_paths)]:
            pe, _neg, pooled, _neg_pooled = _pipeline.encode_prompt(
                prompt=prompt,
                device=_torch.device(_dev()),
                num_images_per_prompt=1,
                do_classifier_free_guidance=False,
            )
            all_prompt_embeds.append(pe)
            all_pooled_embeds.append(pooled)

        # Memory plan for a 16GB card: the VAE and both text encoders are frozen
        # and only needed once. Pre-encode every ref image to a latent up front,
        # capture the (static) prompt embeds above, then evict VAE + text
        # encoders to CPU so the training loop runs with only the UNet resident.
        proj_dim = _pipeline.text_encoder_2.config.projection_dim  # grab before eviction
        with _torch.no_grad():
            all_latents = []
            for i in range(images.shape[0]):
                lat = vae.encode(images[i:i + 1]).latent_dist.sample() * vae.config.scaling_factor
                all_latents.append(lat)
            all_latents = _torch.cat(all_latents, dim=0)
        del images
        vae.to("cpu")
        text_encoder.to("cpu")
        text_encoder_2.to("cpu")
        if _torch.cuda.is_available():
            _torch.cuda.empty_cache()
        elif _dev() == "mps":
            _torch.mps.empty_cache()

        optimizer = _torch.optim.AdamW(
            filter(lambda p: p.requires_grad, unet.parameters()),
            lr=params.get("learning_rate", 1.0e-4)
        )

        steps = params.get("steps", 400)

        from accelerate import Accelerator
        # bf16 mixed precision is CUDA-only; MPS falls back to no mixed precision
        # (the UNet is already bf16). Accelerate's autocast device_type is inferred.
        _mixed = "bf16" if _dev() == "cuda" else "no"
        accelerator = Accelerator(gradient_accumulation_steps=2, mixed_precision=_mixed)

        unet, optimizer = accelerator.prepare(unet, optimizer)

        unet.train()
        for step in range(steps):
            with accelerator.accumulate(unet):
                idx = step % all_latents.shape[0]
                latents = all_latents[idx:idx + 1]

                noise = _torch.randn_like(latents)
                # Small noise offset — helps SDXL learn fuller contrast/darks and
                # tightens identity instead of drifting toward washed-out grey.
                noise = noise + 0.05 * _torch.randn(
                    latents.shape[0], latents.shape[1], 1, 1, device=latents.device, dtype=latents.dtype
                )
                bsz = latents.shape[0]
                timesteps = _torch.randint(0, _pipeline.scheduler.config.num_train_timesteps, (bsz,), device=latents.device)
                timesteps = timesteps.long()

                noisy_latents = _pipeline.scheduler.add_noise(latents, noise, timesteps)

                prompt_embeds = all_prompt_embeds[idx]
                pooled_prompt_embeds = all_pooled_embeds[idx]
                add_time_ids = _pipeline._get_add_time_ids((resolution, resolution), (0,0), (resolution, resolution), dtype=prompt_embeds.dtype, text_encoder_projection_dim=proj_dim).to(_dev())
                added_cond_kwargs = {"text_embeds": pooled_prompt_embeds, "time_ids": add_time_ids}
                
                model_pred = unet(noisy_latents, timesteps, prompt_embeds, added_cond_kwargs=added_cond_kwargs).sample
                
                loss = F.mse_loss(model_pred.float(), noise.float(), reduction="mean")
                accelerator.backward(loss)
                optimizer.step()
                optimizer.zero_grad()
                
        # Save
        output_path = params.get("output_path")
        unet = accelerator.unwrap_model(unet)
        
        # PEFT save_pretrained writes to a directory. 
        # But we need a single safetensors file as output.
        from safetensors.torch import save_file
        from peft import get_peft_model_state_dict
        from diffusers.utils import convert_state_dict_to_kohya
        state_dict = get_peft_model_state_dict(unet)
        # CRITICAL: raw PEFT keys (base_model.model.*.lora_A/lora_B) silently fail
        # to map in ComfyUI/A1111 — the LoRA loads to zero effect. Retag to unet.*
        # and convert to Kohya lora_unet_* format, the one inference engines load.
        # (Verified: raw keys -> 1120 "key not loaded" warnings; kohya -> 0.)
        def _retag(k: str) -> str:
            if k.startswith("base_model.model."):
                return k.replace("base_model.model.", "unet.", 1)
            return k if k.startswith("unet.") else "unet." + k
        state_dict = {_retag(k): v for k, v in state_dict.items()}
        state_dict = convert_state_dict_to_kohya(state_dict)
        # PEFT keeps adapters in fp32 even when the base is bf16. Casting on save
        # halves the file size with no real fidelity hit for SDXL LoRA inference.
        state_dict = {k: v.to(_torch.bfloat16) for k, v in state_dict.items()}
        save_file(state_dict, output_path)

        _eprint(f"[run_trainer] saved lora to {output_path} (kohya format, {len(state_dict)} keys)")
        
    except _torch.cuda.OutOfMemoryError as e:
        # Include the step at failure so the caller / UI can report "failed at step 312/800"
        # and users know whether a partial checkpoint might exist or how far it got.
        cur_step = step + 1 if 'step' in dir() else 'unknown'
        return {"ok": False, "error": f"OOM during training at step {cur_step}/{steps}: {e}"}
    except Exception as e:
        cur_step = step + 1 if 'step' in dir() else 'unknown'
        # MPS raises a plain RuntimeError (not torch.cuda.OutOfMemoryError) on
        # unified-memory exhaustion; surface it as an OOM-style failure.
        if "out of memory" in str(e).lower():
            return {"ok": False, "error": f"OOM during training at step {cur_step}/{steps}: {e}"}
        return {"ok": False, "error": f"training failed at step {cur_step}: {e}\n{traceback.format_exc()}"}
        
    return {"ok": True, "lora_path": output_path, "lora_version": 1}

def _do_unload(cmd):
    global _pipeline
    if _pipeline is not None:
        del _pipeline
        _pipeline = None
    if _torch is not None:
        if _torch.cuda.is_available():
            _torch.cuda.empty_cache()
        elif _dev() == "mps":
            _torch.mps.empty_cache()
    return {"ok": True}

def _do_shutdown(cmd):
    return {"ok": True}

OPS = {
    "ping": lambda cmd: {"ok": True, "ready": _pipeline is not None},
    "load": _do_load,
    "train": _do_train,
    "unload": _do_unload,
    "shutdown": _do_shutdown
}

def main():
    _eprint("[run_trainer] daemon ready, waiting on stdin...")
    for line in sys.stdin:
        try:
            cmd = json.loads(line)
            op = cmd.get("op")
            handler = OPS.get(op)
            if handler is None:
                _respond({"ok": False, "error": f"unknown op: {op}"})
                continue
            response = handler(cmd)
            _respond(response)
            if op == "shutdown":
                return
        except Exception as e:
            _respond({"ok": False, "error": f"daemon crash: {e}\n{traceback.format_exc()}"})

if __name__ == "__main__":
    main()
