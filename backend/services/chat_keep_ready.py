"""Keep ready, for Settings → Models → Chat: the active chat model stays loaded
with no idle timeout, steps aside for image, video and training work, and is
loaded again once that work has released the card.

The switch is ``GUAARDVARK_CHAT_KEEP_READY`` in ``.env`` and in this process's
environment, like network access, so it is per machine and applies at once.

How the pieces fit:
- Chat requests ask Ollama to keep the model with ``keep_alive: -1``
  (``config.get_chat_keep_alive``), and the GPU orchestrator's idle eviction
  skips its slot. Exclusive work still evicts it.
- ``keep_ready_pass`` runs in the orchestrator's background loop (every 30 s,
  backend process only). When the model is not loaded and no GPU work is
  running, it loads it; when another caller left it with a finite keep_alive,
  it pins it again. It also loads the speech-to-text model voice chat uses.
- A load waits for free graphics memory instead of pushing a render out: Ollama
  would otherwise split the model onto the CPU or the render would run out of
  memory.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from backend import profiles as P

logger = logging.getLogger(__name__)

KEEP_READY_ENV = "GUAARDVARK_CHAT_KEEP_READY"

# Free graphics memory asked for beyond the model's file size before loading.
# Measured 2026-10-09 on a GTX 1080: qwen3.5:4b (3170 MB file) took 3754 MB of
# the card at a 4k context, about 600 MB over the file; 1024 leaves room for
# the longer contexts chat requests ask for.
LOAD_HEADROOM_MB = 1024
# A model Ollama will drop within this long counts as not pinned.
PIN_HORIZON = timedelta(days=1)
# Speech-to-text model voice chat starts with (voice_api's default).
WHISPER_MODEL = "tiny.en"

_lock = threading.Lock()
# The orchestrator's loop and the Settings route both run passes.
_pass_lock = threading.Lock()
_load_thread: Optional[threading.Thread] = None
_status = {"state": "off", "detail": None, "model": None, "since": time.time()}


class KeepReadyRefused(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def keep_ready_on() -> bool:
    return os.environ.get(KEEP_READY_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def set_keep_ready(enabled: bool, root: Optional[Path] = None) -> bool:
    """Persist the switch to .env and apply it in this process."""
    if not P.env_file_writable(root):
        raise KeepReadyRefused("Guaardvark cannot write its .env file, so Keep ready cannot be changed from here.")
    P.set_env_value(KEEP_READY_ENV, "1" if enabled else None, root)
    if enabled:
        os.environ[KEEP_READY_ENV] = "1"
    else:
        os.environ.pop(KEEP_READY_ENV, None)
        _set_status("off")
    return enabled


def status() -> dict:
    """What Keep ready is doing now, for Settings. "ready" is checked against
    Ollama on every read, since the last pass may be up to 30 s old."""
    with _lock:
        out = dict(_status)
    out["enabled"] = keep_ready_on()
    if not out["enabled"]:
        out.update(state="off", detail=None)
    elif out["state"] == "ready" and out.get("model"):
        try:
            if _loaded_entry(out["model"]) is None:
                out.update(state="waiting", detail=f"{out['model']} is not loaded; it loads at the next check")
        except Exception:
            out.update(state="waiting", detail="Ollama is not running")
    return out


def _set_status(state: str, detail: Optional[str] = None, model: Optional[str] = None) -> None:
    with _lock:
        changed = _status["state"] != state or _status["detail"] != detail
        _status.update(state=state, detail=detail, model=model or _status["model"])
        if changed:
            _status["since"] = time.time()
    if changed and state != "off":
        logger.info("Keep ready: %s%s", state, f" ({detail})" if detail else "")


def active_chat_model() -> Optional[str]:
    from backend.config import _read_saved_model_name
    return _read_saved_model_name()


def same_model(a: Optional[str], b: Optional[str]) -> bool:
    """Ollama names a model without a tag as name:latest."""
    if not a or not b:
        return False
    def full(n):
        return n if ":" in n else f"{n}:latest"
    return full(a.strip().lower()) == full(b.strip().lower())


def _ollama_url() -> str:
    from backend.config import OLLAMA_BASE_URL
    return OLLAMA_BASE_URL.rstrip("/")


def _gpu_busy_reason() -> Optional[str]:
    """Why the card is not free for the chat model now, or None."""
    try:
        from backend.services.gpu_resource_coordinator import get_gpu_coordinator
        lock = get_gpu_coordinator().get_gpu_status()
        if not lock.get("available", True):
            owner = (lock.get("lock_info") or {}).get("owner") or "another job"
            return f"the GPU is reserved for {owner}"
    except Exception:
        pass
    try:
        from backend.services.job_operation_gate import get_gate
        gate = get_gate().snapshot()
        if gate.get("gpu_busy"):
            return "GPU work is running"
        if gate.get("gpu_cooldown_remaining_s"):
            return "GPU work just finished"
    except Exception:
        pass
    try:
        from backend.config import COMFYUI_URL
        queue = requests.get(f"{COMFYUI_URL}/queue", timeout=1).json()
        if queue.get("queue_running") or queue.get("queue_pending"):
            return "ComfyUI is rendering"
    except Exception:
        pass
    return None


def _free_vram_mb() -> Optional[int]:
    try:
        from backend.services.gpu_resource_coordinator import get_available_vram
        info = get_available_vram()
        if info.get("total_mb"):
            return int(info.get("available_mb") or 0)
    except Exception:
        pass
    return None


def _model_size_mb(model: str) -> Optional[int]:
    try:
        tags = requests.get(f"{_ollama_url()}/api/tags", timeout=5).json().get("models", [])
    except Exception:
        return None
    for entry in tags:
        if same_model(entry.get("name"), model):
            return int(entry.get("size") or 0) // (1024 * 1024) or None
    return None


def _loaded_entry(model: str) -> Optional[dict]:
    """The model's /api/ps entry, None when it is not loaded. Raises when
    Ollama does not answer."""
    running = requests.get(f"{_ollama_url()}/api/ps", timeout=5).json().get("models", [])
    for entry in running:
        if same_model(entry.get("name"), model):
            return entry
    return None


def _pinned(entry: dict) -> bool:
    expires = entry.get("expires_at") or ""
    try:
        when = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError:
        return False
    return when - datetime.now(timezone.utc) > PIN_HORIZON


def _load(model: str) -> None:
    started = time.monotonic()
    try:
        # An empty prompt loads the model and returns; keep_alive -1 keeps it.
        response = requests.post(
            f"{_ollama_url()}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": -1},
            timeout=300,
        )
        response.raise_for_status()
        _set_status("ready", model=model)
        logger.info("Keep ready: %s loaded in %.1f s", model, time.monotonic() - started)
    except Exception as e:
        _set_status("waiting", f"loading {model} failed: {e}", model)


def _warm_speech() -> None:
    try:
        from backend.utils import faster_whisper_utils as W
        if W.FASTER_WHISPER_AVAILABLE and not W.is_loaded() and W.is_model_installed(WHISPER_MODEL):
            W.get_faster_whisper_model(WHISPER_MODEL)
    except Exception as e:
        logger.debug("Keep ready: speech model not loaded: %s", e)


def keep_ready_pass() -> None:
    """One check: load or pin the active chat model when the card allows."""
    if not keep_ready_on() or not _pass_lock.acquire(blocking=False):
        return
    try:
        _pass()
    finally:
        _pass_lock.release()


def _pass() -> None:
    global _load_thread
    if _load_thread is not None and _load_thread.is_alive():
        return
    model = active_chat_model()
    if not model:
        _set_status("waiting", "no chat model is set")
        return
    try:
        entry = _loaded_entry(model)
    except Exception:
        _set_status("waiting", "Ollama is not running", model)
        return
    if entry is not None:
        if not _pinned(entry):
            # Another caller asked for a finite keep_alive; ask again for none.
            _load_thread = threading.Thread(target=_load, args=(model,), name="keep-ready-pin", daemon=True)
            _load_thread.start()
            return
        _set_status("ready", model=model)
        _warm_speech()
        return
    busy = _gpu_busy_reason()
    if busy:
        _set_status("stepped aside", busy, model)
        return
    size = _model_size_mb(model)
    free = _free_vram_mb()
    if size and free is not None and free < size + LOAD_HEADROOM_MB:
        _set_status(
            "waiting",
            f"{free} MB of graphics memory free, {model} needs about {size + LOAD_HEADROOM_MB} MB",
            model,
        )
        return
    _set_status("loading", model=model)
    _load_thread = threading.Thread(target=_load, args=(model,), name="keep-ready-load", daemon=True)
    _load_thread.start()
