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


def _hold_lock(path_str, hold_seconds):
    """Helper for the multi-process timeout test."""
    from scripts.dep_reconciler.lock import StateLock
    with StateLock(Path(path_str)).acquire(timeout=1.0):
        time.sleep(hold_seconds)


def test_second_acquirer_times_out(tmp_path):
    lock_path = tmp_path / "shared.lock"
    p = mp.Process(target=_hold_lock, args=(str(lock_path), 2.0))
    p.start()
    time.sleep(0.3)  # let the child acquire
    try:
        with pytest.raises(LockTimeoutError):
            with StateLock(lock_path).acquire(timeout=0.5):
                pass
    finally:
        p.join(timeout=5)
