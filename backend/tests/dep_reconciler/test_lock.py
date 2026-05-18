import multiprocessing as mp
import time
from pathlib import Path

import pytest

from scripts.dep_reconciler.lock import StateLock, LockTimeoutError


def test_acquires_and_releases(tmp_path):
    lock = StateLock(tmp_path / "test.lock")
    with lock.acquire(timeout=1.0):
        assert lock.is_held
    assert not lock.is_held


def _hold_lock_with_signal(path_str, ready_event, hold_seconds):
    """Helper for the multi-process timeout test.

    Signals readiness via mp.Event AFTER acquiring the lock so the
    parent doesn't race with child startup.
    """
    from scripts.dep_reconciler.lock import StateLock
    with StateLock(Path(path_str)).acquire(timeout=2.0):
        ready_event.set()
        time.sleep(hold_seconds)


def test_second_acquirer_times_out(tmp_path):
    lock_path = tmp_path / "shared.lock"
    ready = mp.Event()
    p = mp.Process(target=_hold_lock_with_signal, args=(str(lock_path), ready, 2.0))
    p.start()
    try:
        assert ready.wait(timeout=5.0), "child failed to acquire within 5s"
        with pytest.raises(LockTimeoutError):
            with StateLock(lock_path).acquire(timeout=0.5):
                pass
    finally:
        p.join(timeout=5)
        if p.is_alive():
            p.kill()
            p.join(timeout=2)
        assert not p.is_alive(), "child process still alive after kill"
