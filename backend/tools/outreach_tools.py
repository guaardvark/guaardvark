#!/usr/bin/env python3
"""
Social Outreach Tools — chat-callable wrappers around the social_outreach
services. Each tool maps to a verb the user might say in chat:

  outreach_status         — "what's outreach doing right now?"
  outreach_list_queue     — "show me pending drafts"
  outreach_draft_post     — "draft a comment for <url>"
  outreach_approve_draft  — "approve draft 42"
  outreach_reject_draft   — "kill draft 42"
  outreach_run_pass       — "run a Reddit outreach pass" / "scout for candidates"

These call the same module functions the HTTP API does, so behavior matches
the OutreachPage button-for-button. Cadence + kill-switch + supervised mode
gates still apply downstream — none of these tools bypass them.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.social_outreach import audit, kill_switch, persona

logger = logging.getLogger(__name__)


# Shared platform vocabulary so the LLM doesn't invent values like "x.com"
_KNOWN_PLATFORMS = ("reddit", "discord", "facebook", "twitter", "youtube")
_KNOWN_RUN_PLATFORMS = ("reddit", "self_share", "recon", "draft")


def _row_summary(row) -> Dict[str, Any]:
    """Slim a SocialOutreachLog row down to what the LLM (and the user
    reading the tool card) actually needs. The full row has post-hoc fields
    that aren't useful in a chat answer."""
    return {
        "id": row.id,
        "platform": row.platform,
        "action": row.action,
        "status": row.status,
        "grade": row.grade_score,
        "target_url": row.target_url,
        "draft_text": (row.draft_text or "")[:400],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class OutreachStatusTool(BaseTool):
    """Snapshot of the outreach loop: enabled, supervised, cadence."""

    name = "outreach_status"
    description = (
        "Get the current state of the social outreach loop (enabled / supervised / "
        "cadence per platform). Use when the user asks 'is outreach on?', 'how many "
        "posts today?', or 'what's the outreach status?'."
    )
    parameters: Dict[str, ToolParameter] = {}

    def execute(self, **kwargs) -> ToolResult:
        try:
            payload = {
                "enabled": kill_switch.is_enabled(),
                "supervised": kill_switch.is_supervised(),
                "caps": {
                    "min_gap_seconds": kill_switch.CADENCE_MIN_GAP_SECONDS,
                    "daily_cap": kill_switch.CADENCE_DAILY_CAP,
                },
                "cadence": kill_switch.cadence_status(),
            }
            return ToolResult(success=True, output=payload, metadata=payload)
        except Exception as e:
            logger.exception("outreach_status failed")
            return ToolResult(success=False, error=str(e))


class OutreachListQueueTool(BaseTool):
    """List drafts waiting for approval (or recently approved/posted)."""

    name = "outreach_list_queue"
    description = (
        "List social outreach drafts. Defaults to status='drafted' (pending review). "
        "Pass status='approved' to see what's queued to post next, or status='posted' "
        "for recent history. Returns up to `limit` rows (default 10)."
    )
    parameters = {
        "status": ToolParameter(
            name="status", type="string", required=False,
            description="One of: drafted, approved, posted, rejected",
            default="drafted",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False,
            description="Max rows to return (1-50)", default=10,
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        status = (kwargs.get("status") or "drafted").strip().lower()
        try:
            limit = int(kwargs.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))

        try:
            from backend.models import SocialOutreachLog
            q = SocialOutreachLog.query
            if status:
                q = q.filter(SocialOutreachLog.status == status)
            rows = q.order_by(SocialOutreachLog.created_at.desc()).limit(limit).all()
            summary = [_row_summary(r) for r in rows]
            return ToolResult(
                success=True,
                output={"count": len(summary), "status": status, "rows": summary},
                metadata={"count": len(summary), "status": status},
            )
        except Exception as e:
            logger.exception("outreach_list_queue failed")
            return ToolResult(success=False, error=str(e))


class OutreachDraftPostTool(BaseTool):
    """Draft a single comment or share post and queue it for review."""

    name = "outreach_draft_post"
    description = (
        "Draft a social outreach comment or share post (does NOT post). "
        "Platforms: reddit, discord, facebook, twitter, youtube. "
        "For mode='comment' you must supply either thread_context (the OP/comment/video "
        "description) or target_url (we'll scout it — for YouTube URLs, scrapes the video "
        "title + description). For mode='share' supply share_target (e.g. 'r/SideProject') "
        "and optionally share_link (defaults to guaardvark.com). "
        "The draft lands in the queue at status='drafted' for human approval — nothing "
        "posts until the user approves it in the OutreachPage UI."
    )
    parameters = {
        "platform": ToolParameter(
            name="platform", type="string", required=True,
            description=f"One of: {', '.join(_KNOWN_PLATFORMS)}",
        ),
        "mode": ToolParameter(
            name="mode", type="string", required=False,
            description="'comment' or 'share'", default="comment",
        ),
        "thread_context": ToolParameter(
            name="thread_context", type="string", required=False,
            description="OP body + top comments concatenated (comment mode)",
        ),
        "target_url": ToolParameter(
            name="target_url", type="string", required=False,
            description="URL of the thread; if thread_context is missing we scout it",
        ),
        "share_target": ToolParameter(
            name="share_target", type="string", required=False,
            description="Where the share post goes, e.g. 'r/SideProject' (share mode)",
        ),
        "share_link": ToolParameter(
            name="share_link", type="string", required=False,
            description="Link to share; defaults to https://guaardvark.com",
        ),
        "tone": ToolParameter(
            name="tone", type="string", required=False,
            description="Optional tone preset: default, engaging, technical, casual, formal, humorous",
        ),
        "feature_hint": ToolParameter(
            name="feature_hint", type="string", required=False,
            description="Override auto-detected feature angle (e.g. 'video_gen', 'rag')",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        platform = (kwargs.get("platform") or "").strip().lower()
        if platform not in _KNOWN_PLATFORMS:
            return ToolResult(
                success=False,
                error=f"platform must be one of {_KNOWN_PLATFORMS}, got '{platform}'",
            )
        mode = (kwargs.get("mode") or "comment").strip().lower()
        target_url = kwargs.get("target_url")
        target_thread_id = None

        # Build context dict for persona.draft_outreach_text
        if mode == "share":
            share_target = (kwargs.get("share_target") or "").strip()
            if not share_target:
                return ToolResult(
                    success=False,
                    error="share mode requires share_target (e.g. 'r/SideProject')",
                )
            context = {
                "target": share_target,
                "link_url": (kwargs.get("share_link") or persona.SITE_URL),
            }
        else:
            thread_context = (kwargs.get("thread_context") or "").strip()
            # Convenience: if no thread_context but we got a URL, scout it the
            # same way the OutreachPage modal does. Saves the user from pasting.
            # Reddit goes through the JSON API (gets OP + top comments).
            # YouTube and everything else fall through to _scout_generic_url —
            # for YouTube watch pages this returns the video title + the
            # description's first paragraphs (YouTube serves OG metadata
            # server-side), which is enough context for the persona to draft a
            # comment that engages with the actual video topic.
            if not thread_context and target_url:
                try:
                    from backend.api.social_outreach_api import (
                        _scout_reddit_url, _scout_generic_url,
                    )
                    scouted = None
                    if "reddit.com" in (target_url or ""):
                        scouted = _scout_reddit_url(target_url)
                    if scouted is None:
                        result = _scout_generic_url(target_url)
                        # _scout_generic_url returns (dict, code) on error
                        if isinstance(result, tuple):
                            return ToolResult(
                                success=False,
                                error=f"scout failed: {result[0].get('error', 'unknown')}",
                            )
                        scouted = result
                    thread_context = scouted.get("thread_context") or ""
                    target_thread_id = scouted.get("target_thread_id")
                    # YouTube target_thread_id = video id from ?v= or /shorts/.
                    # Useful for dedupe so we don't draft on the same video twice.
                    if not target_thread_id and "youtube.com" in (target_url or ""):
                        import re
                        m = re.search(r"[?&]v=([\w-]{6,})", target_url)
                        if m:
                            target_thread_id = m.group(1)
                except Exception as e:
                    logger.warning("scout fallback failed: %s", e)
            if not thread_context:
                return ToolResult(
                    success=False,
                    error="comment mode needs thread_context or a target_url to scout",
                )
            context = {"thread_context": thread_context, "url": target_url}

        try:
            result = persona.draft_outreach_text(
                platform=platform,
                context=context,
                tone=kwargs.get("tone"),
                mode=mode,
                feature_hint=kwargs.get("feature_hint"),
            )
        except Exception as e:
            logger.exception("draft_outreach_text failed")
            return ToolResult(success=False, error=f"LLM draft failed: {e}")

        draft_text = (result.get("draft") or "").strip()
        grade = float(result.get("grade") or 0.0)
        reason = result.get("reason") or ""

        # Persist the same way /draft-comment does so the OutreachPage queue
        # picks the row up immediately. Tag source so we can tell chat-driven
        # drafts apart from cron-driven ones.
        audit_id = audit.log_outreach_event(
            platform=platform,
            action="comment" if mode == "comment" else "share",
            target_url=target_url,
            target_thread_id=target_thread_id,
            draft_text=draft_text,
            status="drafted",
            grade_score=grade,
            extra={"reason": reason, "source": "chat_tool"},
        )

        return ToolResult(
            success=bool(draft_text),
            output={
                "audit_id": audit_id,
                "platform": platform,
                "mode": mode,
                "draft": draft_text,
                "grade": grade,
                "reason": reason,
                "queued_status": "drafted",
            },
            metadata={"audit_id": audit_id, "grade": grade},
        )


class OutreachApproveDraftTool(BaseTool):
    """Approve a queued draft so the next outreach pass posts it."""

    name = "outreach_approve_draft"
    description = (
        "Approve an outreach draft by id. Optionally pass draft_text to overwrite "
        "the text before approving (handy when the user asks to tweak it). "
        "Approved drafts post on the next Celery tick, subject to cadence + kill switch."
    )
    requires_approval = True  # surfaces a confirmation card in the chat UI
    parameters = {
        "id": ToolParameter(
            name="id", type="int", required=True,
            description="SocialOutreachLog row id (from outreach_list_queue)",
        ),
        "draft_text": ToolParameter(
            name="draft_text", type="string", required=False,
            description="Optional replacement text before approving",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        try:
            event_id = int(kwargs.get("id"))
        except (TypeError, ValueError):
            return ToolResult(success=False, error="id must be an integer")

        try:
            from backend.models import SocialOutreachLog, db
            row = SocialOutreachLog.query.get(event_id)
            if row is None:
                return ToolResult(success=False, error=f"draft {event_id} not found")
            if "draft_text" in kwargs and kwargs["draft_text"] is not None:
                row.draft_text = str(kwargs["draft_text"])
            row.status = "approved"
            db.session.commit()
            return ToolResult(
                success=True,
                output=_row_summary(row),
                metadata={"id": row.id, "status": row.status},
            )
        except Exception as e:
            logger.exception("outreach_approve_draft failed")
            return ToolResult(success=False, error=str(e))


class OutreachRejectDraftTool(BaseTool):
    """Reject a queued draft so it never posts."""

    name = "outreach_reject_draft"
    description = (
        "Reject an outreach draft by id. Marks the row 'rejected' so it won't post. "
        "Use when the user says 'kill that one', 'don't post draft 42', etc."
    )
    parameters = {
        "id": ToolParameter(
            name="id", type="int", required=True,
            description="SocialOutreachLog row id",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        try:
            event_id = int(kwargs.get("id"))
        except (TypeError, ValueError):
            return ToolResult(success=False, error="id must be an integer")

        try:
            from backend.models import SocialOutreachLog, db
            row = SocialOutreachLog.query.get(event_id)
            if row is None:
                return ToolResult(success=False, error=f"draft {event_id} not found")
            row.status = "rejected"
            db.session.commit()
            return ToolResult(
                success=True,
                output=_row_summary(row),
                metadata={"id": row.id, "status": row.status},
            )
        except Exception as e:
            logger.exception("outreach_reject_draft failed")
            return ToolResult(success=False, error=str(e))


class OutreachRunPassTool(BaseTool):
    """Trigger an outreach Celery pass on demand."""

    name = "outreach_run_pass"
    description = (
        "Queue an outreach pass without waiting for the cron. platform options: "
        "'reddit' (engage with a sub — pass subreddit to target one, omit for "
        "round-robin), 'self_share' (link post to next round-robin sub), "
        "'recon' (scout candidates only, never posts), 'draft' (LLM-draft any "
        "candidate rows). Returns the Celery task id. Cadence + kill switch still apply."
    )
    requires_approval = True
    parameters = {
        "platform": ToolParameter(
            name="platform", type="string", required=True,
            description=f"One of: {', '.join(_KNOWN_RUN_PLATFORMS)}",
        ),
        "subreddit": ToolParameter(
            name="subreddit", type="string", required=False,
            description="Target subreddit name (without r/) for platform='reddit'",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        platform = (kwargs.get("platform") or "").strip().lower()
        if platform not in _KNOWN_RUN_PLATFORMS:
            return ToolResult(
                success=False,
                error=f"platform must be one of {_KNOWN_RUN_PLATFORMS}, got '{platform}'",
            )
        if not kill_switch.is_enabled():
            return ToolResult(
                success=False,
                error="outreach is disabled (kill switch is off). Flip it on from /outreach first.",
            )

        subreddit = (kwargs.get("subreddit") or "").strip() or None

        try:
            if platform == "reddit":
                from backend.tasks.social_outreach_tasks import (
                    engage_with_subreddit, tick_reddit_outreach,
                )
                async_result = (
                    engage_with_subreddit.delay(subreddit) if subreddit
                    else tick_reddit_outreach.delay()
                )
                msg = (
                    f"Reddit pass queued for r/{subreddit}." if subreddit
                    else "Reddit pass queued (round-robin)."
                )
            elif platform == "self_share":
                from backend.tasks.social_outreach_tasks import tick_self_share
                async_result = tick_self_share.delay()
                msg = "Self-share pass queued."
            elif platform == "recon":
                from backend.tasks.social_outreach_tasks import tick_recon_reddit
                async_result = tick_recon_reddit.delay()
                msg = "Recon pass queued (scout only, no posting)."
            else:  # 'draft'
                from backend.tasks.social_outreach_tasks import tick_draft_candidates
                async_result = tick_draft_candidates.delay()
                msg = "Content pass queued (drafting candidate rows)."

            return ToolResult(
                success=True,
                output={
                    "task_id": async_result.id,
                    "platform": platform,
                    "subreddit": subreddit,
                    "message": msg,
                },
                metadata={"task_id": async_result.id, "platform": platform},
            )
        except Exception as e:
            logger.exception("outreach_run_pass failed")
            return ToolResult(success=False, error=str(e))


__all__ = [
    "OutreachStatusTool",
    "OutreachListQueueTool",
    "OutreachDraftPostTool",
    "OutreachApproveDraftTool",
    "OutreachRejectDraftTool",
    "OutreachRunPassTool",
]
