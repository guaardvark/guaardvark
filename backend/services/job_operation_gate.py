"""JobOperationGate — cross-surface traffic light for jobs.

Counterpart of PluginOperationGate (backend/plugins/plugin_manager.py:50)
applied at the Jobs/Activity level. Stops two surfaces from stepping on
each other: e.g. when Activity is mid-load on an ACE-Step training (10 GB
exclusive VRAM), Jobs should disable the "Render now" button on a video
editor card and surface a banner instead of letting the user enqueue a
render that will queue behind / fight for GPU.

Per the user's note in plans/2026-04-29-tasks-jobs-progress-unification.md
§8.1 — the two pages need to be aware of each other; this is the shared
state both poll and respect.

State the gate tracks:
- which JobKinds are currently in-progress (any number, per-kind set of ids)
- which kinds claim GPU exclusivity (TRAINING, VIDEO_RENDER) and who holds it now
- per-kind cooldown after release (parallels PluginOperationGate)

Reads happen via /api/jobs/gate (snapshot) and the jobs:gate socket event
(live updates on claim/release). Writes happen from the kinds themselves
when they start / finish — the orchestrator calls into the gate at the
same point it requests VRAM.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from backend.services.job_types import JobKind

logger = logging.getLogger(__name__)


# Kinds that can't share the GPU with anything heavy. Consistent with
# requires_exclusive_vram on AudioBackend (introduced in commit 8d779ac).
# LORA_TRAIN added: per-Subject LoRA training is a full GPU load on the shared
# 16GB card and must serialize against video render and model finetune.
# Storyboard image generation rides the VIDEO_RENDER slot (the "heavy GPU
# generation" bucket) so it cannot run concurrently with an editor render on
# the same card.
GPU_EXCLUSIVE_KINDS: set[JobKind] = {
    JobKind.TRAINING,
    JobKind.VIDEO_RENDER,
    JobKind.LORA_TRAIN,
}

# Cooldown after a GPU-exclusive job releases — gives CUDA a moment to
# settle before another claim. Mirrors PLUGIN_COOLDOWN_GPU_S in spirit.
GPU_RELEASE_COOLDOWN_S = 8.0


class GpuBusyError(Exception):
    """Raised by ``gpu_exclusive(..., on_busy="raise")`` when the GPU is already
    held by another job. Carries the human-readable reason from the gate so the
    caller can surface it (UI banner) or fail the stage cleanly."""


class GpuCapacityError(GpuBusyError):
    """The estimate can never fit this card; retrying will not help."""


class GpuQueuedError(GpuBusyError):
    """Not this job's turn: the job arbiter is letting another waiting job claim
    the GPU first (job order setting on). Retrying soon is the point; the
    message says where the job is in line."""


class SystemLoadBusyError(GpuBusyError):
    """The GPU could take the job but the machine cannot yet: system RAM, swap or
    CPU is short (GlobalLoadGate). ``detail`` says which, in plain words."""

    def __init__(self, detail: str):
        super().__init__(f"System under heavy load — {detail}")
        self.detail = detail


# How a queue panel names the job holding the GPU. VIDEO_RENDER is the general
# GPU-exclusive kind (stills, edits, songs and storyboards claim it too), so the
# kind alone does not say what is rendering; the native id does. Batch ids carry
# their kind as a prefix (output_registration.BATES_PREFIXES, the blueprint/upload
# image batches); the other prefixes are the ids each caller passes to
# gpu_session. An id no prefix matches is named by the kind, and VIDEO_RENDER
# only as "another job". Mirrored by describeGpuHolder in useJobsGate.js.
_HOLDER_ID_LABELS = (
    ("VideoBatch", "Video Gen"),
    ("ImageBatch", "Image Gen"),
    ("blueprint_", "Image Gen"),
    ("upload_", "Image Gen"),
    ("stills_", "a picture"),            # stills_pipeline (chat, CLI, MCP, batch)
    ("gen_", "a picture"),               # offline_image_generator direct
    ("chat_edit_", "a picture edit"),    # image_tools / comfyui_image_generator
    ("chat_anim_", "an animation"),      # image_tools
    ("infographic_", "an infographic"),  # infographic_generator
    ("char_", "character pictures"),     # character_generation_tasks
    ("storyboard_", "storyboard pictures"),  # production_swarm_tasks
    ("prod_", "a Film Crew render"),         # production_swarm_tasks
    ("mv_", "a music video"),            # music_video_tasks / music_video_api
    ("video:music3:", "a song"),         # comfyui_music_generator
    ("editor_", "a video render"),       # video_render_tasks
    ("router:", "a video render"),       # video_generation_router
    ("upscale:", "an upscale"),          # upscale_supervisor
)
_HOLDER_KIND_LABELS = {
    "video_render": "another job",
    "training": "model training",
    "lora_train": "LoRA training",
}
# The gate's refusal: "GPU is held by <kind>:<native_id> — wait for completion".
_GATE_HOLDER_RE = re.compile(r"held by (?P<kind>[a-z_]+):(?P<id>\S+)")


def gpu_holder_label(exc: Optional[BaseException]) -> Optional[str]:
    """Plain name of the job a GPU refusal says holds the card, or None."""
    text = str(exc or "")
    if "held by another process" in text:
        return "another process"
    m = _GATE_HOLDER_RE.search(text)
    if not m:
        return None
    for prefix, label in _HOLDER_ID_LABELS:
        if m.group("id").startswith(prefix):
            return label
    kind = m.group("kind")
    return _HOLDER_KIND_LABELS.get(kind, kind.replace("_", " "))


def _wait_key(reason: str) -> str:
    """A refusal reason without its numbers, so a cooldown countdown is one wait."""
    return re.sub(r"\d+(?:\.\d+)?", "#", reason or "")


def gpu_wait_message(exc: Optional[BaseException], free_mb, need_mb) -> str:
    """What a queued job is waiting for, for the queue panel and progress footer.

    Names the job holding the GPU when the refusal says who it is."""
    if isinstance(exc, SystemLoadBusyError):
        return f"Waiting for the system: {exc.detail}"
    holder = gpu_holder_label(exc)
    if holder:
        return (
            f"Queued behind {holder} — needs ~{need_mb / 1024:.1f} GB, "
            f"{(free_mb or 0) / 1024:.1f} GB free"
        )
    return f"Waiting for VRAM — {(free_mb or 0) / 1024:.1f}GB free, need ~{need_mb / 1024:.1f}GB"


class GpuOOMError(RuntimeError):
    """CUDA/MPS ran out of memory during a render."""


def is_cuda_oom(exc: BaseException) -> bool:
    """True for torch/MPS out-of-memory errors, by type name or message."""
    if type(exc).__name__ == "OutOfMemoryError" or isinstance(exc, GpuOOMError):
        return True
    msg = (str(exc) or "").lower()
    return (
        "out of memory" in msg
        and any(k in msg for k in ("cuda", "cublas", "cudnn", "mps"))
    ) or "cuda out of memory" in msg


def classify_render_exception(exc: BaseException):
    """The RenderErrorKind for an exception raised while a render runs in this
    process: a GPU refusal, an OOM, ComfyUI unreachable, else UNKNOWN."""
    from backend.services.job_types import RenderErrorKind, failure_kind

    carried = failure_kind(exc) or next((failure_kind(a) for a in getattr(exc, "args", ()) if failure_kind(a)), None)
    if carried:
        return carried
    try:
        from backend.services.gpu_resource_policy import RenderStopped
        if isinstance(exc, RenderStopped):
            return RenderErrorKind.CANCELLED
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, GpuCapacityError):
        return RenderErrorKind.CARD_TOO_SMALL
    if isinstance(exc, GpuBusyError):
        return RenderErrorKind.VRAM_BUSY
    if is_cuda_oom(exc):
        return RenderErrorKind.OOM
    try:
        import requests
        unreachable = (requests.ConnectionError, requests.Timeout)
    except (ImportError, AttributeError):  # pragma: no cover - requests is a backend dependency
        unreachable = ()
    if unreachable and isinstance(exc, unreachable):
        # requests names the URL it could not reach. Another service's (the
        # video editor a music video assembles in, Audio Foundry) is not
        # ComfyUI; without a URL, ComfyUI stays the likely one.
        url = str(getattr(getattr(exc, "request", None), "url", None) or "")
        if url and not _is_comfyui_url(url):
            return RenderErrorKind.SERVICE_DOWN
        return RenderErrorKind.COMFYUI_DOWN
    return RenderErrorKind.UNKNOWN


def _is_comfyui_url(url: str) -> bool:
    """True when ``url`` is on ComfyUI's port (config.COMFYUI_URL). The host is
    not compared: localhost and 127.0.0.1 both name this machine."""
    from urllib.parse import urlsplit
    try:
        from backend.config import COMFYUI_URL
    except Exception:  # noqa: BLE001 - config needs the full backend environment
        COMFYUI_URL = os.environ.get("GUAARDVARK_COMFYUI_URL") or "http://127.0.0.1:8188"
    try:
        return urlsplit(url).port == urlsplit(COMFYUI_URL).port
    except ValueError:
        return False


class JobOperationGate:
    """Thread-safe gate coordinating cross-surface job ops.

    Singleton; accessed through `get_gate()`. Consumers either:
    1. Try to claim before starting work (`try_claim_gpu_exclusive`); on
       refusal, surface a busy banner / disable the button.
    2. Read the gate state for display (`snapshot`) and let the user
       decide.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # in_progress: kind → set of native_ids currently running
        self._in_progress: dict[JobKind, set[str]] = {k: set() for k in JobKind}
        # GPU exclusivity holder — at most one kind+id at a time
        self._gpu_holder: Optional[tuple[JobKind, str, float]] = None  # (kind, id, claimed_at)
        # Thread that claimed the GPU slot — if it dies without releasing, reap on next claim.
        self._holder_thread: Optional[threading.Thread] = None
        # Last-released-at timestamp for cooldown reasoning
        self._gpu_last_released: float = 0.0
        # "kind:native_id" -> how many waits that job has open; see waiting_for_gpu.
        self._gpu_waiters: dict[str, int] = {}

    # ---- claim / release ---------------------------------------------------

    def register_running(self, kind: JobKind, native_id: str) -> None:
        """Mark a job in-progress without claiming GPU exclusivity.

        For any kind that runs alongside others (CPU-only, lightweight, etc.)
        Just tracks visibility for the snapshot endpoint.
        """
        with self._lock:
            self._in_progress[kind].add(str(native_id))

    def unregister_running(self, kind: JobKind, native_id: str) -> None:
        with self._lock:
            self._in_progress[kind].discard(str(native_id))

    def _reap_dead_holder_locked(self) -> None:
        """Release the GPU slot if the claiming thread exited without cleanup."""
        if self._gpu_holder is None:
            return
        ht = self._holder_thread
        if ht is not None and ht.is_alive():
            return
        hk, hid, _ = self._gpu_holder
        logger.warning(
            "Reaping stale GPU slot held by dead thread: %s:%s",
            hk.value,
            hid,
        )
        self._gpu_holder = None
        self._holder_thread = None
        self._in_progress[hk].discard(hid)
        self._gpu_last_released = time.monotonic()

    def try_claim_gpu_exclusive(
        self, kind: JobKind, native_id: str
    ) -> tuple[bool, str]:
        """Try to claim GPU-exclusivity for `kind:native_id`.

        Returns (acquired, reason). On True, the caller proceeds and MUST
        call `release_gpu_exclusive` when done. On False, `reason` is a
        human-readable description suitable for the UI.
        """
        if kind not in GPU_EXCLUSIVE_KINDS:
            # Caller's a non-exclusive kind — just register and proceed.
            self.register_running(kind, native_id)
            return True, "Non-exclusive kind; running alongside other jobs"

        with self._lock:
            now = time.monotonic()
            self._reap_dead_holder_locked()

            # Already held? Either by us (idempotent) or by someone else.
            if self._gpu_holder is not None:
                hk, hid, _ = self._gpu_holder
                # Idempotent only if WE (same kind + same id) already hold it.
                # The compared tuple must use the HELD id (hid), not native_id —
                # comparing native_id to itself wrongly told any same-kind caller
                # it already held the GPU while a different id held it.
                if (hk, hid) == (kind, str(native_id)):
                    return True, "Already holding GPU exclusively"
                return False, f"GPU is held by {hk.value}:{hid} — wait for completion"

            # Cooldown after a recent release?
            cooldown_remaining = (self._gpu_last_released + GPU_RELEASE_COOLDOWN_S) - now
            if cooldown_remaining > 0:
                return False, f"GPU cooling down — try again in {cooldown_remaining:.1f}s"

            # OK, take the slot.
            self._gpu_holder = (kind, str(native_id), time.time())
            self._holder_thread = threading.current_thread()
            self._in_progress[kind].add(str(native_id))
            return True, "GPU claimed exclusively"

    def release_gpu_exclusive(
        self, kind: JobKind, native_id: str, *, cooldown: bool = True
    ) -> None:
        """Release a previously-claimed GPU-exclusive slot. Idempotent.

        ``cooldown=False`` clears the holder without starting the post-release
        cooldown — for a claim that was refused before any work touched the card.
        """
        with self._lock:
            if self._gpu_holder is None:
                self._in_progress[kind].discard(str(native_id))
                return
            hk, hid, _ = self._gpu_holder
            if (hk, hid) != (kind, str(native_id)):
                # Not the holder — still drop the in-progress flag so the
                # snapshot doesn't show a phantom run.
                self._in_progress[kind].discard(str(native_id))
                return
            self._gpu_holder = None
            self._holder_thread = None
            if cooldown:
                self._gpu_last_released = time.monotonic()
            self._in_progress[kind].discard(str(native_id))

    # ---- contextmanager ----------------------------------------------------

    @contextmanager
    def waiting_for_gpu(self, kind: JobKind, native_id: str) -> Iterator[None]:
        """Mark a job as waiting its turn for the GPU for the ``with`` block.

        Between one job's release and the next one's claim the card looks free;
        anything that would load a model of its own then (Keep ready reloading
        the chat model) reads ``gpu_waiting`` in the snapshot and holds off, or
        the waiting job's fit check passes before that memory lands and the job
        runs out of memory. Wrap only the wait, not the work: a claimed job is
        already ``gpu_busy``.
        """
        key = f"{getattr(kind, 'value', kind)}:{native_id}"
        with self._lock:
            self._gpu_waiters[key] = self._gpu_waiters.get(key, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                left = self._gpu_waiters.get(key, 0) - 1
                if left > 0:
                    self._gpu_waiters[key] = left
                else:
                    self._gpu_waiters.pop(key, None)

    @contextmanager
    def gpu_exclusive(
        self,
        kind: JobKind,
        native_id: str,
        *,
        on_busy: str = "raise",
        wait_timeout: float = 120.0,
        cancel_event: Optional[threading.Event] = None,
        on_wait: Optional[Callable[[str], None]] = None,
        admit: Optional[Callable[[], tuple[bool, str]]] = None,
    ) -> Iterator[bool]:
        """Claim the GPU-exclusive slot for the duration of a ``with`` block.

        This is the single front door every GPU-heavy surface should use so the
        in-memory gate actually serializes work on the shared 16GB card.

        on_busy controls behaviour when the slot can't be claimed:
          - "raise"    (default): raise GpuBusyError(reason). Caller fails the
            stage cleanly instead of piling a second job onto the GPU.
          - "register": fall back to register_running() for snapshot visibility
            (DEGRADED — the job runs WITHOUT real exclusivity; use only where
            blocking is worse than contention).
          - "wait": poll up to ``wait_timeout`` seconds for the slot to free, then
            claim it — for SERIAL BACKGROUND queues (e.g. batch video) that should
            wait out a transient busy / the 8s post-release cooldown instead of
            failing the job. Raises GpuBusyError only if it never frees in time,
            or as soon as ``cancel_event`` is set: a cancelled job must not sit
            out the rest of the wait in front of the next one. ``on_wait(reason)``
            is called when the wait starts and whenever the holder changes, so the
            caller can show what it is queued behind.

        ``admit()`` (the job arbiter's turn check, job_arbiter.admit_callable)
        is asked before every claim attempt; while it answers (False, reason)
        the slot is not tried, exactly as if it were busy for that reason, and
        a refusal that ends the wait raises GpuQueuedError. None (job order
        off) leaves every attempt a plain try_claim_gpu_exclusive.

        Yields True when the exclusive slot was acquired, False in the
        degraded ("register") path. Release is idempotent and always runs in
        ``finally`` — it clears both the holder and the in-progress flag, so a
        bare register_running() in the degraded path is cleaned up too.
        """
        queued = False

        def _attempt() -> tuple[bool, str]:
            nonlocal queued
            if admit is not None:
                turn, why = admit()
                queued = not turn
                if not turn:
                    return False, why
            return self.try_claim_gpu_exclusive(kind, native_id)

        acquired, reason = _attempt()
        if not acquired and on_busy == "wait":
            # Serial background callers wait out a transient busy / the post-release
            # cooldown rather than fail. Poll try_claim until it frees or we time out.
            import time as _t
            deadline = _t.monotonic() + max(0.0, wait_timeout)
            reported = None
            with self.waiting_for_gpu(kind, native_id):
                while not acquired and _t.monotonic() < deadline:
                    if on_wait is not None and _wait_key(reason) != reported:
                        reported = _wait_key(reason)
                        try:
                            on_wait(reason)
                        except Exception:  # noqa: BLE001 — a status line must not end the wait
                            logger.debug("gpu_exclusive on_wait callback failed", exc_info=True)
                    if cancel_event is not None and cancel_event.is_set():
                        reason = f"cancelled while waiting for the GPU ({reason})"
                        queued = False
                        break
                    _t.sleep(min(1.0, max(0.05, deadline - _t.monotonic())))
                    acquired, reason = _attempt()
            if acquired:
                logger.info(
                    "gpu_exclusive(%s:%s) acquired after waiting out gate busy/cooldown",
                    kind.value, native_id,
                )
        if not acquired:
            if on_busy == "register":
                logger.warning(
                    "gpu_exclusive(%s:%s) busy (%s) — running DEGRADED "
                    "(register-only, no real exclusivity)",
                    kind.value, native_id, reason,
                )
                self.register_running(kind, native_id)
                try:
                    yield False
                finally:
                    self.release_gpu_exclusive(kind, native_id)
                return
            raise (GpuQueuedError if queued else GpuBusyError)(reason)

        try:
            yield True
        finally:
            # Idempotent: clears the holder if we hold it, and always drops the
            # in-progress flag. Safe even for non-exclusive kinds (which only
            # registered) and double-calls.
            self.release_gpu_exclusive(kind, native_id)

    # ---- snapshot ----------------------------------------------------------

    def snapshot(self) -> dict:
        """Wire-format gate state for /api/jobs/gate."""
        with self._lock:
            in_progress = {
                kind.value: sorted(ids) for kind, ids in self._in_progress.items() if ids
            }
            holder = None
            if self._gpu_holder is not None:
                hk, hid, claimed_at = self._gpu_holder
                holder = {
                    "kind": hk.value,
                    "native_id": hid,
                    "claimed_at": claimed_at,
                    "duration_s": time.time() - claimed_at,
                }
            now = time.monotonic()
            cooldown_remaining = max(
                0.0,
                (self._gpu_last_released + GPU_RELEASE_COOLDOWN_S) - now,
            )
            return {
                "in_progress": in_progress,
                "gpu_busy": holder is not None,
                "gpu_holder": holder,
                "gpu_cooldown_remaining_s": round(cooldown_remaining, 2),
                "gpu_waiting": sorted(self._gpu_waiters),
                "gpu_exclusive_kinds": sorted(k.value for k in GPU_EXCLUSIVE_KINDS),
            }


# Singleton accessor — patterned on PluginManager's _gate.

_GATE_SINGLETON: Optional[JobOperationGate] = None
_GATE_LOCK = threading.Lock()


def get_gate() -> JobOperationGate:
    global _GATE_SINGLETON
    if _GATE_SINGLETON is None:
        with _GATE_LOCK:
            if _GATE_SINGLETON is None:
                _GATE_SINGLETON = JobOperationGate()
    return _GATE_SINGLETON
