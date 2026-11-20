"""The ComfyUI release this install runs, checked against a model's floor.

A video-registry entry may declare ``min_comfyui`` ("0.45"): the oldest
ComfyUI release its graph is known to run on. Below it the model is left out
of every list a person picks from, and a render that names it is refused.

The version comes from the installed tree (``comfyui_version.py``), which can
be read while ComfyUI is stopped. When the user runs ComfyUI themselves (the
card's external switch) or the tree cannot be read, the running server's
``/system_stats`` answers instead. An unknown version is never a refusal.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# List endpoints call this on every page load; a ComfyUI update invalidates it.
_TTL_S = 30.0
_lock = threading.Lock()
_cache: dict = {"at": None, "value": None}


def parse_version(raw) -> Optional[tuple]:
    """(major, minor) from "0.46.0", "v0.45" or (0, 46); None when unreadable."""
    if isinstance(raw, tuple):
        return tuple(int(p) for p in raw[:2]) if len(raw) >= 2 else None
    match = re.match(r"\s*v?(\d+)\.(\d+)", str(raw or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _external() -> bool:
    try:
        from backend.plugins.plugin_manager import comfyui_is_external
        return comfyui_is_external()
    except Exception:  # noqa: BLE001 - no switch readable: treat as the bundled ComfyUI
        return False


def _tree_version() -> Optional[tuple]:
    try:
        from backend.config import COMFYUI_DIR
        from backend.services.comfyui_update_service import read_tree_version
        return parse_version(read_tree_version(Path(COMFYUI_DIR)))
    except Exception:  # noqa: BLE001
        logger.debug("ComfyUI tree version not readable", exc_info=True)
        return None


def _server_version() -> Optional[tuple]:
    try:
        import requests
        from backend.services.comfyui_update_service import default_base_url
        stats = requests.get(f"{default_base_url()}/system_stats", timeout=2).json()
        return parse_version((stats.get("system") or {}).get("comfyui_version"))
    except Exception:  # noqa: BLE001 - a stopped server is an unknown version
        return None


def comfyui_version(*, fresh: bool = False) -> Optional[tuple]:
    """(major, minor) of the ComfyUI this install renders with, or None."""
    now = time.monotonic()
    with _lock:
        if not fresh and _cache["at"] is not None and now - _cache["at"] < _TTL_S:
            return _cache["value"]
    value = None if _external() else _tree_version()
    if value is None:
        value = _server_version()
    with _lock:
        _cache["at"], _cache["value"] = now, value
    return value


def invalidate() -> None:
    """Forget the cached version (after a ComfyUI update or rollback)."""
    with _lock:
        _cache["at"] = None


def min_comfyui(model_id: Optional[str]) -> Optional[tuple]:
    """The floor ``model_id``'s registry entry declares, or None."""
    try:
        from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
    except Exception:  # noqa: BLE001
        return None
    entry = VIDEO_MODEL_REGISTRY.get(model_id or "") or {}
    if "min_comfyui" not in entry and entry.get("like"):
        # A user model added before the floor was declared inherits its template's.
        entry = VIDEO_MODEL_REGISTRY.get(entry["like"]) or {}
    return parse_version(entry.get("min_comfyui"))


def too_old_for(model_id: Optional[str], version: Optional[tuple] = None) -> Optional[tuple]:
    """The floor ``model_id`` needs when the known ComfyUI is below it, else None."""
    floor = min_comfyui(model_id)
    if floor is None:
        return None
    ver = version if version is not None else comfyui_version()
    if ver is None or ver >= floor:
        return None
    return floor


def offered(model_id: Optional[str], version: Optional[tuple] = None) -> bool:
    """False when the model's floor is above the ComfyUI this install runs."""
    return too_old_for(model_id, version) is None


def refusal(model_id: Optional[str], name: Optional[str] = None) -> Optional[str]:
    """The sentence a render of a model below its floor gets, or None."""
    floor = too_old_for(model_id)
    if floor is None:
        return None
    ver = comfyui_version()
    label = name or model_id
    return (f"{label} needs ComfyUI {floor[0]}.{floor[1]} or newer; this install runs "
            f"ComfyUI {ver[0]}.{ver[1]}. Update ComfyUI from its plugin card, or pick another model.")
