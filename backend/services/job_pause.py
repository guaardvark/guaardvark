"""Pause and resume a job by kind.

Pause holds work without losing it, where cancel throws it away: a video
batch keeps the clips it has finished and its place in the list, and a
scheduled job keeps its request. Only kinds that can pick up where they
stopped are listed; ``pausable`` and ``resumable`` say so per job for the
pages (job metadata).
"""

from __future__ import annotations

import logging

from backend.services.job_types import JobKind

logger = logging.getLogger(__name__)

_TASK_KINDS = (JobKind.TASK, JobKind.OUTREACH, JobKind.WEBSITE, JobKind.PUBLISH)


def _video_gen(native_id: str, paused: bool) -> bool:
    from backend.services.batch_video_generator import get_batch_video_generator

    generator = get_batch_video_generator()
    return bool(generator.pause_batch(str(native_id)) if paused else generator.resume_batch(str(native_id)))


def _task(native_id: str, paused: bool) -> bool:
    """A scheduled row is held with no time; a running hand-off row has the
    job it follows paused. A held row is resumed by scheduling it again
    (/api/night-shift/jobs/<id>/schedule), which needs to know when."""
    from backend.models import Task, db
    from backend.services import job_handoff, night_shift

    task = db.session.get(Task, int(native_id))
    if task is None:
        return False
    if task.status == night_shift.WAITING:
        return night_shift.hold(task.id) if paused else False
    if task.status in ("in-progress", "paused"):
        return job_handoff.set_paused(task.type, task.handler_config, paused)
    return False


def set_paused(kind: JobKind, native_id: str, paused: bool) -> bool:
    """Pause (or resume) one job. False when its kind cannot be paused or the
    job is not in a state to be. Never raises."""
    try:
        if kind == JobKind.VIDEO_GEN:
            return _video_gen(native_id, paused)
        if kind in _TASK_KINDS:
            return _task(native_id, paused)
    except Exception as e:  # noqa: BLE001 - called from an HTTP handler
        logger.exception("set_paused(%s:%s, %s) raised %s", kind.value, native_id, paused, e)
    return False


def task_flags(row) -> dict:
    """``pausable`` / ``resumable`` for a Task row's job metadata."""
    from backend.services import job_handoff, night_shift

    status = getattr(row, "status", None)
    if status == night_shift.WAITING:
        held = getattr(row, "schedule_type", None) not in night_shift.SCHEDULED_TYPES
        return {"pausable": not held}
    can = job_handoff.can_pause(getattr(row, "type", None), getattr(row, "handler_config", None))
    return {"pausable": can and status == "in-progress", "resumable": can and status == "paused"}
