# LoRA Trainer Plugin

Trains character, environment, and prop LoRAs for the Film Crew.

## Backends

The plugin auto-selects between two backends:

- **mock** (always available) — sleeps ~1s, writes a stub safetensors file, returns success. Useful for testing the casting flow without a GPU.
- **real** (requires setup) — runs SDXL LoRA training in an isolated `venv-torch/` via subprocess. ~10-15 min per subject on a 24 GB GPU.

Selection priority:
  1. `GUAARDVARK_LORA_BACKEND=mock|real|auto` env var (default: `auto`)
  2. Auto: `real` if `venv-torch/bin/python` exists, else `mock`

### Setting up real training

  $ cd plugins/lora_trainer
  $ ./scripts/setup_venv.sh

This creates `venv-torch/`, installs torch+diffusers+peft (~7 GB once + ~5 GB cache for the SDXL base on first run), and verifies CUDA. Once it succeeds, the next training dispatch picks the real backend automatically.

### Reverting to mock

  $ rm -rf plugins/lora_trainer/venv-torch

Or set `GUAARDVARK_LORA_BACKEND=mock` in the environment.

### Hyperparameters

  - Base model: `stabilityai/stable-diffusion-xl-base-1.0`
  - LoRA rank/alpha: 16/16; target_modules = to_q, to_k, to_v, to_out.0
  - Steps: `min(1500, max(400, num_refs * 100))`
  - LR 1e-4, bf16, batch=1 with gradient_accumulation_steps=2
  - Resolution 1024 (resize all refs)
