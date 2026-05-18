"""Per-kind cancel dispatch.

The unified Tasks/Jobs page surfaces a single Cancel button regardless
of what kind of job a user is looking at — but the actual transport to
cancel a Task is different from a TrainingJob (PID SIGTERM) or a
VideoRender (ffmpeg SIGTERM) or a UnifiedProgress in-memory event.

This module owns that dispatch. cancel_job(kind, native_id) returns
True on success, False on a clean failure (job not found, not
cancellable, transport unavailable). Exceptions don't escape; this is
called from an API endpoint that should always return JSON, never crash.

Adds a new kind = add an entry to CANCEL_DISPATCH below + a new
_cancel_<kind>() function. The API layer doesn't change.
"""
from __future__ import annotations

import logging
import os
import signal
from typing import Callable

from backend.services.job_types import JobKind

logger = logging.getLogger(__name__)


def _cancel_task(native_id: str) -> bool:
    """Cancel a Task row.

    Two cases: (a) Task ran via Celery (job_id == celery_task_id) → use
    Celery's revoke transport; (b) Task ran via the legacy threaded
    executor → mark the row cancelled and trust the executor's poll loop
    to notice on next iteration. The legacy path is best-effort; users
    on it may need to wait up to one poll cycle for the cancel to land.
    """
    try:
        from backend.models import Task as DBTask, db
    except ImportError:
        return False

    task = db.session.get(DBTask, int(native_id))
    if task is None:
        return False
    if task.status in ("completed", "failed", "cancelled"):
        # Already terminal — nothing to cancel, but caller's intent is satisfied.
        return True

    # (a) Try Celery revoke first if the Task carries a celery task id.
    celery_task_id = getattr(task, "job_id", None)
    if celery_task_id:
        try:
            from backend.celery_app import celery
            celery.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
            logger.info("cancel_task: revoked Celery task %s for Task #%s", celery_task_id, task.id)
        except Exception as e:
            logger.warning("cancel_task: Celery revoke failed for %s (%s); falling back to flag-only", celery_task_id, e)

    # (b) Always mark the row cancelled too — the legacy executor polls
    # this column to know when to bail; the Celery path also benefits as
    # a final cleanup if revoke arrived after the worker had already
    # picked up the task.
    try:
        task.status = "cancelled"
        db.session.commit()
        return True
    except Exception as e:
        logger.exception("cancel_task: DB commit failed for Task #%s: %s", task.id, e)
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


def _cancel_training(native_id: str) -> bool:
    """Cancel a TrainingJob — PID SIGTERM if known, else mark + Celery revoke."""
    try:
        from backend.models import TrainingJob, db
    except ImportError:
        return False

    job = db.session.get(TrainingJob, int(native_id))
    if job is None:
        return False
    if job.status in ("completed", "failed", "cancelled"):
        return True

    pid = getattr(job, "pid", None)
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
            logger.info("cancel_training: SIGTERM'd pid %s for TrainingJob %s", pid, job.id)
        except (ProcessLookupError, ValueError):
            logger.info("cancel_training: pid %s already gone for TrainingJob %s", pid, job.id)
        except Exception as e:
            logger.warning("cancel_training: SIGTERM failed for pid %s (%s)", pid, e)

    if job.celery_task_id:
        try:
            from backend.celery_app import celery
            celery.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception as e:
            logger.warning("cancel_training: Celery revoke failed (%s)", e)

    try:
        job.status = "cancelled"
        if hasattr(job, "error_message"):
            job.error_message = "Cancelled by user"
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def _cancel_unified_progress(native_id: str) -> bool:
    """Cancel an in-memory UnifiedProgress process via the existing transport."""
    try:
        from backend.utils.unified_progress_system import get_unified_progress
    except ImportError:
        return False
    try:
        ups = get_unified_progress()
        if hasattr(ups, "cancel_process"):
            ups.cancel_process(native_id, "CANCELLED")
            return True
    except Exception as e:
        logger.warning("cancel_unified_progress: %s failed (%s)", native_id, e)
    return False


def _cancel_self_improvement(native_id: str) -> bool:
    """Self-improvement runs are cooperative — flag the row and let the
    runner notice. If a celery task id is on the row we revoke it too."""
    try:
        from backend.models import SelfImprovementRun, db
    except ImportError:
        return False
    row = db.session.get(SelfImprovementRun, int(native_id))
    if row is None:
        return False
    try:
        if hasattr(row, "status"):
            row.status = "cancelled"
        if hasattr(row, "celery_task_id") and row.celery_task_id:
            try:
                from backend.celery_app import celery
                celery.control.revoke(row.celery_task_id, terminate=True)
            except Exception:
                pass
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def _cancel_experiment(native_id: str) -> bool:
    try:
        from backend.models import ExperimentRun, db
    except ImportError:
        return False
    row = db.session.get(ExperimentRun, int(native_id))
    if row is None:
        return False
    try:
        if hasattr(row, "status"):
            row.status = "cancelled"
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def _cancel_demo(native_id: str) -> bool:
    """Demo steps aren't user-cancellable in the current product. Caller
    should not have shown the cancel button (Job.cancellable=False);
    return False so an accidental call surfaces clearly."""
    return False


def _cancel_batch_csv(native_id: str) -> bool:
    """CSV batch — bare-SQL table; no cancel column today, so this is a
    no-op stub. Wire when batch_job_db.py grows a cancel flag (Phase 8
    of the unification handles batch_job_db migration)."""
    logger.info("cancel_batch_csv: stub — batch_csv kind not yet cancellable for %s", native_id)
    return False


def _cancel_video_render(native_id: str) -> bool:
    """Editor render — SIGTERM the ffmpeg subprocess. Lives in the editor
    plan (plans/2026-04-29-video-editor.md) which adds a render queue;
    until that ships, returning False is the honest answer."""
    logger.info("cancel_video_render: stub — video editor render queue lands later")
    return False


CANCEL_DISPATCH: dict[JobKind, Callable[[str], bool]] = {
    JobKind.TASK: _cancel_task,
    JobKind.TRAINING: _cancel_training,
    JobKind.SELF_IMPROVEMENT: _cancel_self_improvement,
    JobKind.EXPERIMENT: _cancel_experiment,
    JobKind.DEMO: _cancel_demo,
    JobKind.BATCH_CSV: _cancel_batch_csv,
    JobKind.VIDEO_RENDER: _cancel_video_render,
    JobKind.UNIFIED_PROGRESS: _cancel_unified_progress,
}


def cancel_job(kind: JobKind, native_id: str) -> bool:
    """Dispatch a cancel by kind. Returns True on success, False otherwise.

    Never raises — this is called from an HTTP handler that needs JSON
    out, never an exception. Errors are logged.
    """
    fn = CANCEL_DISPATCH.get(kind)
    if fn is None:
        logger.warning("cancel_job: no transport for kind %s", kind)
        return False
    try:
        return bool(fn(native_id))
    except Exception as e:
        logger.exception("cancel_job: transport for %s:%s raised %s", kind.value, native_id, e)
        return False
