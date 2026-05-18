"""Job history recording.

Writes a row to `job_history` whenever a Job reaches a terminal status
(completed / failed / cancelled). The unified emit boundary in
unified_progress_system._emit_event calls record_terminal_job on every
emission; this module dedupes (by Job.id) and persists.

Reads happen via /api/jobs/history (lands alongside this module — see
unified_jobs_resource_api.py extension below).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from backend.services.job_types import Job, JobStatus

logger = logging.getLogger(__name__)


def record_terminal_job(job: Job) -> Optional[str]:
    """If `job` is in terminal status, persist it to job_history.

    Idempotent: if a row already exists for `job.id`, updates it in place
    rather than inserting again. The same Job can emit terminal status
    multiple times (e.g. retry sequences) and we want the most recent
    snapshot, not duplicate rows.

    Returns the persisted id on success, None if the job wasn't terminal
    or persistence failed (failures are logged, never raised — this is
    a fire-and-forget call from the emit hot path).
    """
    if not job.status.is_terminal:
        return None

    try:
        from backend.models import JobHistory, db
        from flask import has_app_context, current_app
        if not has_app_context():
            # Running from a Celery worker without app context — skip the
            # DB write rather than crashing. The Flask process emit path
            # will record it when its own emission hits.
            logger.debug("record_terminal_job: no app context, skipping DB write for %s", job.id)
            return None
    except Exception as e:
        logger.warning("record_terminal_job: imports failed (%s); skipping", e)
        return None

    try:
        existing = db.session.get(JobHistory, job.id)
        finished = job.finished_at or datetime.now()
        payload = {
            "kind": job.kind.value,
            "native_id": str(job.native_id),
            "label": job.label or job.id,
            "status": job.status.value,
            "progress": job.progress,
            "started_at": job.started_at,
            "finished_at": finished,
            "duration_s": job.duration_s,
            "error_message": job.error_message,
            "parent_id": job.parent_id,
            "job_metadata": job.metadata or None,
        }

        if existing is None:
            row = JobHistory(id=job.id, **payload)
            db.session.add(row)
        else:
            for k, v in payload.items():
                setattr(existing, k, v)

        db.session.commit()
        return job.id
    except Exception as e:
        logger.warning("record_terminal_job: persist failed for %s (%s)", job.id, e)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def list_history(
    *,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Paginated history read for the Tasks/Jobs page History tab.

    Sort is finished_at DESC so the most recent terminal jobs appear first.
    Caller is responsible for clamping `limit` to a reasonable value; this
    function trusts what's passed.
    """
    try:
        from backend.models import JobHistory, db
    except Exception as e:
        logger.warning("list_history: imports failed (%s)", e)
        return []

    q = db.session.query(JobHistory).order_by(JobHistory.finished_at.desc())
    if kind:
        q = q.filter(JobHistory.kind == kind)
    if status:
        q = q.filter(JobHistory.status == status)

    rows = q.offset(offset).limit(limit).all()
    return [r.to_dict() for r in rows]
