"""LatencyTracker lock re-entry.

`EmbeddingRouter.get_stats()` is reachable from `GET /api/model/resources`, which
the Settings page polls, so a lock that cannot be re-entered there hangs a request
thread permanently rather than failing.
"""
from __future__ import annotations

import threading

from backend.utils.embedding_router import LatencyTracker


def _call_with_deadline(fn, seconds: float = 5.0):
    """Run `fn` in a daemon thread and return its result, or None if it blocked.

    The call under test deadlocks rather than raising when it regresses, so it is
    run off the main thread — otherwise a failure hangs the whole suite instead of
    reporting.
    """
    box = {}

    def run():
        box["value"] = fn()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=seconds)
    return None if t.is_alive() else box.get("value")


def test_get_stats_does_not_deadlock_on_its_own_lock():
    tracker = LatencyTracker()
    tracker.record("gpu", 12.0)
    tracker.record("cpu", 30.0)

    stats = _call_with_deadline(tracker.get_stats)

    assert stats is not None, "get_stats() blocked — LatencyTracker.lock is not reentrant"
    assert stats["gpu_samples"] == 1
    assert stats["cpu_samples"] == 1
    assert 0.0 <= stats["optimal_gpu_ratio"] <= 1.0


def test_get_stats_is_reentrant_with_no_samples():
    """The empty case takes the early-return branch and must re-enter too."""
    assert _call_with_deadline(LatencyTracker().get_stats) is not None
