"""One YouTube comment through the official API, started by a person.

A connected YouTube account with Outreach comments switched on takes approved
YouTube drafts from the agent browser. The person still approves the draft and
presses Post now, one click for one comment, and the cadence limits and the public
posting stop still apply. No browser is driven, so the Agent Screen need not be open,
and the Outreach switch is not needed either: that switch runs the scheduled passes,
and nothing here is scheduled.

Without such an account nothing changes: the draft goes to the browser (watched_post).
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.parse import parse_qs, urlparse

from backend.services.social_outreach import kill_switch, persona, platforms, transitions

logger = logging.getLogger(__name__)

PLATFORM = "youtube"
ACTIONS = ("comment", "reply")


def account():
    """The connected YouTube account switched on for comments, or None."""
    from backend.models import Connection
    from backend.services.connections.providers.youtube import OUTREACH_COMMENTS_FIELD

    for connection in Connection.query.filter_by(provider=PLATFORM, enabled=True).all():
        try:
            config = json.loads(connection.config or "{}")
        except (TypeError, ValueError):
            continue
        if config.get(OUTREACH_COMMENTS_FIELD) == "yes" and connection.status == "connected":
            return connection
    return None


def platform_ready(platform: Optional[str]) -> bool:
    return (platform or "").strip().lower() == PLATFORM and account() is not None


def ready_platforms() -> list[str]:
    return [PLATFORM] if account() is not None else []


def handles(row) -> bool:
    return platform_ready(row.platform) and (row.action or "").strip().lower() in ACTIONS


def _refusal(row) -> Optional[str]:
    if row.status != "approved":
        return f"Only an approved draft can be posted; this one is '{row.status}'."
    stopped = kill_switch.posting_stop_reason()
    if stopped:
        return stopped
    from backend.utils.settings_utils import web_access_block_reason

    blocked = web_access_block_reason("post an approved outreach draft")
    if blocked:
        return blocked
    allowed, why = kill_switch.cadence_allows_post(PLATFORM)
    if not allowed:
        return f"{platforms.label_for(PLATFORM)} posting limit: {why}."
    return None


def video_id(url: Optional[str]) -> Optional[str]:
    parsed = urlparse(url or "")
    if parsed.hostname in ("youtu.be", "www.youtu.be"):
        return parsed.path.strip("/") or None
    return (parse_qs(parsed.query).get("v") or [None])[0]


def _linked_comment(url: Optional[str]) -> Optional[str]:
    """The comment a YouTube comment link points at (``&lc=<id>``)."""
    return (parse_qs(urlparse(url or "").query).get("lc") or [None])[0]


def _send(ctx, row, video: str) -> tuple[str, str]:
    """Post the draft. Returns (comment id, the text that went out)."""
    from backend.services.connections.providers import youtube
    from backend.services.social_outreach.watched_post import reply_parts

    if (row.action or "").strip().lower() == "comment":
        text = persona.apply_utm_tags(row.posted_text or row.draft_text or "", platform=PLATFORM, campaign="v253")
        return youtube.post_comment(ctx, video, text), text

    reply_text, anchor = reply_parts(row)
    plain = (row.draft_text or "").strip()
    reply_text = reply_text or ("" if plain.startswith("{") else plain)
    if not reply_text:
        raise LookupError("This reply draft has no text.")
    parent = _linked_comment(row.target_url) or youtube.find_comment(ctx, video, anchor)
    if not parent:
        raise LookupError("The comment this replies to was not found on the video.")
    text = persona.apply_utm_tags(reply_text, platform=PLATFORM, campaign="v253")
    return youtube.post_reply(ctx, parent, text), text


def post(row) -> tuple[bool, str]:
    """Post one approved draft now. Returns (posted, what to tell the person)."""
    from backend.services.connections.providers import youtube
    from backend.services.connections.service import build_ctx

    refusal = _refusal(row)
    if refusal:
        return False, refusal
    video = video_id(row.target_url)
    if not video:
        return False, "This draft has no YouTube video link."
    connection = account()
    if connection is None:
        return False, "No YouTube account is switched on for comments."
    if not transitions.claim(row.id):
        return False, f"The draft changed before the post started (now '{transitions.current_status(row.id)}')."
    if not transitions.submit_gate(row.id)():
        transitions.abort_in_flight(row.id, f"api: {transitions.WITHDRAWN_BEFORE_SUBMIT}")
        return False, "Nothing was posted: the draft was rejected or posting was stopped first."

    ctx = build_ctx(connection)
    try:
        comment_id, text = _send(ctx, row, video)
    except LookupError as e:
        transitions.abort_in_flight(row.id, f"api: {e}")
        return False, f"Nothing was posted. {e}"
    except (RuntimeError, ValueError) as e:
        transitions.abort_in_flight(row.id, f"api: {e}")
        return False, f"The post did not go through: {e}"
    except Exception as e:  # noqa: BLE001 - a lost connection may still have posted
        logger.error("YouTube comment for draft %s ended unclear: %s", row.id, e)
        transitions.abort_in_flight(row.id, f"submitted_unconfirmed: may be live: {type(e).__name__}")
        return False, "The connection dropped while posting, so it may have gone out. Check the video before retrying."

    kill_switch.record_post(PLATFORM)
    transitions.mark_posted(row.id, text)
    url = f"https://www.youtube.com/watch?v={video}&lc={comment_id}"
    if youtube.comment_text(ctx, comment_id) is None:
        return True, f"Posted draft #{row.id}, but YouTube is not showing it yet; it may be held for review. {url}"
    return True, f"Posted draft #{row.id}: {url}"
