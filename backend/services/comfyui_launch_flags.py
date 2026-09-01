# backend/services/comfyui_launch_flags.py
"""CLI flags Guaardvark always passes when it starts ComfyUI.

Keep `plugins/comfyui/scripts/start.sh` in lockstep: that script cannot import
this module, so it repeats the same env names and defaults. Tests assert both.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional, Sequence

# Nothing leaves the machine during generation. ComfyUI itself never downloads
# weights, but custom nodes do (2026-08-28: the CogVideoX wrapper pulled an
# 11GB snapshot from Hugging Face mid-render), so the process is started with
# the Hub client in offline mode and its API nodes disabled. Downloads happen
# in the backend, behind the Install button in Manage Video Models, where the
# person can see them. `--disable-api-nodes` also stops the ComfyUI frontend
# talking to the internet (comfy/cli_args.py).
LOCAL_ONLY_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
}
LOCAL_ONLY_CLI_ARGS = ("--disable-api-nodes",)

PREVIEW_METHOD_ENV = "GUAARDVARK_COMFYUI_PREVIEW_METHOD"
PREVIEW_SIZE_ENV = "GUAARDVARK_COMFYUI_PREVIEW_SIZE"
PREVIEW_METHOD_DEFAULT = "auto"
PREVIEW_SIZE_DEFAULT = 256
PREVIEW_METHODS = frozenset({"none", "auto", "latent2rgb", "taesd"})
PREVIEW_SIZE_MIN = 64
PREVIEW_SIZE_MAX = 1024


def preview_cli_args(env: Optional[Mapping[str, str]] = None) -> Sequence[str]:
    """Return `--preview-method` / `--preview-size` for a ComfyUI argv.

    ComfyUI's own default is `none`, which means API clients never receive
    sampler thumbnails. `auto` selects Latent2RGB (no extra weights). `none`
    remains the operator rollback.
    """
    src = env if env is not None else os.environ
    method = (src.get(PREVIEW_METHOD_ENV) or PREVIEW_METHOD_DEFAULT).strip().lower()
    if method not in PREVIEW_METHODS:
        method = PREVIEW_METHOD_DEFAULT
    args = ["--preview-method", method]
    if method == "none":
        return args
    try:
        size = int(src.get(PREVIEW_SIZE_ENV) or PREVIEW_SIZE_DEFAULT)
    except (TypeError, ValueError):
        size = PREVIEW_SIZE_DEFAULT
    size = max(PREVIEW_SIZE_MIN, min(PREVIEW_SIZE_MAX, size))
    args.extend(["--preview-size", str(size)])
    return args


# Attention backend. ComfyUI's default is PyTorch SDPA. `ck` routes attention
# through comfy_kitchen's int8 kernels (`--use-ck-attention`, ComfyUI ≥ 0.33),
# which ships in the backend venv already; `sage` needs the separately
# installed sageattention package (`--use-sage-attention`) and is never
# auto-installed. The flag is process-wide: it changes every ComfyUI-routed
# family, not only the one being tuned, which is why the default stays
# `pytorch` until every family has been compared. Measured 2026-09-01 for
# MiniMax H3 on a 16 GB RTX 40-series card (864x480, 124 frames, 20 steps,
# same seed): ck 339 s at 15.0 s/step against PyTorch 390 s at 17.0 s/step,
# same VRAM peak, frames indistinguishable. Wan, LTX and Hunyuan are not yet
# compared, so `auto` is a documented opt-in rather than the default. `auto`
# prefers ck, then sage, then pytorch, by availability.
ATTENTION_ENV = "GUAARDVARK_COMFYUI_ATTENTION"
ATTENTION_DEFAULT = "pytorch"
ATTENTION_BACKENDS = frozenset({"auto", "ck", "sage", "pytorch"})
ATTENTION_CLI_FLAG = {"ck": "--use-ck-attention", "sage": "--use-sage-attention"}


def attention_cli_args(
    env: Optional[Mapping[str, str]] = None,
    *,
    ck_available: bool = False,
    sage_available: bool = False,
) -> Sequence[str]:
    """Return the attention flag for a ComfyUI argv, or nothing for PyTorch.

    A backend that is requested but not importable falls back to nothing
    (ComfyUI would refuse to start otherwise); the launcher logs the choice.
    """
    src = env if env is not None else os.environ
    choice = (src.get(ATTENTION_ENV) or ATTENTION_DEFAULT).strip().lower()
    if choice not in ATTENTION_BACKENDS:
        choice = ATTENTION_DEFAULT
    available = {"ck": ck_available, "sage": sage_available}
    if choice == "auto":
        choice = next((b for b in ("ck", "sage") if available[b]), "pytorch")
    if choice in ATTENTION_CLI_FLAG and available[choice]:
        return [ATTENTION_CLI_FLAG[choice]]
    return []


# VRAM ComfyUI leaves untouched. 1.0 GB keeps the desktop compositor alive on
# a maxed 16 GB card; a larger value makes the partial loader offload more
# weights so a model whose activations outgrow ComfyUI's estimate (MiniMax H3
# int8 on 16 GB) finishes a step instead of running out mid-kernel.
RESERVE_VRAM_ENV = "GUAARDVARK_COMFYUI_RESERVE_VRAM"
RESERVE_VRAM_DEFAULT = 1.0


def reserve_vram_cli_args(env: Optional[Mapping[str, str]] = None) -> Sequence[str]:
    """Return `--reserve-vram <gb>` for a ComfyUI argv; bad values fall back."""
    src = env if env is not None else os.environ
    raw = (src.get(RESERVE_VRAM_ENV) or "").strip()
    try:
        value = float(raw) if raw else RESERVE_VRAM_DEFAULT
    except ValueError:
        value = RESERVE_VRAM_DEFAULT
    if value < 0:
        value = RESERVE_VRAM_DEFAULT
    return ["--reserve-vram", f"{value:g}"]
