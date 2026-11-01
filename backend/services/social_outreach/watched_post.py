"""One browser post, started by a person who is watching it happen.

Reddit and Facebook have no posting path here but the agent browser, and neither
does YouTube unless a connected account posts comments (api_post, which takes the
draft instead). A draft for the browser is posted only when, after approving it, a
person presses "Post now, I'm watching" for it on the Approvals page while the
Agent Screen is open and streaming in the app (agent_screen_presence). The
click starts exactly that one post; a second click is refused while it runs,
and nothing posts these drafts on a schedule or in a batch.

``start`` runs in the request: it checks every gate, takes the single
running-post slot in Redis and queues ``run`` on the worker. ``run`` checks the
gates again, claims the row and drives the browser. The person must still be
watching at the submit step; if the viewer has gone away the claim is handed
back and nothing is published. A post that does not start within
``START_WINDOW_S`` of the click is not started at all.

The outcome of the last post is kept in Redis for the Approvals page
(``status``); the row's own status is the record.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, NamedTuple, Optional

from backend.services.social_outreach import kill_switch, platforms, transitions
from backend.utils import agent_screen_presence

logger = logging.getLogger(__name__)

RUNNING_KEY = "social_outreach:watched_post:running"
LAST_KEY = "social_outreach:watched_post:last"
# Longer than a post takes (browser restart, page load, typing, read-back);
# a worker that dies mid-post frees the slot after this.
RUNNING_TTL_S = 10 * 60
LAST_TTL_S = 24 * 3600
# The worker runs one job at a time; a post still waiting behind another job
# this long after the click is not started, because the person who pressed
# the button may no longer be watching.
START_WINDOW_S = 120

TASK_NAME = "social_outreach.post_watched_draft"


class _NotPostable(Exception):
    """The row itself cannot be posted (a reply without its anchor, a share
    without a title); it is aborted with this reason and not retried."""


class StartOutcome(NamedTuple):
    started: bool
    message: str
    # The row's status, or None when there is no such row.
    status: Optional[str]


def _refusal(row) -> Optional[str]:
    """Why ``row`` may not be posted now, in words the person can act on."""
    if row.status != "approved":
        return f"Only an approved draft can be posted; this one is '{row.status}'."
    policy = platforms.policy_for(row.platform)
    if policy is None:
        return f"Nothing here posts to '{row.platform}'."
    if policy.mode == platforms.REFUSED:
        return policy.refusal
    if policy.mode == platforms.BOT:
        return f"{policy.label} drafts are sent by the {policy.label} bot, not from here."
    if not platforms.posts(row.platform, row.action):
        return f"Nothing here can post a {row.action or 'post'} on {policy.label}."
    stopped = kill_switch.posting_stop_reason()
    if stopped:
        return stopped
    if not kill_switch.is_enabled():
        return "Outreach is switched off. Switch it on on the Outreach page, then press Post now again."
    from backend.utils.settings_utils import web_access_block_reason

    blocked = web_access_block_reason("post an approved outreach draft")
    if blocked:
        return blocked
    cadence_ok, cadence_reason = kill_switch.cadence_allows_post((row.platform or "").strip().lower())
    if not cadence_ok:
        return f"{policy.label} posting limit: {cadence_reason}."
    watching, why = agent_screen_presence.watching()
    if not watching:
        return why
    return None


def _row(event_id: int):
    from backend.models import SocialOutreachLog, db

    db.session.expire_all()
    return db.session.get(SocialOutreachLog, event_id)


def _take_slot(event_id: int) -> Optional[str]:
    """Hold the one running-post slot for ``event_id``. None on success,
    else why not. Without Redis nothing starts."""
    r = kill_switch._get_redis()
    if r is None:
        return "Redis is unavailable, so the post cannot be started safely."
    value = json.dumps({"event_id": event_id, "since": time.time()})
    if r.set(RUNNING_KEY, value, nx=True, ex=RUNNING_TTL_S):
        return None
    running = _running(r)
    which = f" (draft #{running['event_id']})" if running else ""
    return f"Another post is running{which}. Wait for it to finish, then press Post now again."


def _running(r) -> Optional[dict]:
    try:
        raw = r.get(RUNNING_KEY)
        return json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return None


def release_slot(event_id: int) -> None:
    """Free the running-post slot if ``event_id`` holds it. Never raises."""
    try:
        r = kill_switch._get_redis()
        if r is None:
            return
        running = _running(r)
        if running and running.get("event_id") == event_id:
            r.delete(RUNNING_KEY)
    except Exception as e:  # noqa: BLE001 - the slot also expires on its own
        logger.warning("watched post: could not free the running slot: %s", e)


def _remember(event_id: int, posted: bool, message: str) -> None:
    try:
        r = kill_switch._get_redis()
        if r is not None:
            r.set(LAST_KEY, json.dumps({
                "event_id": event_id, "posted": posted, "message": message, "at": time.time(),
            }), ex=LAST_TTL_S)
    except Exception as e:  # noqa: BLE001 - the row's status is the record
        logger.warning("watched post: could not keep the outcome: %s", e)


def start(event_id: int, dispatch: Optional[Callable[[int, float], Any]] = None) -> StartOutcome:
    """Start the watched post for one approved draft (needs an app context).

    ``dispatch(event_id, clicked_at)`` queues ``run`` on the worker; the
    default sends the Celery task.
    """
    row = _row(event_id)
    if row is None:
        return StartOutcome(False, "not found", None)
    from backend.services.social_outreach import api_post

    if api_post.handles(row):
        posted, message = api_post.post(row)
        _remember(event_id, posted, message)
        return StartOutcome(posted, message, transitions.current_status(event_id))
    refusal = _refusal(row)
    if refusal:
        return StartOutcome(False, refusal, row.status)
    busy = _take_slot(event_id)
    if busy:
        return StartOutcome(False, busy, row.status)
    clicked_at = time.time()
    try:
        (dispatch or _dispatch)(event_id, clicked_at)
    except Exception as e:  # noqa: BLE001 - a post that did not queue must free the slot
        release_slot(event_id)
        logger.error("watched post: could not queue draft %s: %s", event_id, e)
        return StartOutcome(False, f"The post could not be queued: {e}", row.status)
    label = platforms.label_for(row.platform)
    return StartOutcome(
        True,
        f"Posting draft #{event_id} to {label}. Watch the Agent Screen: the agent opens the "
        "page in its browser, types this text and submits it.",
        row.status,
    )


def _dispatch(event_id: int, clicked_at: float) -> None:
    from backend.celery_app import celery

    celery.send_task(TASK_NAME, args=[event_id, clicked_at], queue="default")


def status() -> dict:
    """What the Approvals page shows: is a viewer streaming, is a post
    running, how the last one ended."""
    watching, why = agent_screen_presence.watching()
    running = last = None
    try:
        r = kill_switch._get_redis()
        if r is not None:
            running = _running(r)
            raw = r.get(LAST_KEY)
            last = json.loads(raw) if raw else None
    except Exception as e:  # noqa: BLE001 - status is advisory
        logger.warning("watched post: status unreadable: %s", e)
    return {"watching": watching, "watching_reason": why, "running": running, "last": last}


def run(event_id: int, clicked_at: float) -> dict:
    """The worker side of one watched post (needs an app context).

    Returns {"event_id", "posted", "status", "message"}.
    """
    try:
        posted, message = _run(event_id, clicked_at)
    except Exception as e:
        transitions.abort_in_flight(event_id, f"servo: {type(e).__name__}: {e}")
        _remember(event_id, False, f"The post failed: {e}")
        raise
    finally:
        release_slot(event_id)
    _remember(event_id, posted, message)
    return {
        "event_id": event_id,
        "posted": posted,
        "status": transitions.current_status(event_id),
        "message": message,
    }


def _run(event_id: int, clicked_at: float) -> tuple[bool, str]:
    waited = time.time() - float(clicked_at or 0)
    if waited > START_WINDOW_S:
        return False, (
            f"The post did not start within {START_WINDOW_S // 60} minutes of the click "
            "(the worker was busy). Nothing was posted; press Post now again."
        )
    row = _row(event_id)
    if row is None:
        return False, "The draft no longer exists."
    refusal = _refusal(row)
    if refusal:
        return False, refusal

    from backend.utils.agent_browser_control import agent_browser_control

    with agent_browser_control() as busy:
        if busy:
            return False, (
                f"The agent is busy ({busy}). Nothing was posted; press Post now again when it is free."
            )
        if not transitions.claim(event_id):
            return False, f"The draft changed before the post started (now '{transitions.current_status(event_id)}')."

        left: list[str] = []
        gate = transitions.submit_gate(event_id)

        def before_submit() -> bool:
            still, why = agent_screen_presence.watching()
            if not still:
                left.append(why or agent_screen_presence.NOT_WATCHING)
                transitions.release_claim(event_id)
                return False
            return gate()

        try:
            success, reason = _post(row, before_submit)
        except _NotPostable as e:
            transitions.abort_in_flight(event_id, str(e))
            return False, f"This draft cannot be posted: {e}"

    if success:
        return True, f"Posted draft #{event_id}."
    if left:
        return False, f"Nothing was posted: the Agent Screen was closed before the submit step. {left[0]}"
    if reason == transitions.WITHDRAWN_BEFORE_SUBMIT:
        logger.info("watched post: draft %s was withdrawn before submit", event_id)
        transitions.abort_in_flight(event_id, f"servo: {reason}")
        return False, "Nothing was posted: the draft was rejected or posting was stopped before the submit step."
    transitions.abort_in_flight(event_id, f"servo: {reason}")
    return False, f"The post did not go through: {reason}"


def _post(row, before_submit: Callable[[], bool]) -> tuple[bool, str]:
    """Drive the platform's poster for ``row`` and record a published post.

    Returns the poster's (published, reason). Raises _NotPostable when the
    row has nothing a poster could post. The posters are looked up when
    called, so a test can replace them on their modules.
    """
    from backend.services.social_outreach.reddit_outreach import record_post_via_backend

    platform = (row.platform or "").strip().lower()
    action = (row.action or "").strip().lower()

    if action == "comment":
        # Content-agent drafts carry UTM-tagged copy in posted_text; drafts
        # from /draft-comment leave it NULL and the posters tag at type time.
        text = row.posted_text or row.draft_text
        if platform == "reddit":
            from backend.services.social_outreach.reddit_outreach import post_comment_via_servo
            success, reason = post_comment_via_servo(row.target_url, text, before_submit=before_submit)
        elif platform == "youtube":
            from backend.services.social_outreach.youtube_outreach import post_youtube_comment_via_servo
            success, reason = post_youtube_comment_via_servo(
                row.target_url, text, row.task_id, before_submit=before_submit,
            )
        elif platform == "facebook":
            from backend.services.social_outreach.facebook_outreach import post_comment_via_bidi
            success, reason = post_comment_via_bidi(row.target_url, text, before_submit=before_submit)
        else:
            raise _NotPostable(f"unsupported: no poster for action 'comment' on platform '{platform}'")
        if success:
            record_post_via_backend(
                row.id, row.target_url, row.target_thread_id, text, row.task_id, platform=platform,
            )
        return success, reason

    if action == "reply":
        # The content agent stores a reply's draft_text as a JSON envelope
        # {draft, anchor, incoming_*}: the anchor names the parent comment, and
        # posted_text holds the plain reply typed into the composer.
        reply_text, anchor_hint = reply_parts(row)
        if not anchor_hint or not reply_text:
            raise _NotPostable("reply_missing_anchor_or_text")
        from backend.services.social_outreach.youtube_outreach import post_youtube_reply_via_servo
        success, reason = post_youtube_reply_via_servo(
            row.target_url, anchor_hint, reply_text, row.task_id, before_submit=before_submit,
        )
        if success:
            record_post_via_backend(
                row.id, row.target_url, row.target_thread_id, reply_text, row.task_id, platform=platform,
            )
        return success, reason

    if action == "share":
        from backend.services.social_outreach import profile
        from backend.services.social_outreach.self_share import _submit_post_via_servo

        payload: dict = {}
        try:
            payload = json.loads(row.draft_text or "{}")
            title = (payload.get("title") or "").strip()
        except json.JSONDecodeError:
            title = (row.draft_text or "").strip()
        # The subreddit is the one in target_url (https://www.reddit.com/r/SideProject).
        m = re.search(r"/r/([^/]+)", row.target_url or "")
        subreddit = m.group(1) if m else ""
        # Rows drafted before the link was stored fall back to the profile's site.
        link_url = (payload.get("link_url") or "").strip() or profile.site_url()
        if not link_url:
            raise _NotPostable(profile.no_link_reason())
        if not (subreddit and title):
            raise _NotPostable("share row missing subreddit or title")
        success, reason = _submit_post_via_servo(subreddit, title, link_url, before_submit=before_submit)
        if success:
            _record_share(row, f"{title}\n{link_url}")
        return success, reason

    raise _NotPostable(f"unsupported: no poster for action '{action}' on platform '{platform}'")


def reply_parts(row) -> tuple[str, str]:
    """A reply draft's (text, anchor). The anchor names the parent comment."""
    reply_text = (row.posted_text or "").strip()
    anchor_hint = ""
    try:
        if row.draft_text and row.draft_text.strip().startswith("{"):
            envelope = json.loads(row.draft_text)
            anchor_hint = (
                envelope.get("anchor")
                or envelope.get("anchor_hint")
                or envelope.get("parent_text")
                or ""
            )[:200]
            if not reply_text:
                reply_text = (envelope.get("draft") or "").strip()
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        logger.warning("reply envelope parse failed for row %s: %s", row.id, e)
    return reply_text, anchor_hint


def _record_share(row, posted_text: str) -> None:
    import requests

    from backend.services.social_outreach.reddit_outreach import backend_url

    try:
        requests.post(
            f"{backend_url()}/social-outreach/record-post",
            json={
                "audit_id": row.id,
                "platform": "reddit",
                "posted_text": posted_text,
                "target_url": row.target_url,
                "target_thread_id": None,
                "task_id": row.task_id,
            },
            timeout=10,
        )
    except Exception as e:  # noqa: BLE001 - the post is out; the reaper and history show it
        logger.warning("record-post failed: %s", e)
