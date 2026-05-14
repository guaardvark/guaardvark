"""Periodic memory housekeeping.

Cleans up old per-session memory state rows from the `system_setting` table
(keys of shape `memory_state_<session_id>`). The MemoryManager writes these
rows on every turn via `persist_memory()`; without a sweeper they accumulate
for every session that ever existed. We let `cleanup_old_memory()` reap rows
that haven't been touched for `MEMORY_RETENTION_DAYS` (default 30).

Wired into Celery Beat from `backend/celery_app.py`.
"""

from __future__ import annotations

import logging
import os

from celery import shared_task

logger = logging.getLogger(__name__)


def _retention_days() -> int:
    try:
        return int(os.environ.get("GUAARDVARK_MEMORY_RETENTION_DAYS", "30"))
    except ValueError:
        return 30


@shared_task(name="memory.cleanup_old_session_state", bind=True)
def cleanup_old_session_memory(self, days: int | None = None) -> dict:
    """Walk `system_setting` and delete `memory_state_*` rows older than
    `days` (or `GUAARDVARK_MEMORY_RETENTION_DAYS`, or 30).

    Returns `{"deleted": <count>, "days": <retention>}` for visibility in
    Celery's task results store.
    """
    retention = days if days is not None else _retention_days()

    try:
        # The Flask app context is required because MemoryManager goes through
        # the Flask-SQLAlchemy session. Same pattern as social_outreach_tasks.
        from backend.app import app
    except Exception as e:
        logger.error(f"Could not import Flask app for memory cleanup: {e}")
        return {"deleted": 0, "days": retention, "error": "no_app_context"}

    with app.app_context():
        try:
            from backend.models import db
            from backend.utils.memory_manager import MemoryManager

            # cleanup_old_memory returns the actual deleted count from the same
            # transaction that did the delete — no race window between counting
            # and deleting, and the count reflects what really happened (was 0
            # on the error path before, now matches reality).
            mgr = MemoryManager(db_session=db.session)
            deleted = mgr.cleanup_old_memory(days=retention) or 0

            return {"deleted": deleted, "days": retention}
        except Exception as e:
            logger.error(f"Memory cleanup task failed: {e}", exc_info=True)
            return {"deleted": 0, "days": retention, "error": str(e)}


@shared_task(name="memory.reconcile_belief_updates", bind=True)
def reconcile_belief_updates(self, threshold: int | None = None) -> dict:
    """Stage PendingFix rows for any knowledge-file line that ≥N sessions have
    contradicted via belief_update memories.

    Wraps `lesson_reconciler.scan_belief_updates()`. Idempotent — the scan
    skips groups that already have an open PendingFix for the same (file,
    element). PendingFix rows are review-gated; nothing here writes to the
    knowledge files directly. The on-demand CLI / API entrypoints still work
    exactly as before; this task just makes the loop self-driving.

    Disable via env `GUAARDVARK_RECONCILER_BEAT_DISABLED=1` if you want the
    original opt-in cadence back without removing the schedule entry.

    Returns `{"proposals_created": <count>, "threshold": <int>}`.
    """
    if os.environ.get("GUAARDVARK_RECONCILER_BEAT_DISABLED", "").strip() in {"1", "true", "yes"}:
        return {"proposals_created": 0, "skipped": "disabled_by_env"}

    try:
        from backend.app import app
    except Exception as e:
        logger.error(f"Could not import Flask app for reconciler beat: {e}")
        return {"proposals_created": 0, "error": "no_app_context"}

    with app.app_context():
        try:
            from backend.services.lesson_reconciler import scan_belief_updates, DEFAULT_THRESHOLD
            t = threshold if threshold is not None else DEFAULT_THRESHOLD
            created = scan_belief_updates(threshold=t) or 0
            if created:
                logger.info(f"[RECONCILER-BEAT] staged {created} pending fix(es) at threshold={t}")
            return {"proposals_created": created, "threshold": t}
        except Exception as e:
            logger.error(f"Reconciler beat task failed: {e}", exc_info=True)
            return {"proposals_created": 0, "error": str(e)}
