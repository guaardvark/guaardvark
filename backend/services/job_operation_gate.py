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
import threading
import time
from typing import Optional

from backend.services.job_types import JobKind

logger = logging.getLogger(__name__)


# Kinds that can't share the GPU with anything heavy. Consistent with
# requires_exclusive_vram on AudioBackend (introduced in commit 8d779ac).
GPU_EXCLUSIVE_KINDS: set[JobKind] = {JobKind.TRAINING, JobKind.VIDEO_RENDER}

# Cooldown after a GPU-exclusive job releases — gives CUDA a moment to
# settle before another claim. Mirrors PLUGIN_COOLDOWN_GPU_S in spirit.
GPU_RELEASE_COOLDOWN_S = 8.0


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
        # Last-released-at timestamp for cooldown reasoning
        self._gpu_last_released: float = 0.0

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

            # Already held? Either by us (idempotent) or by someone else.
            if self._gpu_holder is not None:
                hk, hid, _ = self._gpu_holder
                if (hk, str(native_id)) == (kind, str(native_id)):
                    return True, "Already holding GPU exclusively"
                return False, f"GPU is held by {hk.value}:{hid} — wait for completion"

            # Cooldown after a recent release?
            cooldown_remaining = (self._gpu_last_released + GPU_RELEASE_COOLDOWN_S) - now
            if cooldown_remaining > 0:
                return False, f"GPU cooling down — try again in {cooldown_remaining:.1f}s"

            # OK, take the slot.
            self._gpu_holder = (kind, str(native_id), time.time())
            self._in_progress[kind].add(str(native_id))
            return True, "GPU claimed exclusively"

    def release_gpu_exclusive(self, kind: JobKind, native_id: str) -> None:
        """Release a previously-claimed GPU-exclusive slot. Idempotent."""
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
            self._gpu_last_released = time.monotonic()
            self._in_progress[kind].discard(str(native_id))

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
