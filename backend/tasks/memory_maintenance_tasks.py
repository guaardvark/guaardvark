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
