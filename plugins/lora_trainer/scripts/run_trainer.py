"""SDXL LoRA trainer subprocess. Runs INSIDE plugins/lora_trainer/venv-torch/.

Protocol: see plugins/lora_trainer/real_trainer.py (RealLoraTrainer)."""

import json
import sys
import traceback

_pipeline = None
_torch = None

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
    
    if not torch.cuda.is_available():
        return {"ok": False, "error": "CUDA not available — LoRA training requires a GPU"}
        
    try:
        from diffusers import StableDiffusionXLPipeline
        _pipeline = StableDiffusionXLPipeline.from_pretrained(
            model_id, torch_dtype=torch.bfloat16, use_safetensors=True,
        ).to("cuda")
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
        
        config = LoraConfig(
            r=params.get("rank", 16),
            lora_alpha=params.get("alpha", 16),
            target_modules=["to_q", "to_k", "to_v", "to_out.0"],
        )
        unet = get_peft_model(unet, config)
        
        from PIL import Image
        import torch.nn.functional as F
        from torchvision import transforms
        
        resolution = params.get("resolution", 1024)
        image_paths = params.get("ref_image_paths", [])
        
        if not image_paths:
            return {"ok": False, "error": "no ref_image_paths provided"}
            
        images = []
        for path in image_paths:
            img = Image.open(path).convert("RGB")
            img = img.resize((resolution, resolution), Image.Resampling.LANCZOS)
            img_tensor = transforms.ToTensor()(img)
            img_tensor = transforms.Normalize([0.5], [0.5])(img_tensor)
            images.append(img_tensor)
            
        images = _torch.stack(images).to("cuda", dtype=_torch.bfloat16)
        
        instance_prompt = params.get("instance_prompt", "a photo")
        
        from diffusers.loaders.lora_pipeline import _encode_prompt_sdxl
        prompt_embeds, pooled_prompt_embeds = _encode_prompt_sdxl(
            _pipeline,
            prompt=instance_prompt,
            device=_torch.device("cuda"),
            num_images_per_prompt=1,
            do_classifier_free_guidance=False
        )
        
        optimizer = _torch.optim.AdamW(
            filter(lambda p: p.requires_grad, unet.parameters()),
            lr=params.get("learning_rate", 1.0e-4)
        )
        
        steps = params.get("steps", 400)
        
        from accelerate import Accelerator
        accelerator = Accelerator(gradient_accumulation_steps=2, mixed_precision="bf16")
        
        unet, optimizer, images = accelerator.prepare(unet, optimizer, images)
        
        unet.train()
        for step in range(steps):
            with accelerator.accumulate(unet):
                idx = step % len(images)
                batch_img = images[idx:idx+1]
                
                latents = vae.encode(batch_img).latent_dist.sample()
                latents = latents * vae.config.scaling_factor
                
                noise = _torch.randn_like(latents)
                bsz = latents.shape[0]
                timesteps = _torch.randint(0, _pipeline.scheduler.config.num_train_timesteps, (bsz,), device=latents.device)
                timesteps = timesteps.long()
                
                noisy_latents = _pipeline.scheduler.add_noise(latents, noise, timesteps)
                
                add_time_ids = _pipeline._get_add_time_ids((resolution, resolution), (0,0), (resolution, resolution), dtype=prompt_embeds.dtype, text_encoder_projection_dim=_pipeline.text_encoder_2.config.projection_dim).to("cuda")
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
        state_dict = get_peft_model_state_dict(unet)
        save_file(state_dict, output_path)
        
        _eprint(f"[run_trainer] saved lora to {output_path}")
        
    except _torch.cuda.OutOfMemoryError as e:
        return {"ok": False, "error": f"OOM during training: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"training failed: {e}\n{traceback.format_exc()}"}
        
    return {"ok": True, "lora_path": output_path, "lora_version": 1}

def _do_unload(cmd):
    global _pipeline
    if _pipeline is not None:
        del _pipeline
        _pipeline = None
    if _torch is not None:
        _torch.cuda.empty_cache()
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
