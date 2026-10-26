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
the OutreachPage button-for-button. Every draft waits for a person's approval,
and cadence and the kill switch apply downstream — none of these tools bypass
them, and none of them starts a post.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.social_outreach import (
    audit,
    kill_switch,
    persona,
    platforms,
    profile,
    transitions,
)
from backend.utils.backend_http import BackendError, is_mcp_transport, request_json

logger = logging.getLogger(__name__)


# Shared platform vocabulary so the LLM doesn't invent values like "x.com".
# X and LinkedIn are not here: outreach never posts to them (platforms.REFUSED).
_KNOWN_PLATFORMS = ("reddit", "discord", "facebook", "youtube")
_KNOWN_RUN_PLATFORMS = (
    "reddit", "self_share", "recon", "draft", "youtube", "youtube_recon",
)
# Statuses outreach_list_queue lists oldest first: approved rows wait in
# created_at order, so the oldest has waited longest.
_OLDEST_FIRST_STATUSES = ("approved",)
# What outreach_draft_post can draft. The mode decides both the persona prompt
# and the queued row's action, so anything else ("reply", "comments") is
# refused rather than drafted as one thing and queued as another.
_DRAFT_MODES = ("comment", "share")
# Platforms a mode='share' draft can be posted to. The watched poster's share
# branch (watched_post._post) submits a link post to the subreddit named in
# the row's target_url and knows no other destination, so a share draft for
# any other platform would be approved and then aborted.
_SHARE_PLATFORMS = ("reddit",)

# A share_target is "r/SideProject", "/r/SideProject", "SideProject", or a
# reddit.com/r/SideProject URL.
_SUBREDDIT_SHORT_RE = re.compile(r"^/?(?:r/)?(?P<name>[A-Za-z0-9_]{2,21})/?$", re.IGNORECASE)
_SUBREDDIT_URL_RE = re.compile(
    r"^https?://(?:[a-z]+\.)?reddit\.com/r/(?P<name>[A-Za-z0-9_]{2,21})(?:[/?#].*)?$",
    re.IGNORECASE,
)


def _subreddit_name(share_target: str) -> Optional[str]:
    """The subreddit a share_target names, or None if it names none."""
    target = (share_target or "").strip()
    match = _SUBREDDIT_SHORT_RE.match(target) or _SUBREDDIT_URL_RE.match(target)
    return match.group("name") if match else None


def _share_title(draft_text: str) -> str:
    """The title the watched poster will submit for a share row's draft_text.

    Mirrors the share branch of watched_post._post: a JSON draft carries
    "title"; anything else is used whole.
    """
    try:
        payload = json.loads(draft_text or "{}")
    except json.JSONDecodeError:
        return (draft_text or "").strip()
    if not isinstance(payload, dict):
        return (draft_text or "").strip()
    return (payload.get("title") or "").strip()


def _row_summary(row) -> Dict[str, Any]:
    """Slim a SocialOutreachLog row down to what the LLM (and the user
    reading the tool card) actually needs. The full row has post-hoc fields
    that aren't useful in a chat answer. Accepts the model row or its
    to_dict() form, which is what the backend returns over HTTP."""
    if isinstance(row, dict):
        get = row.get
    else:
        def get(key):
            return getattr(row, key, None)
    created = get("created_at")
    return {
        "id": get("id"),
        "platform": get("platform"),
        "action": get("action"),
        "status": get("status"),
        "grade": get("grade_score"),
        "target_url": get("target_url"),
        "draft_text": (get("draft_text") or "")[:400],
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }


class OutreachStatusTool(BaseTool):
    """Snapshot of the outreach loop: enabled, supervised, cadence."""

    name = "outreach_status"
    read_only = True
    description = (
        "Get the current state of the social outreach loop (enabled / supervised / "
        "cadence per platform). Use when the user asks 'is outreach on?', 'how many "
        "posts today?', or 'what's the outreach status?'."
    )
    parameters: Dict[str, ToolParameter] = {}

    def execute(self, **kwargs) -> ToolResult:
        if is_mcp_transport(self):
            # The backend owns the settings table and the cadence counters;
            # this process has neither a database session nor the backend's
            # environment, and would report its own defaults as the answer.
            try:
                payload = request_json("GET", "/api/social-outreach/status").data
            except BackendError as e:
                return ToolResult(
                    success=False,
                    error=f"Could not read the outreach status: {e}",
                    metadata={"backend_error": e.kind},
                )
            if not isinstance(payload, dict):
                return ToolResult(success=False, error="The backend returned no outreach status.")
        else:
            try:
                payload = kill_switch.status_snapshot()
            except Exception as e:
                logger.exception("outreach_status failed")
                return ToolResult(success=False, error=str(e))

        if payload.get("settings_readable") is False:
            return ToolResult(
                success=False,
                error=(
                    "The backend could not read the outreach settings from its database, so "
                    "whether outreach is enabled is unknown. Nothing posts "
                    "while the settings are unreadable."
                ),
                metadata=payload,
            )
        return ToolResult(success=True, output=payload, metadata=payload)


class OutreachListQueueTool(BaseTool):
    """List drafts waiting for approval (or recently approved/posted)."""

    name = "outreach_list_queue"
    read_only = True
    description = (
        "List social outreach drafts by status. Defaults to status='drafted' (waiting "
        "for review). status='approved' lists what is queued to post, oldest first, "
        "which is the order it posts in; every other status is newest first, e.g. "
        "status='posted' for recent history. Returns up to `limit` rows (default 10)."
    )
    parameters = {
        "status": ToolParameter(
            name="status", type="string", required=False,
            description=(
                "candidate (found, not drafted yet), drafted (waiting for review), approved "
                "(waiting for the person to start the post, or for the Discord bot), "
                "processing (a poster has picked it up), submitting (being "
                "published now), posted, rejected, aborted (posting failed), unsupported "
                "(nothing here posts that action on that platform)"
            ),
            default="drafted", enum=list(transitions.KNOWN_STATUSES),
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False,
            description="Max rows to return (1-50)", default=10,
        ),
    }

    @staticmethod
    def _answer(status: str, summary: List[Dict[str, Any]]) -> ToolResult:
        order = "oldest_first" if status in _OLDEST_FIRST_STATUSES else "newest_first"
        return ToolResult(
            success=True,
            output={"count": len(summary), "status": status, "order": order, "rows": summary},
            metadata={"count": len(summary), "status": status},
        )

    def execute(self, **kwargs) -> ToolResult:
        status = str(kwargs.get("status") or "drafted").strip().lower()
        if status not in transitions.KNOWN_STATUSES:
            return ToolResult(
                success=False,
                error=(
                    f"status must be one of {transitions.KNOWN_STATUSES}, got '{status}'. "
                    "Drafts waiting for review are 'drafted'."
                ),
            )
        try:
            limit = int(kwargs.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))

        if is_mcp_transport(self):
            return self._list_via_backend(status, limit)

        try:
            from backend.models import SocialOutreachLog
            created = SocialOutreachLog.created_at
            rows = (
                SocialOutreachLog.query
                .filter(SocialOutreachLog.status == status)
                .order_by(created.asc() if status in _OLDEST_FIRST_STATUSES else created.desc())
                .limit(limit)
                .all()
            )
            return self._answer(status, [_row_summary(r) for r in rows])
        except Exception as e:
            logger.exception("outreach_list_queue failed")
            return ToolResult(success=False, error=str(e))

    def _list_via_backend(self, status: str, limit: int) -> ToolResult:
        # /queue and /approved are the Outreach page's own lists; any other
        # status is filtered out of the most recent /audit rows.
        path = {
            "drafted": "/api/social-outreach/queue",
            "approved": "/api/social-outreach/approved",
        }.get(status)
        params = None
        if path is None:
            path, params = "/api/social-outreach/audit", {"limit": 1000}
        try:
            rows = request_json("GET", path, params=params).data or []
        except BackendError as e:
            return ToolResult(success=False, error=f"Could not list outreach drafts: {e}")
        rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=status not in _OLDEST_FIRST_STATUSES)
        return self._answer(status, [_row_summary(r) for r in rows[:limit]])


class OutreachDraftPostTool(BaseTool):
    """Draft a single comment or share post and queue it for review."""

    name = "outreach_draft_post"
    read_only = False
    destructive = False
    description = (
        "Draft a social outreach comment or share post (does NOT post). "
        "Platforms: reddit, discord, facebook, youtube. X and LinkedIn are not "
        "offered for outreach. "
        "For mode='comment' you must supply either thread_context (the OP/comment/video "
        "description) or target_url (we'll scout it — for YouTube URLs, scrapes the video "
        "title + description). mode='share' drafts a Reddit link post: platform must be "
        "reddit, share_target is the subreddit (e.g. 'r/SideProject') and share_link is "
        "optional (defaults to the site URL in the outreach profile). "
        "The draft lands in the queue at status='drafted' for human approval — nothing "
        "posts until the user approves it on the Approvals page (Outreach tab). For a post "
        "to the user's own connected accounts (Discord webhook, Bluesky, Mastodon, Telegram, "
        "LinkedIn) use request_publish."
    )
    parameters = {
        "platform": ToolParameter(
            name="platform", type="string", required=True,
            description=f"One of: {', '.join(_KNOWN_PLATFORMS)}",
            enum=list(_KNOWN_PLATFORMS),
        ),
        "mode": ToolParameter(
            name="mode", type="string", required=False,
            description="'comment' (reply in a thread) or 'share' (new Reddit link post)",
            default="comment", enum=list(_DRAFT_MODES),
        ),
        "thread_context": ToolParameter(
            name="thread_context", type="string", required=False,
            description="OP body + top comments concatenated (comment mode)",
        ),
        "target_url": ToolParameter(
            name="target_url", type="string", required=False,
            description=(
                "Comment mode: URL of the thread; if thread_context is missing we scout it. "
                "Ignored in share mode, where the destination is share_target."
            ),
        ),
        "share_target": ToolParameter(
            name="share_target", type="string", required=False,
            description="Share mode: the subreddit the link post goes to, e.g. 'r/SideProject'",
        ),
        "share_link": ToolParameter(
            name="share_link", type="string", required=False,
            description="Link to share; defaults to the site URL in the outreach profile",
        ),
        "tone": ToolParameter(
            name="tone", type="string", required=False,
            description=f"Optional tone preset: {', '.join(persona.TONE_GUIDES)}",
            enum=list(persona.TONE_GUIDES),
        ),
        "feature_hint": ToolParameter(
            name="feature_hint", type="string", required=False,
            description=(
                "A feature for the draft to lead with, e.g. 'rag' or 'video_gen' "
                "(or plain words). The persona leads with it when it fits the thread "
                "(the community, in share mode) and grades below 0.3, saying why, "
                "when it does not. Leave it out to let the persona pick the angle "
                "from the pitch sheet."
            ),
        ),
        "include_link": ToolParameter(
            name="include_link", type="bool", required=False,
            description=(
                "Comment mode only. When true, the draft carries a link from the "
                "outreach profile: the repo for YouTube, the site for other platforms. "
                "The persona is asked to work it in; if it leaves it out, the "
                "link is appended on its own line. The persona is told to grade "
                "below 0.7 when the link cannot be made to feel natural; the "
                "draft is queued either way. Defaults to false."
            ),
            default=False,
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        platform = (kwargs.get("platform") or "").strip().lower()
        refusal = platforms.refusal_for(platform) or platforms.refusal_for_url(kwargs.get("target_url"))
        if refusal:
            return ToolResult(success=False, error=refusal)
        if platform not in _KNOWN_PLATFORMS:
            return ToolResult(
                success=False,
                error=f"platform must be one of {_KNOWN_PLATFORMS}, got '{platform}'",
            )
        mode = str(kwargs.get("mode") or "comment").strip().lower()
        if mode not in _DRAFT_MODES:
            return ToolResult(
                success=False,
                error=f"mode must be one of {_DRAFT_MODES}, got '{mode}'",
            )
        target_url = kwargs.get("target_url")
        target_thread_id = None

        # Build context dict for persona.draft_outreach_text
        if mode == "share":
            if platform not in _SHARE_PLATFORMS:
                return ToolResult(
                    success=False,
                    error=(
                        f"mode='share' can only be posted on {', '.join(_SHARE_PLATFORMS)} "
                        f"(a link post to a subreddit). For {platform}, draft a comment on a "
                        "thread with mode='comment'."
                    ),
                )
            share_target = (kwargs.get("share_target") or "").strip()
            if not share_target:
                return ToolResult(
                    success=False,
                    error="share mode requires share_target (e.g. 'r/SideProject')",
                )
            subreddit = _subreddit_name(share_target)
            if not subreddit:
                return ToolResult(
                    success=False,
                    error=(
                        f"share_target '{share_target}' is not a subreddit. "
                        "Use the form 'r/SideProject'."
                    ),
                )
            # The posting tick reads the subreddit from the row's target_url,
            # so a share row points at the subreddit, whatever target_url the
            # caller sent.
            from backend.services.social_outreach.reddit_outreach import REDDIT_BASE
            target_url = f"{REDDIT_BASE}/r/{subreddit}"
            context = {
                "target": f"r/{subreddit}",
                "link_url": (kwargs.get("share_link") or profile.site_url()),
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
                from backend.utils.settings_utils import web_access_block_reason
                blocked = web_access_block_reason("read the thread at that link")
                if blocked:
                    return ToolResult(
                        success=False,
                        error=f"{blocked} Or pass the thread's text as thread_context.",
                    )
                try:
                    from backend.api.social_outreach_api import (
                        _scout_reddit_url, _scout_generic_url,
                    )
                    scouted = None
                    try:
                        from urllib.parse import urlparse
                        _host = (urlparse(target_url or "").hostname or "").lower()
                    except Exception:
                        _host = ""
                    from backend.utils.hosts import host_matches, url_host_matches
                    if host_matches(_host, "reddit.com"):
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
                    if not target_thread_id and url_host_matches(target_url, "youtube.com"):
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
                requested_feature=kwargs.get("feature_hint"),
                include_link=bool(kwargs.get("include_link", False)),
            )
        except Exception as e:
            logger.exception("draft_outreach_text failed")
            return ToolResult(success=False, error=f"LLM draft failed: {e}")

        draft_text = (result.get("draft") or "").strip()
        grade = float(result.get("grade") or 0.0)
        reason = result.get("reason") or ""

        if mode == "share" and not _share_title(draft_text):
            return ToolResult(
                success=False,
                error=(
                    "The persona returned a share draft with no title. A Reddit link post "
                    "needs one, so nothing was queued."
                ),
                output={"platform": platform, "mode": mode, "draft": draft_text,
                        "grade": grade, "reason": reason},
            )

        if is_mcp_transport(self):
            return self._queue_via_backend(
                platform, mode, target_url, target_thread_id, draft_text, grade, reason,
            )

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
                "target_url": target_url,
                "draft": draft_text,
                "grade": grade,
                "reason": reason,
                "queued_status": "drafted",
            },
            metadata={"audit_id": audit_id, "grade": grade},
        )

    @staticmethod
    def _queue_via_backend(platform, mode, target_url, target_thread_id, draft_text, grade, reason) -> ToolResult:
        # POST /drafts queues the text this tool already drafted, at
        # status='drafted'; /draft-comment would draft it a second time.
        draft = {
            "platform": platform,
            "mode": mode,
            "target_url": target_url,
            "draft": draft_text,
            "grade": grade,
            "reason": reason,
        }
        if not draft_text:
            return ToolResult(success=False, error="The persona returned an empty draft.", output=draft)
        try:
            row = request_json("POST", "/api/social-outreach/drafts", payload={
                "platform": platform,
                "action": "comment" if mode == "comment" else "share",
                "target_url": target_url,
                "target_thread_id": target_thread_id,
                "draft_text": draft_text,
                "grade_score": grade,
                "source": "chat_tool",
                "reason": reason,
            }).data or {}
        except BackendError as e:
            return ToolResult(success=False, error=f"The draft was written but not queued: {e}", output=draft)
        return ToolResult(
            success=True,
            output={**draft, "audit_id": row.get("id"), "queued_status": row.get("status", "drafted")},
            metadata={"audit_id": row.get("id"), "grade": grade},
        )


class OutreachApproveDraftTool(BaseTool):
    """Approve a queued draft. Approving does not post it."""

    name = "outreach_approve_draft"
    read_only = False
    description = (
        "Approve an outreach draft by id. Optionally pass draft_text to overwrite "
        "the text before approving (handy when the user asks to tweak it). "
        "Approving does not post: a Reddit, YouTube or Facebook draft posts only when "
        "the user presses Post now on the Approvals page with the Agent Screen open, "
        "and the Discord bot sends a Discord draft. X and LinkedIn outreach drafts are refused."
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
            from backend.models import SocialOutreachLog
            from backend.services.social_outreach import transitions
            draft_text = kwargs.get("draft_text")
            if not transitions.approve(event_id, None if draft_text is None else str(draft_text)):
                status = transitions.current_status(event_id)
                if status is None:
                    return ToolResult(success=False, error=f"draft {event_id} not found")
                return ToolResult(success=False, error=transitions.approval_refusal(event_id))
            row = SocialOutreachLog.query.get(event_id)
            return ToolResult(
                success=True,
                output={**_row_summary(row), "next": platforms.after_approval(row.platform)},
                metadata={"id": row.id, "status": row.status},
            )
        except Exception as e:
            logger.exception("outreach_approve_draft failed")
            return ToolResult(success=False, error=str(e))


class OutreachRejectDraftTool(BaseTool):
    """Reject a queued draft so it never posts."""

    name = "outreach_reject_draft"
    read_only = False
    # A rejected draft cannot be moved back to the queue.
    destructive = True
    description = (
        "Reject an outreach draft by id so it will not post. Success means the row is "
        "'rejected' and nothing will be published for it, even if it had already been "
        "picked up for posting. Once a draft is being submitted or is posted it can no "
        "longer be stopped: the call then fails and says so, and the row is left as it is. "
        "Use when the user says 'kill that one', 'don't post draft 42', etc."
    )
    parameters = {
        "id": ToolParameter(
            name="id", type="int", required=True,
            description="SocialOutreachLog row id",
        ),
    }

    @staticmethod
    def _rejected(row, rejected_from: Optional[str]) -> ToolResult:
        summary = _row_summary(row)
        summary["rejected_from"] = rejected_from
        if rejected_from == "processing":
            summary["note"] = (
                "This draft had been picked up for posting. It was stopped before it "
                "was submitted and will not post."
            )
        return ToolResult(
            success=True,
            output=summary,
            metadata={"id": summary["id"], "status": summary["status"], "rejected_from": rejected_from},
        )

    @staticmethod
    def _not_rejected(event_id: int, status: Optional[str], refusal: str) -> ToolResult:
        return ToolResult(
            success=False,
            error=f"Draft {event_id} was not rejected: {refusal}.",
            metadata={"id": event_id, "status": status},
        )

    def execute(self, **kwargs) -> ToolResult:
        try:
            event_id = int(kwargs.get("id"))
        except (TypeError, ValueError):
            return ToolResult(success=False, error="id must be an integer")

        if is_mcp_transport(self):
            try:
                row = request_json("POST", f"/api/social-outreach/reject/{event_id}").data
            except BackendError as e:
                if e.status == 404:
                    return ToolResult(success=False, error=f"draft {event_id} not found")
                if e.status == 409:
                    status = e.body.get("status") if isinstance(e.body, dict) else None
                    return self._not_rejected(event_id, status, str(e))
                return ToolResult(success=False, error=str(e))
            return self._rejected(row, row.get("rejected_from"))

        try:
            from backend.models import SocialOutreachLog
            from backend.services.social_outreach import transitions
            outcome = transitions.reject(event_id)
            if outcome.status is None:
                return ToolResult(success=False, error=f"draft {event_id} not found")
            if not outcome.rejected:
                return self._not_rejected(
                    event_id, outcome.status, transitions.reject_refusal(outcome.status),
                )
            return self._rejected(SocialOutreachLog.query.get(event_id), outcome.status)
        except Exception as e:
            logger.exception("outreach_reject_draft failed")
            return ToolResult(success=False, error=str(e))


class OutreachRunPassTool(BaseTool):
    """Trigger a Task-backed outreach pass on demand."""

    name = "outreach_run_pass"
    read_only = False
    description = (
        "Queue an outreach pass without waiting for the cron. platform options: "
        "'reddit', 'self_share', 'recon', 'draft', 'youtube' / 'youtube_recon' "
        "(pass topics or keyword_profile for YouTube scout). For freeform requests "
        "like 'comment on YouTube videos about Offline AI', prefer "
        "outreach_execute_intent instead. Cadence + kill switch still apply; "
        "a YouTube scout also needs web access on in Settings (off by default). "
        "Never posts."
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
        "topics": ToolParameter(
            name="topics", type="string", required=False,
            description="Comma-separated YouTube keyword topics, e.g. 'Offline AI, ComfyUI'",
        ),
        "keyword_profile": ToolParameter(
            name="keyword_profile", type="string", required=False,
            description="Single YouTube keyword profile (alias for one topic)",
        ),
        "chain_draft": ToolParameter(
            name="chain_draft", type="bool", required=False,
            description="After YouTube recon, also draft candidates (default true for youtube)",
            default=True,
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
        profiles: list[str] = []
        topics_raw = kwargs.get("topics")
        if isinstance(topics_raw, list):
            profiles.extend(str(t).strip() for t in topics_raw if t)
        elif isinstance(topics_raw, str) and topics_raw.strip():
            profiles.extend(p.strip() for p in topics_raw.split(",") if p.strip())
        kp = (kwargs.get("keyword_profile") or "").strip()
        if kp:
            profiles.append(kp)
        chain_draft = kwargs.get("chain_draft")
        if chain_draft is None:
            chain_draft = platform in ("youtube", "youtube_recon")

        try:
            from backend.services.social_outreach.job_service import queue_outreach_run

            queued = queue_outreach_run(
                platform,
                subreddit=subreddit,
                keyword_profiles=profiles or None,
                chain_draft=bool(chain_draft),
                created_by="chat_tool",
            )

            return ToolResult(
                success=True,
                output=queued,
                metadata={
                    "task_id": queued.get("task_id"),
                    "job_id": queued.get("job_id"),
                    "platform": platform,
                },
            )
        except Exception as e:
            logger.exception("outreach_run_pass failed")
            return ToolResult(success=False, error=str(e))


class OutreachExecuteIntentTool(BaseTool):
    """Classify natural-language outreach, then dispatch to real tools/ops."""

    name = "outreach_execute_intent"
    read_only = False
    description = (
        "Classify a natural-language outreach request and act: answer status, list the "
        "draft queue, approve/reject a draft id, or (only when clearly asked) queue a "
        "scout→draft job with real topics. Never invent YouTube scout jobs for status "
        "questions or off-topic chat. Examples that scout: 'comment on youtube videos "
        "regarding Offline AI or ComfyUI'. Examples that do NOT scout: 'what's the "
        "status of youtube outreach?', jokes. Never posts. "
        "Prefer structured platform+topics when you already know them."
    )
    requires_approval = True
    parameters = {
        "text": ToolParameter(
            name="text", type="string", required=True,
            description="The user's natural-language outreach request",
        ),
        "platform": ToolParameter(
            name="platform", type="string", required=False,
            description="Optional override: youtube|reddit|discord (with topics skips LLM)",
        ),
        "action": ToolParameter(
            name="action", type="string", required=False,
            description="Optional override: comment|recon|draft|share|reply",
        ),
        "topics": ToolParameter(
            name="topics", type="string", required=False,
            description="Optional comma-separated topics override (with platform skips LLM)",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        text = (kwargs.get("text") or "").strip()
        if not text and not (kwargs.get("platform") and kwargs.get("topics")):
            return ToolResult(success=False, error="text is required (or platform+topics)")
        topics = None
        topics_raw = kwargs.get("topics")
        if isinstance(topics_raw, list):
            topics = [str(t).strip() for t in topics_raw if t]
        elif isinstance(topics_raw, str) and topics_raw.strip():
            topics = [p.strip() for p in topics_raw.split(",") if p.strip()]

        try:
            from backend.services.social_outreach.intent import execute_outreach_intent

            result = execute_outreach_intent(
                text=text,
                platform=kwargs.get("platform"),
                action=kwargs.get("action"),
                topics=topics,
                created_by="chat_tool",
            )
            # Refuse is a successful classification with no work — not a tool crash.
            success = bool(result.get("ok") or result.get("refused"))
            return ToolResult(
                success=success,
                output=result,
                error=None if success else result.get("error"),
                metadata={
                    "task_ids": result.get("task_ids") or [],
                    "intent": result.get("intent"),
                    "refused": bool(result.get("refused")),
                    "plan": result.get("plan"),
                },
            )
        except Exception as e:
            logger.exception("outreach_execute_intent failed")
            return ToolResult(success=False, error=str(e))


__all__ = [
    "OutreachStatusTool",
    "OutreachListQueueTool",
    "OutreachDraftPostTool",
    "OutreachApproveDraftTool",
    "OutreachRejectDraftTool",
    "OutreachRunPassTool",
    "OutreachExecuteIntentTool",
]
