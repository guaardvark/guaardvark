# backend/services/comfyui_launch_flags.py
"""CLI flags Guaardvark always passes when it starts ComfyUI.

Keep `plugins/comfyui/scripts/start.sh` in lockstep: that script cannot import
this module, so it repeats the same env names and defaults. Tests assert both.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional, Sequence

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
