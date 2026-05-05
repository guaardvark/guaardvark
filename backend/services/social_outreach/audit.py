"""
Append-only audit log for every outreach action — drafts, posts, aborts.

Two sinks: jsonl on disk (survives DB nukes) + SocialOutreachLog rows (queryable).
Both are written for every event. If one sink fails the other still records.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

AUDIT_DIR = Path(os.environ.get("GUAARDVARK_ROOT", "/home/llamax1/LLAMAX8")) / "data" / "social_outreach"
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"


def log_outreach_event(
    platform: str,
    action: str,
    target_url: Optional[str] = None,
    target_thread_id: Optional[str] = None,
    draft_text: Optional[str] = None,
    posted_text: Optional[str] = None,
    status: str = "drafted",
    grade_score: Optional[float] = None,
    abort_reason: Optional[str] = None,
    task_id: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """
    Record one outreach event. Returns the SocialOutreachLog row id, or None
    if the DB write failed.

    The jsonl write happens first and is fsync'd — even if the DB insert blows
    up we still have the trail.
    """
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    record = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "platform": platform,
        "action": action,
        "status": status,
        "target_url": target_url,
        "target_thread_id": target_thread_id,
        "draft_text": draft_text,
        "posted_text": posted_text,
        "grade_score": grade_score,
        "abort_reason": abort_reason,
        "task_id": task_id,
        "extra": extra or {},
    }

    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as e:
        logger.error("audit jsonl write failed: %s", e)

    row_id: Optional[int] = None
    try:
        from sqlalchemy.orm import Session

        from backend.models import SocialOutreachLog, db

        # Old version used `db.session.begin_nested()` + `db.session.commit()`,
        # which committed the *caller's* outer transaction as a side-effect of
        # logging — a partial batch update or an in-flight ORM mutation in the
        # request handler would leak to disk on every audit call. Use a detached
        # session bound to the engine so audit writes are fully isolated from
        # whatever the caller is doing.
        with Session(db.engine) as audit_session:
            row = SocialOutreachLog(
                platform=platform,
                action=action,
                target_url=target_url,
                target_thread_id=target_thread_id,
                draft_text=draft_text,
                posted_text=posted_text,
                status=status,
                grade_score=grade_score,
                abort_reason=abort_reason,
                task_id=task_id,
            )
            audit_session.add(row)
            audit_session.commit()
            row_id = row.id
    except Exception as e:
        logger.error("audit DB write failed: %s", e)
        # The detached session context manager rolls back automatically on
        # exception; nothing to clean up here.
        # and destroy the caller's pending changes.

    return row_id


def recent_thread_ids(platform: str, hours: int = 168) -> set[str]:
    """Thread IDs we've already touched in the window. Used for dedupe."""
    from datetime import timedelta
    try:
        from backend.models import SocialOutreachLog
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.platform == platform)
            .filter(SocialOutreachLog.created_at >= cutoff)
            .filter(SocialOutreachLog.status == "posted")
            .filter(SocialOutreachLog.target_thread_id.isnot(None))
            .all()
        )
        return {r.target_thread_id for r in rows if r.target_thread_id}
    except Exception as e:
        logger.warning("recent_thread_ids fallback to empty: %s", e)
        return set()
