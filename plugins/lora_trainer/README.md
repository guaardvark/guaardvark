# LoRA Trainer Plugin

Trains character, environment, and prop LoRAs for the Film Crew / Cast Studio.

## Media model registry (product direction)

Character identity is **not** forever-SDXL. Defaults live in
`backend/services/media_model_registry.py`:

| Profile | Role | Train ready (today) |
|---------|------|---------------------|
| **zimage-turbo** | Product default stills + LoRA base | **Yes** (`run_zimage_trainer.py` + backend/venv) |
| **flux-dev** | Max-quality stills / character | No — recipe next |
| **sdxl-legacy** | Legacy path for old LoRAs | **Yes** (`run_trainer.py` + venv-torch) |

Every trained `.safetensors` must ship a schema-v2 sidecar JSON with
`base_model_id` + `lora_format` so inference never force-routes every LoRA to SDXL.

## Backends

The plugin selects between:

- **real Z-Image** (default) — `scripts/run_zimage_trainer.py` via **backend/venv** (needs `ZImagePipeline`). Flow-matching PEFT on the transformer; Kohya/diffusers-compatible save for `load_lora_weights`.
- **real SDXL (legacy)** — `scripts/run_trainer.py` + `venv-torch/` PEFT UNet.
- **mock** (pytest only) — refused outside pytest (NO-MOCKS policy).

Optional: set `ZIMAGE_TURBO_TRAIN_ADAPTER=/path/to/ostris_adapter.safetensors` to load a turbo train adapter before PEFT.

Selection priority:
  1. `GUAARDVARK_LORA_BACKEND=real|auto|mock` env var (default: `auto`)
  2. `auto`: use **real** if a train Python sees an accelerator (CUDA **or Apple MPS**); otherwise **fail** with an error (no silent mock fallback)
  3. `mock`: allowed only when `PYTEST_CURRENT_TEST` is set (unit/integration tests)

### Setting up real training

  $ cd plugins/lora_trainer
  $ ./scripts/setup_venv.sh

This creates `venv-torch/`, installs torch+diffusers+peft (~7 GB once + ~5 GB cache for the SDXL base on first run), and verifies CUDA. Once it succeeds, the next training dispatch picks the real backend automatically.

### Apple Silicon (MPS) training

The default **Z-Image** backend runs in `backend/venv` and now supports Apple
Silicon via the MPS backend (no CUDA required). The trainer scripts resolve the
accelerator as CUDA > MPS > CPU, and `RealLoraTrainer.is_available()` accepts MPS
as a valid accelerator. Requirements:

  - `backend/venv` must have `torch` (MPS build), `diffusers>=0.38` with
    `ZImagePipeline`, and `peft` installed:
    `backend/venv/bin/pip install peft`
  - `PYTORCH_ENABLE_MPS_FALLBACK=1` is set automatically by the trainer driver so
    unsupported MPS ops degrade to CPU instead of hard-failing.

The SDXL-legacy backend still requires `venv-torch/` (CUDA wheels); on Apple
Silicon use the Z-Image default instead.

### When real training is unavailable

If `auto` cannot reach the real trainer (missing venv, GPU busy, accelerator probe failed), the Celery task returns `status: failed` with guidance — it does **not** write a fake LoRA. Check `nvidia-smi` (or MPS availability on Mac), run `setup_venv.sh`, or set `GUAARDVARK_LORA_BACKEND=real` to bypass the availability probe.

### Mock backend (tests only)

Set `GUAARDVARK_LORA_BACKEND=mock` only under pytest. In production that value is rejected with an explicit error.

### Hyperparameters

  - Base model: `stabilityai/stable-diffusion-xl-base-1.0`
  - LoRA rank/alpha: 16/16; target_modules = to_q, to_k, to_v, to_out.0
  - Steps: `min(1500, max(400, num_refs * 100))`
  - LR 1e-4, bf16, batch=1 with gradient_accumulation_steps=2
  - Resolution 1024 (resize all refs)
