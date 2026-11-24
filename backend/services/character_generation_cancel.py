"""Cancel in-flight Cast Studio character sample generation.

Mirrors ``cancel_lora_train``: best-effort revoke of Celery + unified progress
and a cooperative stop via SubjectSample status. The render loop polls that
status while ComfyUI samples and stops its own prompt (queue delete, then an
interrupt scoped to its prompt id), so other ComfyUI work keeps running.
"""
from __future__ import annotations

import logging

from backend.models import db, Subject, SubjectSample

log = logging.getLogger(__name__)

_CHAR_GEN_OPS = frozenset({"generate_samples", "regen_sample"})


def _find_active_jobs(subject_id: int) -> list[tuple[str, dict]]:
    """Return (job_id, additional_data) for in-flight char-gen jobs on this subject."""
    from backend.utils.unified_progress_system import ProcessStatus, get_unified_progress

    ups = get_unified_progress()
    matches: list[tuple[str, dict]] = []
    terminal = {
        ProcessStatus.COMPLETE,
        ProcessStatus.ERROR,
        ProcessStatus.CANCELLED,
    }
    for job_id, event in ups.get_active_processes().items():
        if event.status in terminal:
            continue
        ad = event.additional_data or {}
        if str(ad.get("subject_id") or "") != str(subject_id):
            continue
        op = ad.get("operation") or ""
        kind = ad.get("kind") or ""
        if op in _CHAR_GEN_OPS or kind == "cast_character_gen":
            matches.append((job_id, ad))
    return matches


def cancel_character_generation(subject_id: int) -> dict:
    """Best-effort cancel of character sample generation / regen for a subject.

    Returns ``{"cancelled": True, ...}`` when there was something to stop, or
    ``{"cancelled": False, "reason": ...}`` when nothing was in flight.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return {"cancelled": False, "reason": "not_found"}

    in_flight = (
        SubjectSample.query
        .filter(
            SubjectSample.subject_id == subject_id,
            SubjectSample.status.in_(("pending", "generating")),
        )
        .all()
    )
    jobs = _find_active_jobs(subject_id)

    if not in_flight and not jobs:
        return {"cancelled": False, "reason": "not_generating", "subject_id": subject_id}

    revoked = []
    cancelled_jobs = []
    for job_id, ad in jobs:
        celery_task_id = ad.get("celery_task_id")
        if celery_task_id:
            try:
                from backend.celery_app import celery
                celery.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
                revoked.append(celery_task_id)
                log.info(
                    "character_generation_cancel: revoked Celery task %s (job %s)",
                    celery_task_id, job_id,
                )
            except Exception as e:
                log.warning(
                    "character_generation_cancel: Celery revoke failed for %s (%s)",
                    celery_task_id, e,
                )
        try:
            from backend.utils.unified_progress_system import get_unified_progress
            get_unified_progress().cancel_process(job_id, "Cancelled by user")
            cancelled_jobs.append(job_id)
        except Exception as e:
            log.warning(
                "character_generation_cancel: cancel_process failed for %s (%s)",
                job_id, e,
            )

    # Cooperative stop signal the generate loop watches between (and after) shots.
    marked = 0
    for row in in_flight:
        row.status = "cancelled"
        marked += 1
    db.session.commit()

    return {
        "cancelled": True,
        "subject_id": subject_id,
        "samples_marked": marked,
        "jobs_cancelled": cancelled_jobs,
        "celery_revoked": revoked,
    }


def job_is_cancelled(job_id: str | None) -> bool:
    """True when the unified-progress job was cancelled (file-aware for workers)."""
    if not job_id:
        return False
    try:
        from backend.utils.unified_progress_system import ProcessStatus, get_unified_progress

        ups = get_unified_progress()
        # Reload from disk so an API-side cancel is visible inside the Celery worker.
        try:
            ups._load_process_from_file(job_id)  # noqa: SLF001 — intentional cross-process read
        except Exception:
            pass
        proc = ups.get_process(job_id)
        if proc is None:
            return False
        return proc.status == ProcessStatus.CANCELLED
    except Exception:
        return False
