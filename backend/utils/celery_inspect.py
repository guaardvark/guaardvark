"""Ask the Celery workers a question without waiting out the whole timeout.

An inspect broadcast cannot know how many workers will answer, so it listens
for its full timeout every time: one second for ``active()``, and eight for a
health check that asks four questions at two seconds each. Told how many
replies to expect, it returns as soon as they are in (about 2 ms measured on a
box with two workers, 2026-11-25).

The number of workers is taken from the last broadcast that listened for the
whole timeout, and is counted again in the background once it is
``RECOUNT_AFTER_S`` old, so a worker that starts later is heard from within
that time. Until then a broadcast may return before the new worker's reply.
When fewer workers answer than expected, the count is dropped and the next
call listens for the whole timeout.

Use this for status display. A caller that must hear from every worker (the
outreach kill switch) asks Celery directly.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

RECOUNT_AFTER_S = 20.0
_RECOUNT_TIMEOUT_S = 2.0

_lock = threading.Lock()
_known = {"count": 0, "at": 0.0, "recounting": False}


def _remember(count: int) -> None:
    with _lock:
        _known["count"] = count
        _known["at"] = time.monotonic()


def _recount(celery) -> None:
    try:
        replies = celery.control.inspect(timeout=_RECOUNT_TIMEOUT_S).ping() or {}
        _remember(len(replies))
    except Exception:
        logger.debug("worker recount failed", exc_info=True)
        _remember(0)
    finally:
        with _lock:
            _known["recounting"] = False


def _expected(celery) -> int:
    """Workers to wait for, or 0 when unknown; starts a recount when the number is old."""
    with _lock:
        count = _known["count"]
        stale = count and time.monotonic() - _known["at"] > RECOUNT_AFTER_S
        start = bool(stale and not _known["recounting"])
        if start:
            _known["recounting"] = True
    if start:
        threading.Thread(target=_recount, args=(celery,), name="celery-recount", daemon=True).start()
    return count


def inspect_workers(celery, question: str, timeout: float = 1.0) -> dict[str, Any]:
    """One inspect broadcast (``"active"``, ``"stats"``, ``"reserved"``, ...) as ``{worker: reply}``."""
    count = _expected(celery)
    insp = celery.control.inspect(timeout=timeout, limit=count or None)
    replies = getattr(insp, question)() or {}
    if not count:
        _remember(len(replies))
    elif len(replies) < count:
        _remember(0)
    return replies
