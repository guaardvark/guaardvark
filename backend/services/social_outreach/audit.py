"""
Append-only audit log for every outreach action — drafts, posts, aborts.

Two sinks: jsonl on disk (survives DB nukes) + SocialOutreachLog rows (queryable).
Both are written for every event. If one sink fails the other still records.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from backend.utils.clock import utcnow

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
        "ts": utcnow().isoformat() + "Z",
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
        "ts": utcnow().isoformat() + "Z",
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


def latest_id() -> int:
    """The newest outreach row's id (0 when there is none). Rows added after
    the call have a higher one."""
    try:
        from sqlalchemy import func

        from backend.models import SocialOutreachLog, db
        return int(db.session.query(func.max(SocialOutreachLog.id)).scalar() or 0)
    except Exception as e:
        logger.error("latest outreach row id unavailable: %s", e)
        return 0


def _detached_audit_session():
    """Build a Session bound directly to the engine, isolated from the caller's
    db.session. Same pattern log_outreach_event uses — keeps audit writes from
    accidentally flushing the caller's pending ORM mutations.
    """
    from sqlalchemy.orm import Session
    from backend.models import db
    return Session(db.engine)


# The longest original shown beside a draft; a post longer than this is cut.
SOURCE_TEXT_MAX = 4000


def source_of(payload: dict) -> Optional[str]:
    """What a candidate answers, as the JSON kept in ``source_json``: the
    post's title and body or the comment's text, who wrote it, where, and why
    recon picked it. None when the payload names nothing to show (a share)."""
    if not isinstance(payload, dict):
        return None
    text = (
        payload.get("incoming_text")
        or payload.get("source_text")
        or payload.get("selftext_preview")
        or payload.get("snippet")
        or ""
    )
    title = payload.get("title") or ""
    if not (str(text).strip() or str(title).strip()):
        return None
    source = {
        "title": str(title).strip(),
        "text": str(text).strip()[:SOURCE_TEXT_MAX],
        "author": str(payload.get("incoming_author") or payload.get("author") or "").strip(),
        "community": str(payload.get("subreddit") or payload.get("community") or "").strip(),
        "why": str(payload.get("relevance_reason") or "").strip(),
    }
    return json.dumps(source, ensure_ascii=False)


def mark_drafted_from_candidate(
    audit_id: int,
    draft_text: str,
    grade_score: Optional[float] = None,
    posted_text: Optional[str] = None,
    status: str = "drafted",
) -> bool:
    """Promote a candidate row to drafted (or approved) — Content agent writes
    the real text over the JSON recon payload, replaces score with draft grade.
    `posted_text` is the UTM-tagged version Content prepared so Phase 3
    doesn't have to re-tag at servo time.

    ``status`` is ``\"drafted\"`` or ``\"approved\"``; the Content agent
    passes drafted, since every draft waits for a person's approval.
    """
    if status not in ("drafted", "approved"):
        status = "drafted"
    try:
        from backend.models import SocialOutreachLog
        with _detached_audit_session() as s:
            row = s.get(SocialOutreachLog, audit_id)
            if row is None or row.status != "candidate":
                return False
            try:
                row.source_json = source_of(json.loads(row.draft_text or "{}"))
            except ValueError:
                pass
            row.status = status
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


def mark_rejected(audit_id: int, reason: str, draft: Optional[str] = None) -> bool:
    """Mark a candidate or draft as rejected (Content agent's grade too low,
    sub bans self-promo discovered late, etc.). Preserves draft_text for audit.

    ``draft`` is the text that was written and turned away; it is kept in the
    candidate's payload as ``rejected_draft`` so a person can read what the
    checks refused.
    """
    try:
        from backend.models import SocialOutreachLog
        with _detached_audit_session() as s:
            row = s.get(SocialOutreachLog, audit_id)
            if row is None:
                return False
            if draft and row.status == "candidate":
                try:
                    payload = json.loads(row.draft_text or "{}")
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    payload["rejected_draft"] = draft
                    row.draft_text = json.dumps(payload, ensure_ascii=False)
            row.status = "rejected"
            row.abort_reason = reason
            s.commit()
        return True
    except Exception as e:
        logger.error("mark_rejected failed for audit_id %s: %s", audit_id, e)
        return False


def set_media(audit_id: int, media: Optional[dict]) -> bool:
    """Set (or with None, remove) the image a row's post carries."""
    try:
        from backend.models import SocialOutreachLog
        with _detached_audit_session() as s:
            row = s.get(SocialOutreachLog, audit_id)
            if row is None:
                return False
            row.media_json = json.dumps(media, ensure_ascii=False) if media else None
            s.commit()
        return True
    except Exception as e:
        logger.error("set_media failed for audit_id %s: %s", audit_id, e)
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
        cutoff = utcnow() - timedelta(hours=hours)
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
