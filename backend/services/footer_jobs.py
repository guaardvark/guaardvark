"""Every piece of work shows in the progress footer.

The footer draws the unified progress feed (the job_progress socket event).
Work that never opened an entry there ran with an empty footer: photo edits,
identity renders and tool jobs did, from chat, MCP and the Image Edit page.

``footer_job`` opens an entry and closes it complete, failed or cancelled.
``ensure`` opens one only when this thread has none open yet, and every GPU
claim calls it (gpu_resource_policy.gpu_session), so a job that takes the GPU
shows even when its own code reports nothing. Work nested on the same thread
names itself on the entry it is inside instead of adding a second one.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import uuid
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

_tls = threading.local()

# Exceptions that mean the person stopped the work, not that it failed.
_STOPPED = ("GpuWaitStopped", "RenderStopped")


def _emit(process_id: str, progress: int, message: str, status: str, process_type: str,
          extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        from backend.utils.progress_emitter import emit_progress_event
        emit_progress_event(process_id=process_id, progress=progress, message=message[:300],
                            status=status, process_type=process_type, additional_data=extra)
    except Exception:  # noqa: BLE001 - the footer never fails the work it shows
        logger.debug("footer entry %s not emitted", process_id, exc_info=True)


class FooterJob:
    """One footer entry. ``update`` changes its line; ``fail`` marks it failed at close."""

    def __init__(self, process_id: str, process_type: str, title: str):
        self.process_id = process_id
        self.process_type = process_type
        self.title = title
        self.progress = 0
        self._failure: Optional[str] = None

    def update(self, message: str, progress: Optional[int] = None, **extra: Any) -> None:
        if progress is not None:
            self.progress = max(0, min(99, int(progress)))
        _emit(self.process_id, self.progress, message, "processing", self.process_type, extra or None)

    def fail(self, message: str) -> None:
        self._failure = message or "Failed"


def current() -> Optional[FooterJob]:
    """The entry this thread's work is showing on, if footer_jobs opened one."""
    return getattr(_tls, "job", None)


def _thread_has_entry() -> bool:
    try:
        from backend.utils.unified_progress_system import get_unified_progress
        return get_unified_progress().has_open_process_on_this_thread()
    except Exception:  # noqa: BLE001
        return False


@contextlib.contextmanager
def footer_job(process_type: str, title: str, *,
               extra: Optional[Dict[str, Any]] = None) -> Iterator[FooterJob]:
    """Show this work in the footer from start to finish."""
    outer = current()
    if outer is not None:
        outer.update(title)
        yield outer
        return
    job = FooterJob(f"{process_type}_{uuid.uuid4().hex[:10]}", process_type, title)
    _emit(job.process_id, 0, title, "start", process_type, extra)
    _tls.job = job
    try:
        yield job
    except BaseException as e:
        if type(e).__name__ in _STOPPED:
            _emit(job.process_id, job.progress, f"{title}: stopped", "cancelled", process_type)
        else:
            _emit(job.process_id, job.progress, f"{title}: {e}", "error", process_type)
        raise
    else:
        if job._failure:
            _emit(job.process_id, job.progress, job._failure, "error", process_type)
        else:
            _emit(job.process_id, 100, f"{title}: done", "complete", process_type)
    finally:
        _tls.job = None


@contextlib.contextmanager
def ensure(process_type: str, title: str, *, fallback: bool = False) -> Iterator[Optional[FooterJob]]:
    """``footer_job``, unless this thread's work already shows in the footer.

    ``fallback`` marks an entry opened only so the work is not invisible (every
    GPU claim opens one). The footer shows a fallback entry only while nothing
    else is showing: work that reports on another thread or feed (a video
    batch's job events, ComfyUI's step relay) is not listed twice.
    """
    if current() is not None or _thread_has_entry():
        yield current()
        return
    with footer_job(process_type, title, extra={"fallback": True} if fallback else None) as job:
        yield job
