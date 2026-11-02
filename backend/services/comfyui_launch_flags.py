# backend/services/comfyui_launch_flags.py
"""CLI flags Guaardvark always passes when it starts ComfyUI.

Keep `plugins/comfyui/scripts/start.sh` in lockstep: that script cannot import
this module, so it repeats the same env names and defaults. Tests assert both.
"""

from __future__ import annotations

import os
from pathlib import Path
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
    "ORT_DISABLE_TELEMETRY": "1",
}
LOCAL_ONLY_CLI_ARGS = ("--disable-api-nodes",)

# Every launch setting the plugin's start.sh reads is a GUAARDVARK_COMFYUI_*
# key. They are re-read from the checkout's .env at every plugin start rather
# than inherited from the backend's environment (frozen at backend start), so
# editing .env and restarting the plugin is enough; before this, a reserve or
# attention change still launched the old value until the backend restarted.
LAUNCH_ENV_PREFIX = "GUAARDVARK_COMFYUI_"


def dotenv_launch_overrides(dotenv_path, prefix: str = LAUNCH_ENV_PREFIX) -> dict:
    """``KEY=VALUE`` lines of a .env file whose key starts with ``prefix``.

    Accepts an optional ``export`` prefix and single or double quotes around
    the value; comments and blank lines are skipped; ``$VAR`` references are
    not expanded. An empty value is kept (it means "unset" to the launcher).
    A missing or unreadable file yields nothing.
    """
    found: dict = {}
    try:
        text = Path(dotenv_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith(prefix) or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        found[key] = value
    return found


def shell_exports(mapping: Mapping[str, str]) -> str:
    """``export KEY=value`` lines, quoted for ``eval`` in bash."""
    import shlex
    return "\n".join(f"export {key}={shlex.quote(str(value))}" for key, value in mapping.items())


def launch_env(project_root, base: Optional[Mapping[str, str]] = None) -> dict:
    """The environment a ComfyUI launch sees: ``base`` (the process env by
    default) with the checkout's .env GUAARDVARK_COMFYUI_* keys on top."""
    merged = dict(base if base is not None else os.environ)
    merged.update(dotenv_launch_overrides(Path(project_root) / ".env"))
    return merged

PREVIEW_METHOD_ENV = "GUAARDVARK_COMFYUI_PREVIEW_METHOD"
PREVIEW_SIZE_ENV = "GUAARDVARK_COMFYUI_PREVIEW_SIZE"
PREVIEW_METHOD_DEFAULT = "auto"
PREVIEW_SIZE_DEFAULT = 256
PREVIEW_METHODS = frozenset({"none", "auto", "latent2rgb", "taesd"})
PREVIEW_SIZE_MIN = 64
PREVIEW_SIZE_MAX = 1024


MODEL_PATHS_ENV = "GUAARDVARK_TRAINING_DIR"


def model_paths_launch(project_root) -> tuple[list, dict]:
    """argv and env that add Cast Library LoRAs to ComfyUI's LoRA search.

    ComfyUI resolves LoRAs by filename within its search paths, and Cast LoRAs
    live in STORAGE_DIR/training/loras. plugins/comfyui/guaardvark_model_paths.yaml
    names that folder through GUAARDVARK_TRAINING_DIR. Mirrors
    plugins/comfyui/scripts/start.sh; empty when the YAML is absent, since ComfyUI
    refuses to start on a missing config file.
    """
    cfg = Path(project_root) / "plugins" / "comfyui" / "guaardvark_model_paths.yaml"
    if not cfg.is_file():
        return [], {}
    from backend.config import STORAGE_DIR
    return (["--extra-model-paths-config", str(cfg)],
            {MODEL_PATHS_ENV: str(Path(STORAGE_DIR) / "training")})


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
# same VRAM peak, frames indistinguishable. Wan 2.2 14B I2V was compared
# 2026-09-12: ck left NaN patch tokens in Lightning renders (black tiles on 6 of 9
# clips at 960x544) where PyTorch attention was clean, so the Wan 14B registry
# entries declare `attention: pytorch` and the Wan graph pins it per model while
# the process-wide flag stays. LTX and Hunyuan are not yet compared, so `auto` is
# a documented opt-in rather than the default. `auto`
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
# The reserve the next launch should use when no explicit override is set:
# written by the video generator for the model about to run (the registry's
# `comfyui_reserve_vram_gb`), read by plugins/comfyui/scripts/start.sh.
# Precedence: RESERVE_VRAM_ENV set explicitly > this file > RESERVE_VRAM_DEFAULT.
RESERVE_REQUEST_FILE = "pids/comfyui.reserve-vram"


def parse_reserve_vram_gb(raw: Optional[str]) -> Optional[float]:
    """A GB value from text, or None when it is not a non-negative number."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def explicit_reserve_vram_gb(env: Optional[Mapping[str, str]] = None) -> Optional[float]:
    """The operator's own reserve (RESERVE_VRAM_ENV), or None when unset/invalid."""
    src = env if env is not None else os.environ
    return parse_reserve_vram_gb(src.get(RESERVE_VRAM_ENV))


def reserve_request_path(project_root) -> Path:
    return Path(project_root) / RESERVE_REQUEST_FILE


def read_reserve_request(project_root) -> Optional[float]:
    try:
        return parse_reserve_vram_gb(reserve_request_path(project_root).read_text(encoding="utf-8"))
    except OSError:
        return None


def write_reserve_request(project_root, gb: float) -> Path:
    path = reserve_request_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{float(gb):g}\n", encoding="utf-8")
    return path


def reserve_vram_cli_args(
    env: Optional[Mapping[str, str]] = None, *, requested: Optional[float] = None
) -> Sequence[str]:
    """Return `--reserve-vram <gb>` for a ComfyUI argv.

    An explicit RESERVE_VRAM_ENV wins; otherwise `requested` (the model's
    declared reserve, via the request file); otherwise the default.
    """
    value = explicit_reserve_vram_gb(env)
    if value is None:
        value = requested if requested is not None and requested >= 0 else RESERVE_VRAM_DEFAULT
    return ["--reserve-vram", f"{value:g}"]


def reserve_vram_from_argv(argv: Sequence[str]) -> Optional[float]:
    """The --reserve-vram a running ComfyUI was launched with (its /system_stats
    reply carries sys.argv), or None when the flag is absent or unreadable."""
    items = [str(a) for a in (argv or [])]
    for i, item in enumerate(items):
        if item == "--reserve-vram" and i + 1 < len(items):
            return parse_reserve_vram_gb(items[i + 1])
        if item.startswith("--reserve-vram="):
            return parse_reserve_vram_gb(item.split("=", 1)[1])
    return None
