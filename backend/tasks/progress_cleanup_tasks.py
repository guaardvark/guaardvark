"""Remove progress-store entries whose job died without finishing.

``<OUTPUT_DIR>/.progress_jobs/<job_id>/`` outlives a job when the process
that owned it was killed before it could mark the job complete or failed; the
Jobs page then keeps showing work nothing is doing. The rules live in
``scripts/cleanup_stuck_progress_jobs.py`` (also runnable by hand); this task
runs them on a schedule. Finished jobs are left alone: they are the history.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from celery import shared_task

logger = logging.getLogger(__name__)

STUCK_AFTER_HOURS = 24


def _script_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "cleanup_stuck_progress_jobs.py"
    spec = importlib.util.spec_from_file_location("cleanup_stuck_progress_jobs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@shared_task(name="maintenance.cleanup_stuck_progress_jobs")
def cleanup_stuck_progress_jobs(max_age_hours: int = STUCK_AFTER_HOURS) -> dict:
    """Remove jobs that have shown no sign of life for ``max_age_hours``."""
    try:
        mod = _script_module()
        progress_dir = mod.resolve_progress_dir()
        removable, kept = mod.classify_jobs(progress_dir, max_age_hours=max_age_hours, clean_completed=False)
        removed = mod.remove_jobs(removable) if removable else 0
        if removed:
            logger.info("[PROGRESS] Removed %d stuck job entries from %s", removed, progress_dir)
        return {"status": "ok", "removed": removed, "kept": len(kept)}
    except Exception as exc:
        logger.warning("[PROGRESS] Stuck-job cleanup failed: %s", exc)
        return {"status": "error", "error": str(exc)}
