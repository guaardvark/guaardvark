"""Z-Image Turbo character LoRA trainer subprocess.

Runs with a Python that has diffusers>=0.38 + ZImagePipeline (backend/venv),
NOT the older SDXL-only venv-torch.

Protocol: same JSON-lines stdin/stdout as run_trainer.py (RealLoraTrainer).

Training notes / anticipated fails handled here:
  - 16GB VRAM: Z-Image weights are ~12G transformer + ~7.5G Qwen3 TE + VAE —
    full pipeline.to("cuda") cannot fit. Load stays on CPU; train stages one
    heavy module at a time (VAE encode → TE encode → transformer+LoRA).
  - On ≤16GB cards, train resolution soft-caps at 512 (768 peaks ~13GB alone and
    OOMs by step ~7 once desktop/other CUDA processes take ~1GB + allocator holes).
  - PEFT defaults LoRA adapters to fp32; we cast trainable weights to bf16 so
    grads/Adam states stay smaller. unwrap PeftModel before every train (and on
    OOM) so a failed run cannot double-wrap on retry.
  - PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True reduces fragmentation OOMs.
  - Turbo distillation: training on Turbo is imperfect; we use moderate steps/rank
    and document adapter path via ZIMAGE_TURBO_TRAIN_ADAPTER if present later.
  - CUDA_VISIBLE_DEVICES='' poison: parent RealLoraTrainer forces a real device.
  - Save format: pipeline.save_lora_weights (diffusers/peft) so
    ZImagePipeline.load_lora_weights works at inference.
  - PEFT state keys include ``base_model.model.*``; strip that wrapper before
    save or Diffusers looks for modules that do not exist on the bare transformer.
"""

from __future__ import annotations

import gc
import json
import os
import sys
import traceback
from pathlib import Path

# Must be set before the first CUDA context is created in this process.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

_pipeline = None
_torch = None
_model_id = None


def _dev() -> str:
    """Accelerator device for training: CUDA > MPS (Apple Silicon) > CPU.

    Apple Silicon has no CUDA; torch.backends.mps is the Metal backend. The
    trainer stages one heavy module at a time, so the device is resolved lazily
    per call (after _torch is set in _do_load).
    """
    if _torch is None:
        return "cpu"
    if _torch.cuda.is_available():
        return "cuda"
    if hasattr(_torch.backends, "mps") and _torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _is_bf16_supported() -> bool:
    if _torch is None:
        return False
    if _torch.cuda.is_available():
        return _torch.cuda.is_bf16_supported()
    # MPS supports bfloat16 on Apple Silicon (M-series).
    return _dev() == "mps"


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _retag_peft_transformer_lora(state_dict: dict) -> dict:
    """Strip PEFT ``base_model.model.`` so save_lora_weights emits Diffusers keys.

    Raw PEFT dump: ``base_model.model.layers.0.attention.to_q.lora_A.weight``
    After retag:   ``layers.0.attention.to_q.lora_A.weight``
    save_lora_weights then prefixes ``transformer.`` → loadable by ZImagePipeline.
    """
    out = {}
    for key, value in state_dict.items():
        nk = key
        if nk.startswith("base_model.model."):
            nk = nk[len("base_model.model.") :]
        elif nk.startswith("transformer.base_model.model."):
            nk = nk[len("transformer.base_model.model.") :]
        elif ".base_model.model." in nk:
            nk = nk.replace(".base_model.model.", ".", 1)
        out[nk] = value
    return out


def _cuda_reclaim() -> None:
    """Best-effort host + accelerator allocator reclaim between stages / after OOM."""
    gc.collect()
    if _torch is None:
        return
    try:
        if _torch.cuda.is_available():
            _torch.cuda.empty_cache()
            _torch.cuda.ipc_collect()
        elif _dev() == "mps":
            _torch.mps.empty_cache()
    except Exception:
        pass


def _unwrap_peft_transformer() -> None:
    """Restore bare transformer after a failed/prior train (prevents double-wrap)."""
    global _pipeline
    if _pipeline is None:
        return
    try:
        from peft import PeftModel

        tr = getattr(_pipeline, "transformer", None)
        if isinstance(tr, PeftModel):
            try:
                if hasattr(tr, "unload"):
                    tr.unload()
            except Exception:
                pass
            try:
                _pipeline.transformer = tr.get_base_model()
            except Exception:
                try:
                    _pipeline.transformer = tr.base_model.model
                except Exception:
                    pass
            _eprint("[run_zimage_trainer] unwrapped leftover PeftModel from prior train")
    except Exception as e:
        _eprint(f"[run_zimage_trainer] peft unwrap best-effort failed: {e}")


def _move_pipeline_to_cpu() -> None:
    if _pipeline is None:
        return
    for name in ("vae", "text_encoder", "transformer"):
        mod = getattr(_pipeline, name, None)
        if mod is None:
            continue
        try:
            mod.to("cpu")
        except Exception:
            pass
    _cuda_reclaim()


def _gpu_total_gb() -> float:
    if _torch is None:
        return 0.0
    try:
        if _torch.cuda.is_available():
            return float(_torch.cuda.get_device_properties(0).total_memory) / (1024**3)
        if _dev() == "mps":
            # Apple Silicon unified memory: report the recommended max working set
            # (defaults to ~2/3 of physical RAM) as the "VRAM" budget.
            return float(_torch.mps.recommended_max_memory()) / (1024**3)
    except Exception:
        pass
    return 0.0


def _resolve_model_path(model_id: str) -> str:
    """Prefer local snapshot under data/models/stable_diffusion/."""
    mid = model_id or "Tongyi-MAI/Z-Image-Turbo"
    # Catalog layout: Tongyi-MAI--Z-Image-Turbo
    local_name = mid.replace("/", "--")
    roots = [
        Path(os.environ.get("GUAARDVARK_ROOT", ".")),
        Path(__file__).resolve().parents[3],  # repo root from plugins/lora_trainer/scripts
    ]
    for root in roots:
        cand = root / "data" / "models" / "stable_diffusion" / local_name
        if cand.is_dir() and (cand / "model_index.json").is_file():
            return str(cand)
    return mid


def _do_load(cmd: dict) -> dict:
    global _pipeline, _torch, _model_id
    model_id = cmd.get("model_id") or "Tongyi-MAI/Z-Image-Turbo"
    if _pipeline is not None and _model_id == model_id:
        return {"ok": True}

    _eprint(f"[run_zimage_trainer] loading {model_id}...")
    try:
        import torch
        from diffusers import ZImagePipeline
    except Exception as e:
        return {
            "ok": False,
            "error": (
                f"Z-Image stack unavailable ({e}). Need diffusers>=0.38 with "
                f"ZImagePipeline (use backend/venv, not venv-torch)."
            ),
        }

    _torch = torch
    if not torch.cuda.is_available() and not (
        hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
    ):
        return {
            "ok": False,
            "error": "No accelerator available — Z-Image LoRA training requires CUDA or Apple MPS",
        }

    path = _resolve_model_path(model_id)
    dtype = torch.bfloat16 if _is_bf16_supported() else torch.float16
    try:
        # Stay on CPU at load — full Z-Image (~20GB weights) cannot fit a 16GB card.
        # Train stages VAE → TE → transformer onto CUDA one at a time.
        _pipeline = ZImagePipeline.from_pretrained(
            path,
            torch_dtype=dtype,
            local_files_only=Path(path).is_dir(),
        )
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        _model_id = model_id
        _eprint(f"[run_zimage_trainer] loaded on CPU from {path} dtype={dtype}")
    except torch.cuda.OutOfMemoryError as e:
        _pipeline = None
        _model_id = None
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        return {
            "ok": False,
            "error": (
                "OOM loading Z-Image (unexpected at CPU load). "
                f"Full pipeline does not fit 16GB; staging failed at load: {e}"
            ),
        }
    except Exception as e:
        _pipeline = None
        _model_id = None
        return {"ok": False, "error": f"failed to load Z-Image: {e}\n{traceback.format_exc()}"}

    # Optional turbo training adapter (Ostris) if operator placed it on disk
    adapter = os.environ.get("ZIMAGE_TURBO_TRAIN_ADAPTER", "").strip()
    if adapter and Path(adapter).exists():
        try:
            _pipeline.load_lora_weights(adapter, adapter_name="turbo_train_adapter")
            _eprint(f"[run_zimage_trainer] loaded turbo train adapter: {adapter}")
        except Exception as e:
            _eprint(f"[run_zimage_trainer] turbo adapter load failed (continuing): {e}")

    return {"ok": True}


def _do_train(cmd: dict) -> dict:
    params = cmd.get("params") or {}
    if _pipeline is None or _torch is None:
        return {"ok": False, "error": "model not loaded — call op=load first"}

    try:
        from peft import LoraConfig, get_peft_model, get_peft_model_state_dict
        from PIL import Image, ImageOps
        from torchvision import transforms
        import torch.nn.functional as F
        from safetensors.torch import save_file
    except Exception as e:
        return {"ok": False, "error": f"train deps missing: {e}"}

    image_paths = params.get("ref_image_paths") or []
    if not image_paths:
        return {"ok": False, "error": "no ref_image_paths provided"}

    resolution = max(512, (int(params.get("resolution") or 512) // 64) * 64)
    # Soft-cap: on ≤16.5GB cards 768 train_loop peaks ~13GB for the trainer alone;
    # with desktop/other CUDA (~0.7–1GB) + allocator holes it OOMs around step 7–8.
    # 512 leaves ~1GB headroom on the 16GB dev card (measured).
    gpu_gb = _gpu_total_gb()
    max_res = 512 if gpu_gb and gpu_gb <= 16.5 else 768
    if resolution > max_res:
        _eprint(
            f"[run_zimage_trainer] clamping resolution {resolution} -> {max_res} "
            f"(gpu≈{gpu_gb:.1f}GB)"
        )
        resolution = max_res

    rank = max(4, min(64, int(params.get("rank") or 16)))
    alpha = max(4, min(128, int(params.get("alpha") or rank)))
    lr = float(params.get("learning_rate") or 1e-4)
    steps = int(params.get("steps") or max(400, min(1200, len(image_paths) * 80)))
    output_path = params.get("output_path")
    if not output_path:
        return {"ok": False, "error": "output_path required"}

    instance_prompt = params.get("instance_prompt") or "a photo"
    image_prompts = list(params.get("image_prompts") or [])
    if not image_prompts:
        image_prompts = [instance_prompt] * len(image_paths)
    while len(image_prompts) < len(image_paths):
        image_prompts.append(image_prompts[-1] if image_prompts else instance_prompt)

    stage = "init"
    step = -1
    dev = _dev()
    try:
        # Prior failed train can leave a PeftModel + accelerator weights; unwrap first.
        _unwrap_peft_transformer()
        _move_pipeline_to_cpu()

        # ── freeze + LoRA on transformer while still on CPU (cheap) ─────────
        vae = _pipeline.vae
        text_encoder = _pipeline.text_encoder
        transformer = _pipeline.transformer
        vae.requires_grad_(False)
        text_encoder.requires_grad_(False)
        transformer.requires_grad_(False)

        if hasattr(transformer, "enable_gradient_checkpointing"):
            transformer.enable_gradient_checkpointing()

        lora_config = LoraConfig(
            r=rank,
            lora_alpha=alpha,
            target_modules=["to_q", "to_k", "to_v", "to_out.0"],
            init_lora_weights="gaussian",
        )
        transformer = get_peft_model(transformer, lora_config)
        # PEFT initializes adapters in fp32; cast trainable to bf16 so they match
        # the base transformer and cut grad / optimizer footprint on 16GB.
        train_dtype = _torch.bfloat16 if _is_bf16_supported() else _torch.float16
        for _n, _p in transformer.named_parameters():
            if _p.requires_grad and _p.is_floating_point():
                _p.data = _p.data.to(train_dtype)
        if hasattr(transformer, "enable_gradient_checkpointing"):
            transformer.enable_gradient_checkpointing()
        _pipeline.transformer = transformer

        # ── stage VAE: encode latents, then free VRAM ───────────────────────
        stage = "VAE"
        _eprint(f"[run_zimage_trainer] staging VAE on {dev} for latent cache...")
        vae.to(dev)
        tensors = []
        for path in image_paths:
            img = Image.open(path).convert("RGB")
            img = ImageOps.fit(img, (resolution, resolution), method=Image.Resampling.LANCZOS)
            t = transforms.ToTensor()(img)
            t = transforms.Normalize([0.5], [0.5])(t)
            tensors.append(t)
        images = _torch.stack(tensors).to(dev, dtype=vae.dtype)

        all_latents = []
        with _torch.no_grad():
            for i in range(images.shape[0]):
                lat = vae.encode(images[i : i + 1]).latent_dist.sample()
                # Z-Image VAE uses scaling_factor + shift_factor at decode; encode inverse:
                # latents_for_model = (lat - shift) * scaling  — match pipeline encode habit
                sf = getattr(vae.config, "scaling_factor", 1.0)
                shift = getattr(vae.config, "shift_factor", 0.0)
                lat = (lat - shift) * sf
                all_latents.append(lat.detach().to("cpu"))
        del images, tensors
        vae.to("cpu")
        _cuda_reclaim()
        _eprint(f"[run_zimage_trainer] cached {len(all_latents)} latents @ {resolution}; VAE off GPU")

        # ── stage TE: encode prompts, then free VRAM ────────────────────────
        stage = "TE"
        _eprint(f"[run_zimage_trainer] staging text encoder on {dev} for prompt cache...")
        text_encoder.to(dev)
        all_prompt_embeds = []
        with _torch.no_grad():
            for prompt in image_prompts[: len(image_paths)]:
                # ZImagePipeline.encode_prompt has no num_images_per_prompt
                # (unlike SDXL/FLUX); signature is prompt/device/CFG/neg/embeds.
                pe, _neg = _pipeline.encode_prompt(
                    prompt=prompt,
                    device=_torch.device(dev),
                    do_classifier_free_guidance=False,
                )
                # Park embeds on CPU until transformer stage
                if isinstance(pe, list):
                    all_prompt_embeds.append(
                        [t.detach().cpu() if hasattr(t, "cpu") else t for t in pe]
                    )
                elif hasattr(pe, "cpu"):
                    all_prompt_embeds.append(pe.detach().cpu())
                else:
                    all_prompt_embeds.append(pe)
        text_encoder.to("cpu")
        _cuda_reclaim()
        _eprint(f"[run_zimage_trainer] cached {len(all_prompt_embeds)} prompts; TE off GPU")

        # ── stage transformer: train on accelerator with cached latents/embeds ─
        stage = "transformer"
        if dev == "cuda":
            free_b, total_b = _torch.cuda.mem_get_info()
            _eprint(
                f"[run_zimage_trainer] staging transformer+LoRA on CUDA "
                f"(free={free_b/1024**3:.2f}G / total={total_b/1024**3:.2f}G, "
                f"res={resolution}, rank={rank}, dtype={train_dtype})..."
            )
        else:
            _eprint(
                f"[run_zimage_trainer] staging transformer+LoRA on {dev} "
                f"(res={resolution}, rank={rank}, dtype={train_dtype})..."
            )
        _cuda_reclaim()
        transformer.to(dev)

        # Keep latents and prompt embeds on CPU — move only the current sample
        # per step. On 16GB the transformer + LoRA grads + AdamW states already
        # consume most of the card.
        n_samples = len(all_latents)

        def _embeds_to_dev(pe):
            if isinstance(pe, list):
                return [t.to(dev) if hasattr(t, "to") else t for t in pe]
            if hasattr(pe, "to"):
                return pe.to(dev)
            return pe

        def _embeds_del(pe):
            """Delete accelerator copies of prompt embeds to free memory."""
            if isinstance(pe, list):
                for t in pe:
                    del t
            else:
                del pe

        # ── optim ───────────────────────────────────────────────────────────
        trainable = [p for p in transformer.parameters() if p.requires_grad]
        opt = _torch.optim.AdamW(
            trainable,
            lr=lr,
            weight_decay=0.01,
        )
        transformer.train()

        stage = "train_loop"
        for step in range(steps):
            idx = step % n_samples
            # Move only this sample's latent to the accelerator (tiny: single image
            # latent). Each element already has shape [1, C, H, W] from vae.encode.
            x0 = all_latents[idx].to(dev, dtype=train_dtype)
            noise = _torch.randn_like(x0)

            # Continuous flow-matching time in [0, 1]
            u = _torch.rand((1,), device=dev, dtype=train_dtype).clamp(0.02, 0.98)
            # xt = (1-u)*x0 + u*noise ; velocity target = noise - x0
            u_b = u.view(-1, 1, 1, 1)
            xt = (1.0 - u_b) * x0 + u_b * noise
            target = noise - x0

            # Model expects timestep like inference: normalized; pipeline uses (1000-t)/1000
            # For continuous u we pass u as the normalized "how much noise"
            timestep = u.to(dtype=_torch.float32)

            latent_in = xt.unsqueeze(2)  # match pipeline unsqueeze
            latent_list = list(latent_in.unbind(dim=0))
            # Move only this sample's prompt embed to the accelerator
            pe_dev = _embeds_to_dev(all_prompt_embeds[idx])
            if not isinstance(pe_dev, list):
                pe_list = [pe_dev]
            else:
                pe_list = pe_dev

            model_out = transformer(
                latent_list,
                timestep,
                pe_list,
                return_dict=False,
            )[0]
            # Stay in train_dtype (bf16) — avoid float32 upcast that doubles peak memory
            pred = _torch.stack(model_out, dim=0).squeeze(2)
            # Pipeline flips sign at inference (noise_pred = -model_out); train consistently
            pred = -pred

            loss = F.mse_loss(pred, target)
            loss.backward()
            _torch.nn.utils.clip_grad_norm_(trainable, 1.0)
            opt.step()
            opt.zero_grad(set_to_none=True)

            # Free per-step accelerator tensors explicitly
            _embeds_del(pe_dev)
            del x0, noise, u, u_b, xt, target, timestep, latent_in, latent_list
            del pe_dev, pe_list, model_out, pred

            if step % 50 == 0 or step == steps - 1:
                _eprint(f"[run_zimage_trainer] step {step+1}/{steps} loss={loss.item():.5f}")
            del loss

            # Allocator hygiene — without this, reserved-but-free holes OOM ~step 7 on 16GB.
            if (step + 1) % 25 == 0:
                _cuda_reclaim()

        # ── save diffusers-compatible LoRA ──────────────────────────────────
        stage = "save"
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        peft_state = _retag_peft_transformer_lora(get_peft_model_state_dict(transformer))
        # Prefer pipeline helper if available
        try:
            # save next to file as weights; also write single safetensors
            tmp_dir = out.parent / f".{out.stem}_lora_tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            _pipeline.save_lora_weights(
                str(tmp_dir),
                transformer_lora_layers=peft_state,
            )
            # Flatten to single file path expected by cast (*.safetensors)
            candidates = list(tmp_dir.glob("*.safetensors"))
            if candidates:
                # move first weights file to output_path
                data = candidates[0].read_bytes()
                out.write_bytes(data)
            else:
                # manual dump with transformer. prefix (same as save_lora_weights)
                state = {
                    (k if k.startswith("transformer.") else f"transformer.{k}"): (
                        v.to(_torch.bfloat16) if v.is_floating_point() else v
                    )
                    for k, v in peft_state.items()
                }
                save_file(state, str(out))
            # cleanup tmp
            for f in tmp_dir.iterdir():
                try:
                    f.unlink()
                except Exception:
                    pass
            try:
                tmp_dir.rmdir()
            except Exception:
                pass
        except Exception as e:
            _eprint(f"[run_zimage_trainer] save_lora_weights path failed ({e}); peft dump")
            state = {
                (k if k.startswith("transformer.") else f"transformer.{k}"): (
                    v.to(_torch.bfloat16) if v.is_floating_point() else v
                )
                for k, v in peft_state.items()
            }
            save_file(state, str(out))

        # detach peft so next train starts clean
        _unwrap_peft_transformer()
        _move_pipeline_to_cpu()

        _eprint(f"[run_zimage_trainer] saved LoRA → {out} ({out.stat().st_size} bytes)")
        return {
            "ok": True,
            "lora_path": str(out),
            "lora_version": 1,
            "base_model_id": "zimage-turbo",
            "steps": steps,
            "resolution": resolution,
        }
    except _torch.cuda.OutOfMemoryError as e:
        cur = step + 1 if step >= 0 else "n/a"
        try:
            _unwrap_peft_transformer()
            _move_pipeline_to_cpu()
        except Exception:
            pass
        return {
            "ok": False,
            "error": (
                f"OOM during Z-Image training (stage={stage}, step {cur}/{steps}). "
                f"Full pipeline does not fit 16GB; staging failed at {stage}: {e}"
            ),
        }
    except Exception as e:
        cur = step + 1 if step >= 0 else "n/a"
        try:
            _unwrap_peft_transformer()
            _move_pipeline_to_cpu()
        except Exception:
            pass
        # MPS raises a plain RuntimeError (not torch.cuda.OutOfMemoryError) when
        # unified memory is exhausted; surface it as an OOM-style failure.
        if "out of memory" in str(e).lower():
            return {
                "ok": False,
                "error": (
                    f"OOM during Z-Image training (stage={stage}, step {cur}/{steps}): {e}"
                ),
            }
        return {
            "ok": False,
            "error": (
                f"Z-Image training failed at stage={stage}, step {cur}: {e}\n"
                f"{traceback.format_exc()}"
            ),
        }


def _do_unload(cmd: dict) -> dict:
    global _pipeline, _model_id
    if _pipeline is not None:
        del _pipeline
        _pipeline = None
        _model_id = None
    if _torch is not None:
        _cuda_reclaim()
    return {"ok": True}


def _do_shutdown(cmd: dict) -> dict:
    _do_unload(cmd)
    return {"ok": True}


OPS = {
    "ping": lambda cmd: {"ok": True, "ready": _pipeline is not None, "backend": "zimage"},
    "load": _do_load,
    "train": _do_train,
    "unload": _do_unload,
    "shutdown": _do_shutdown,
}


def main() -> None:
    _eprint("[run_zimage_trainer] daemon ready, waiting on stdin...")
    for line in sys.stdin:
        try:
            cmd = json.loads(line)
            op = cmd.get("op")
            handler = OPS.get(op)
            if handler is None:
                _respond({"ok": False, "error": f"unknown op: {op}"})
                continue
            _respond(handler(cmd))
            if op == "shutdown":
                return
        except Exception as e:
            _respond({"ok": False, "error": f"daemon crash: {e}\n{traceback.format_exc()}"})


if __name__ == "__main__":
    main()
