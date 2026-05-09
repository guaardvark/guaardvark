"""fcntl-based file lock for cross-process exclusion. Stdlib-only."""
from __future__ import annotations

import contextlib
import errno
import fcntl
import time
from pathlib import Path


class LockTimeoutError(RuntimeError):
    """Raised when the lock can't be acquired within the timeout."""


class StateLock:
    """Cross-process advisory lock via fcntl.flock(LOCK_EX | LOCK_NB)."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    @property
    def is_held(self) -> bool:
        return self._fd is not None

    @contextlib.contextmanager
    def acquire(self, *, timeout: float = 30.0, poll: float = 0.2):
        if self._fd is not None:
            raise RuntimeError(
                f"StateLock at {self.path} already held by this instance — "
                "StateLock is not reentrant; use a separate instance for nested locks"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Open (creating if needed) for the lifetime of the lock.
        fd = open(self.path, "a+")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    fd.close()
                    raise
                if time.monotonic() >= deadline:
                    fd.close()
                    raise LockTimeoutError(
                        f"could not acquire {self.path} within {timeout}s"
                    )
                time.sleep(poll)
        self._fd = fd.fileno()
        try:
            yield self
        finally:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            finally:
                fd.close()
                self._fd = None
