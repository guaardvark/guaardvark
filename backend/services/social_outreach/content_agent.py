"""
Content agent — Phase 2 of the multi-agent outreach pipeline.

Walks status="candidate" rows produced by Recon, asks the LLM (via the
existing persona helpers) to draft a comment that fits the thread and
the feature hint, grades the draft, and transitions the row's status:
  - candidate → drafted (grade ≥ MIN_GRADE)
  - candidate → rejected (grade < MIN_GRADE, or empty draft, or error)

Self-contained: reads everything from the candidate row's draft_text JSON
payload (which Recon populates with title, selftext_preview, top_comments,
feature_hint). No live Reddit fetch — that was Recon's job.

Doesn't post. Doesn't call the servo. The servo path is Phase 3 (Outreach),
which already exists in tick_process_approved_drafts.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from backend.services.social_outreach import audit, persona

logger = logging.getLogger(__name__)


MIN_GRADE = 0.7
"""Drafts below this score get rejected. Matches the threshold the existing
draft-comment endpoint uses for the would_post gate so we stay consistent."""

DEFAULT_BATCH_SIZE = 5
"""How many candidates one tick processes. Keep small — each draft is an LLM
call and we don't want a single tick blocking the worker for minutes."""


def _build_thread_context(payload: dict) -> str:
    """Reconstruct the same thread_context shape that reddit_outreach.draft_via_backend
    sends to /draft-comment. Recon stored title/selftext/top_comments inline so
    Content can rebuild the context without hitting Reddit again."""
    title = payload.get("title", "")
    selftext = payload.get("selftext_preview", "") or "(link-only post)"
    comments = payload.get("top_comments", []) or []
    return (
        f"TITLE: {title}\n\n"
        f"OP BODY:\n{selftext}\n\n"
        f"TOP COMMENTS:\n" + "\n---\n".join(comments[:5])
    )


class ContentAgent:
    """Stateless drafting agent. Each call processes one candidate row.

    Returns a small dict so the caller (celery tick) can roll up a batch
    summary without re-querying the DB.
    """

    def draft_candidate(self, audit_id: int) -> dict:
        """Draft this one candidate row. Returns {status, grade, reason}.

        On any failure path the row is moved out of "candidate" status
        (either to "drafted" with the new text, or "rejected" with the
        failure reason). A row that stayed "candidate" after this call is
        a bug.
        """
        from backend.models import SocialOutreachLog
        row = SocialOutreachLog.query.get(audit_id)
        if row is None:
            return {"status": "missing", "grade": None, "reason": f"audit_id {audit_id} not found"}
        if row.status != "candidate":
            return {
                "status": "skipped",
                "grade": None,
                "reason": f"already {row.status}, not candidate",
            }

        try:
            payload = json.loads(row.draft_text or "{}")
        except json.JSONDecodeError:
            audit.mark_rejected(audit_id, "draft_text JSON unparseable (legacy or corrupt row)")
            return {"status": "rejected", "grade": None, "reason": "json_decode_error"}

        feature_hint = payload.get("feature_hint")
        thread_context = _build_thread_context(payload)

        try:
            result = persona.draft_outreach_text(
                platform=row.platform,
                context={"thread_context": thread_context, "url": row.target_url},
                mode="comment" if row.action == "comment" else "share",
                feature_hint=feature_hint,
            )
        except Exception as e:
            logger.warning("ContentAgent.draft_candidate %s: persona call raised: %s", audit_id, e)
            audit.mark_rejected(audit_id, f"draft_call_failed: {e}")
            return {"status": "rejected", "grade": None, "reason": "draft_call_failed"}

        draft_text = (result.get("draft") or "").strip()
        grade = float(result.get("grade") or 0.0)

        if not draft_text:
            audit.mark_rejected(audit_id, "empty draft from LLM")
            return {"status": "rejected", "grade": grade, "reason": "empty_draft"}

        if grade < MIN_GRADE:
            audit.mark_rejected(audit_id, f"grade_too_low:{grade:.2f}")
            return {"status": "rejected", "grade": grade, "reason": "grade_too_low"}

        # UTM-tag any guaardvark.com links the LLM wrote, same as the
        # existing /draft-comment endpoint does. Tagging at the draft
        # boundary catches every URL — including ones the user may later
        # edit into the draft via the UI.
        posted_text = persona.apply_utm_tags(
            draft_text, platform=row.platform, campaign="v253",
        )

        # Store posted_text alongside the draft so the Outreach agent
        # doesn't have to re-tag at servo time.
        from backend.models import db
        row.draft_text = draft_text
        row.posted_text = posted_text
        row.grade_score = grade
        row.status = "drafted"
        db.session.commit()

        return {"status": "drafted", "grade": grade, "reason": None}

    def draft_batch(self, batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
        """Walk the oldest N candidate rows and draft each. Returns a summary.

        Stops at batch_size to keep individual ticks bounded — a celery beat
        every few minutes will drain the queue eventually.
        """
        from backend.models import SocialOutreachLog
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "candidate")
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(batch_size)
            .all()
        )
        report = {
            "considered": len(rows),
            "drafted": 0,
            "rejected": 0,
            "errors": 0,
        }
        for row in rows:
            outcome = self.draft_candidate(row.id)
            status = outcome["status"]
            if status == "drafted":
                report["drafted"] += 1
            elif status == "rejected":
                report["rejected"] += 1
            else:
                report["errors"] += 1
        return report
