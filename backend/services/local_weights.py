"""Load pretrained weights from this machine only.

Nothing leaves a person's machine without them knowing. `from_pretrained`
with a bare hub id will happily fetch gigabytes from Hugging Face the first
time it runs, which turns "Generate" into an unannounced download. Every
runtime load in the backend goes through here, which forces
`local_files_only=True` and turns a cache miss into one clear error naming
the way to install the weights. Downloads happen only in the explicit
install flows (Manage Video Models, the image-model installer), never as a
side effect of generating something.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class WeightsNotInstalled(RuntimeError):
    """The weights are not on this machine and this code path will not fetch them."""


def from_pretrained_local(loader: Any, name_or_path: str, *, purpose: str,
                          install_hint: str, **kwargs: Any) -> Any:
    """`loader.from_pretrained(name_or_path, local_files_only=True, **kwargs)`.

    `loader` is any class exposing the Hugging Face `from_pretrained`
    contract (transformers, diffusers, controlnet_aux, ModelHubMixin). A
    missing local copy surfaces as OSError from all of them; it is re-raised
    as WeightsNotInstalled with `purpose` and `install_hint` so the person
    reads what is missing and how to get it, not a traceback about a URL.
    Any other failure (VRAM, corrupt file) propagates unchanged.
    """
    kwargs["local_files_only"] = True
    try:
        return loader.from_pretrained(name_or_path, **kwargs)
    except OSError as e:
        raise WeightsNotInstalled(
            f"{purpose}: weights for '{name_or_path}' are not on this machine. "
            f"{install_hint} Generation never downloads on its own."
        ) from e


def download_status(name: str, probe_file: str = "config.json",
                    cache_dir: Optional[str | Path] = None) -> dict:
    """What starting a job with this base model would do on the network.

    Used by the training form so a person sees "this will download X from
    huggingface.co" before they press Create, instead of discovering it from
    disk usage. `looks_like_hub_id` is False for Ollama-style tags (`llama3:8b`),
    which the hub will not serve at all; the caller says so.
    """
    name = (name or "").strip()
    looks_like_hub_id = "/" in name and ":" not in name and not name.startswith((".", "/"))
    cached = bool(name) and is_cached(name, probe_file, cache_dir=cache_dir)
    return {
        "name": name,
        "looks_like_hub_id": looks_like_hub_id,
        "cached": cached,
        "will_download": bool(name) and looks_like_hub_id and not cached,
    }


def is_cached(repo_id: str, probe_file: str = "model_index.json",
              cache_dir: Optional[str | Path] = None) -> bool:
    """True when `probe_file` of `repo_id` is already in the local HF cache.

    Diffusers pipelines carry model_index.json; transformers models carry
    config.json. Never touches the network.
    """
    try:
        from huggingface_hub import try_to_load_from_cache
        from huggingface_hub.file_download import _CACHED_NO_EXIST
    except Exception:  # pragma: no cover - huggingface_hub always ships with diffusers
        return False
    try:
        hit = try_to_load_from_cache(
            repo_id, probe_file, cache_dir=str(cache_dir) if cache_dir else None
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("cache probe for %s failed: %s", repo_id, e)
        return False
    return isinstance(hit, str) and hit is not _CACHED_NO_EXIST
