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

_REPO_ROOT = Path(os.environ.get("GUAARDVARK_ROOT") or Path(__file__).resolve().parents[3])
AUDIT_DIR = _REPO_ROOT / "data" / "social_outreach"
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


def log_trail_only(
    platform: str,
    event: str,
    target_url: Optional[str] = None,
    target_thread_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Append a marker to the audit jsonl WITHOUT writing a DB row.

    Used for lifecycle signals that aren't full audit events — e.g. the
    Recon-stage payload (feature_hint, title, sub) that gets overwritten
    when Content promotes candidate→drafted, but is worth keeping for
    analytics. Re-fetchable by grepping the jsonl by event name.
    """
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "platform": platform,
        "event": event,
        "target_url": target_url,
        "target_thread_id": target_thread_id,
        "extra": extra or {},
    }
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as e:
        logger.error("audit jsonl trail write failed: %s", e)


def log_candidate(
    platform: str,
    action: str,  # "comment" or "share"
    target_url: str,
    target_thread_id: str,
    feature_hint: str,
    score: Optional[float] = None,
    extras: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """Record a Recon-stage candidate. Status is "candidate" until the Content
    agent drafts it. draft_text holds the recon payload as JSON so we don't
    need a schema migration — feature_hint, score, optional title_seed/snippet
    all live in there.
    """
    payload = {"feature_hint": feature_hint, "stage": "recon"}
    if extras:
        payload.update(extras)
    return log_outreach_event(
        platform=platform,
        action=action,
        target_url=target_url,
        target_thread_id=target_thread_id,
        draft_text=json.dumps(payload, ensure_ascii=False),
        status="candidate",
        grade_score=score,
    )


def _detached_audit_session():
    """Build a Session bound directly to the engine, isolated from the caller's
    db.session. Same pattern log_outreach_event uses — keeps audit writes from
    accidentally flushing the caller's pending ORM mutations.
    """
    from sqlalchemy.orm import Session
    from backend.models import db
    return Session(db.engine)


def mark_drafted_from_candidate(
    audit_id: int,
    draft_text: str,
    grade_score: Optional[float] = None,
    posted_text: Optional[str] = None,
) -> bool:
    """Promote a candidate row to drafted — Content agent writes the real
    text over the JSON recon payload, replaces score with draft grade.
    `posted_text` is the UTM-tagged version Content prepared so Phase 3
    doesn't have to re-tag at servo time.
    """
    try:
        from backend.models import SocialOutreachLog
        with _detached_audit_session() as s:
            row = s.get(SocialOutreachLog, audit_id)
            if row is None or row.status != "candidate":
                return False
            row.status = "drafted"
            row.draft_text = draft_text
            if grade_score is not None:
                row.grade_score = grade_score
            if posted_text is not None:
                row.posted_text = posted_text
            s.commit()
        return True
    except Exception as e:
        logger.error("mark_drafted_from_candidate failed for audit_id %s: %s", audit_id, e)
        return False


def mark_rejected(audit_id: int, reason: str) -> bool:
    """Mark a candidate or draft as rejected (Content agent's grade too low,
    sub bans self-promo discovered late, etc.). Preserves draft_text for audit.
    """
    try:
        from backend.models import SocialOutreachLog
        with _detached_audit_session() as s:
            row = s.get(SocialOutreachLog, audit_id)
            if row is None:
                return False
            row.status = "rejected"
            row.abort_reason = reason
            s.commit()
        return True
    except Exception as e:
        logger.error("mark_rejected failed for audit_id %s: %s", audit_id, e)
        return False


def mark_draft_aborted(audit_id: int, abort_reason: str) -> bool:
    """Update an existing drafted row to 'aborted' status.

    Preserves draft_text so the UI can show + manually-recover the draft.
    Returns True if the row was found and updated, False if not.
    """
    try:
        from backend.models import SocialOutreachLog
        with _detached_audit_session() as s:
            row = s.get(SocialOutreachLog, audit_id)
            if row is None:
                return False
            row.status = "aborted"
            row.abort_reason = abort_reason
            s.commit()
        return True
    except Exception as e:
        logger.error("mark_draft_aborted failed for audit_id %s: %s", audit_id, e)
        return False


def recent_thread_ids(
    platform: str,
    hours: int = 168,
    statuses: Optional[list[str]] = None,
) -> set[str]:
    """Thread IDs we've already touched in the window. Used for dedupe.

    Default keeps the legacy semantics: only "posted" threads are deduped, so
    Outreach won't re-comment on a thread it already commented on. Recon
    callers should pass statuses=["candidate","drafted","posted","approved"]
    so the scout phase doesn't re-emit the same thread it queued an hour ago.
    """
    from datetime import timedelta
    if statuses is None:
        statuses = ["posted"]
    try:
        from backend.models import SocialOutreachLog
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.platform == platform)
            .filter(SocialOutreachLog.created_at >= cutoff)
            .filter(SocialOutreachLog.status.in_(statuses))
            .filter(SocialOutreachLog.target_thread_id.isnot(None))
            .all()
        )
        return {r.target_thread_id for r in rows if r.target_thread_id}
    except Exception as e:
        logger.warning("recent_thread_ids fallback to empty: %s", e)
        return set()
