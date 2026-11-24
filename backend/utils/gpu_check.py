"""GPU/generation-activity checks for autoresearch politeness.

An overnight research run must never fight the user's image/video generation
for the GPU. The signals:

- The cross-process GPU lease (pids/gpu_lock.json), which every heavy GPU job
  holds while it runs: renders, image and music generation, upscales, LoRA and
  model training (gpu_resource_policy.gpu_session(cross_process=True)).
- An active ComfyUI job (its queue endpoint reports running items), which
  covers work started in ComfyUI itself.
- VRAM pressure via nvidia-smi (same probe pattern as the swarm plugin's
  resource monitor), checked between experiments only.

Each probe fails soft to "not busy": a broken probe must not permanently stall
research.
"""
import logging
import subprocess
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

VRAM_BUSY_THRESHOLD_PCT = 60.0
# How often a waiting model call looks again. A render holds the card for
# minutes, so a few seconds of lag after it ends costs nothing.
GPU_TURN_POLL_S = 5.0


def vram_used_pct() -> float:
    """Percent of VRAM in use across GPUs (max), or 0.0 if unprobeable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return 0.0
        worst = 0.0
        for line in out.stdout.strip().splitlines():
            used_s, total_s = line.split(",")
            used, total = float(used_s), float(total_s)
            if total > 0:
                worst = max(worst, used / total * 100.0)
        return worst
    except Exception as e:
        logger.debug(f"VRAM probe failed: {e}")
        return 0.0


def comfyui_generating() -> bool:
    """Is ComfyUI actively rendering something right now?"""
    try:
        import requests
        from backend.config import COMFYUI_URL
        resp = requests.get(f"{COMFYUI_URL}/queue", timeout=5)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get("queue_running"))
    except Exception:
        return False


def gpu_reserved_reason() -> Optional[str]:
    """Why a background model call must wait right now, or None.

    Only the explicit signals that a render or training job is running. VRAM in
    use is not one: the research run's own models in Ollama would count against it.
    """
    try:
        from backend.services.gpu_resource_coordinator import get_gpu_coordinator

        if get_gpu_coordinator().lock_held():
            return "a render or training job holds the GPU lease"
    except Exception as e:
        logger.debug(f"GPU lease probe failed: {e}")
    if comfyui_generating():
        return "ComfyUI is rendering"
    return None


def wait_for_gpu_turn(
    should_stop: Optional[Callable[[], bool]] = None,
    on_wait: Optional[Callable[[str], None]] = None,
    poll_s: float = GPU_TURN_POLL_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[float]:
    """Block until no render or training job holds the GPU.

    Returns the seconds spent waiting (0.0 when the GPU was free), or None when
    ``should_stop()`` turned true, which is checked first on every look so a
    stopped caller never makes another model call. ``on_wait(reason)`` is called
    when a wait starts and whenever its reason changes.
    """
    started = clock()
    reported = None
    while True:
        if should_stop is not None and should_stop():
            return None
        reason = gpu_reserved_reason()
        if reason is None:
            return clock() - started
        if on_wait is not None and reason != reported:
            reported = reason
            try:
                on_wait(reason)
            except Exception:  # noqa: BLE001 - a status line must not end the wait
                logger.debug("wait_for_gpu_turn on_wait failed", exc_info=True)
        sleep(poll_s)


def gpu_busy(threshold_pct: float = VRAM_BUSY_THRESHOLD_PCT) -> bool:
    """Should a research run yield the GPU right now?"""
    if gpu_reserved_reason() is not None:
        return True
    return vram_used_pct() > threshold_pct
