"""Turn a Celery task failure into a visible progress error.

Progress entries are created by the task itself, keyed by its Celery id or by a
``job_id``/``process_id`` argument. When the task raises, nothing else ever
finishes that entry, so the UI keeps showing 0 %. This helper finds the entry
and marks it errored with the exception text.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

MESSAGE_LIMIT = 500
_ID_KEYS = ("job_id", "process_id", "task_id")


def candidate_ids(task_id: Optional[str], kwargs: Optional[dict]) -> Iterable[str]:
    """Progress ids a failing task may have used, most specific first."""
    seen = []
    for value in [task_id] + [(kwargs or {}).get(k) for k in _ID_KEYS]:
        if value and str(value) not in seen:
            seen.append(str(value))
    return seen


def failure_message(exception: Any) -> str:
    text = f"{type(exception).__name__}: {exception}" if exception is not None else "Task failed"
    return text[:MESSAGE_LIMIT]


def mark_progress_failed(task_id: Optional[str], exception: Any, kwargs: Optional[dict] = None, progress=None) -> Optional[str]:
    """Mark the first matching progress entry as errored; returns the id marked, or None."""
    if progress is None:
        from backend.utils.unified_progress_system import get_unified_progress

        progress = get_unified_progress()
    for pid in candidate_ids(task_id, kwargs):
        if progress.get_process(pid) is not None:
            progress.error_process(pid, failure_message(exception))
            return pid
    return None
