"""One outreach draft at a time, answered in plain words in the chat.

``/outreach`` opens a review for a chat session. The chat then shows one
draft with the post or comment it answers, and the person's next message in
that chat is read here instead of going to the chat model: "yes" posts the
draft, "skip" leaves it for later, "reject" discards it, "change it to ..."
replaces its text, "make it shorter" rewrites it, "stop" closes the review.

The answers are a fixed list read without a model, so nothing posts on a
guess: a message that is none of them changes nothing and is answered with
the list. A "yes" does not post by itself either. It marks the draft, and the
chat then calls ``post`` (at once for a typed answer, after
``SPOKEN_DELAY_MS`` for a spoken one, so a misheard "yes" can be cancelled).
``post`` approves the draft and starts the same post the Approvals page
starts, under the same switches, limits and one-post-at-a-time slot.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

KEY = "social_outreach:review:{session}"
# A review left open is forgotten after a day; its drafts stay in the queue.
TTL_S = 24 * 3600
# How long a spoken "yes" waits before the post starts.
SPOKEN_DELAY_MS = 3000
# A "yes" the chat never followed up on stops counting after this long.
PENDING_TTL_S = 600
# Drafts taken into a review of everything that is waiting.
MAX_WAITING = 25

IN_FLIGHT = ("approved", "processing", "submitting")
# A draft the review asks about: not yet approved, or approved and never posted.
ANSWERABLE = ("drafted", "approved")

HINT = (
    'Say "yes" to post it, "skip" to leave it for later, "reject" to discard it, '
    '"change it to ..." or "make it shorter" to edit it, "add an infographic" or "without the image", '
    'or "stop" to leave the review.'
)

_POST_CORE = (
    r"yes|yeah|yep|yup|approve|approved|post|send|go ahead|do it|"
    r"looks good|sounds good|lgtm|ship it"
)
_TAIL = r"please|it|that|this|one|now|and|then|for me|thanks|thank you"
_LEAD = r"(?:(?:ok|okay|sure|great|good|perfect|alright)\s+)?"
_POST = re.compile(rf"{_LEAD}(?:{_POST_CORE})(?:\s+(?:{_POST_CORE}|{_TAIL}))*")
_SKIP = re.compile(rf"(?:skip|next|later|pass|not now|no|nope|no thanks)(?:\s+(?:{_TAIL}|for now))*")
_REJECT = re.compile(rf"(?:reject|discard|delete|drop|remove|trash)(?:\s+(?:{_TAIL}))*")
_STOP = re.compile(
    r"(?:stop|cancel|quit|exit|end|done|enough|thats all|that's all|close|leave)"
    r"(?:\s+(?:the|review|reviewing|for now|please|it|this|that))*"
)
_EDIT = re.compile(
    r"^\s*(?:change (?:it |that |this )?to|replace (?:it |that |this )?with|make it say|"
    r"say this instead|use this instead|edit(?: it)? to|reword(?: it)? to)\s*[:,\-]?\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_REDRAFT = re.compile(
    r"^\s*(?:make (?:it|this|that)|rewrite(?: it)?|redraft(?: it)?|rephrase(?: it)?|"
    r"reword(?: it)?|try again|again)\b\s*[,:]?\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_IMAGE_OFF = re.compile(r"(?:without|no|remove|drop|lose) (?:the |an |any )?(?:image|picture|infographic|photo)")
_IMAGE_ON = re.compile(
    r"(?:add|attach|with|use|include) (?:an? |the )?(?:image|picture|infographic)|"
    r"(?:use |try )?(?:a |an |the )?(?:different|another|other) (?:image|picture|infographic)"
)
_REDRAFT_BARE = re.compile(r"(?:shorter|longer|friendlier|simpler|(?:more|less) [a-z]+(?: [a-z]+)?)")


def read_answer(text: str) -> tuple[str, str]:
    """(kind, argument) for a message sent while a review is open. Kind is
    post, skip, reject, stop, edit (argument: the new text), redraft
    (argument: what to change), image_off, image_on or unclear."""
    raw = (text or "").strip()
    plain = " ".join(re.sub(r"[^\w\s']", " ", raw.lower()).split())
    if not plain:
        return "unclear", ""
    for kind, pattern in (
        ("image_off", _IMAGE_OFF), ("image_on", _IMAGE_ON),
        ("post", _POST), ("skip", _SKIP), ("reject", _REJECT), ("stop", _STOP),
    ):
        if pattern.fullmatch(plain):
            return kind, ""
    edit = _EDIT.match(raw)
    if edit:
        return "edit", edit.group(1).strip().strip("\"'“”").strip()
    redraft = _REDRAFT.match(raw)
    if redraft:
        return "redraft", redraft.group(1).strip() or "write a different version"
    if _REDRAFT_BARE.fullmatch(plain):
        return "redraft", plain
    return "unclear", ""


# --- state -------------------------------------------------------------------

def _redis():
    from backend.services.social_outreach import kill_switch

    return kill_switch._get_redis()


def _key(session_id: str) -> str:
    return KEY.format(session=re.sub(r"[^\w.\-]", "_", str(session_id))[:120])


def _load(session_id: str) -> Optional[dict]:
    r = _redis()
    if r is None or not session_id:
        return None
    try:
        raw = r.get(_key(session_id))
        return json.loads(raw) if raw else None
    except Exception as e:  # noqa: BLE001 - an unreadable review is a closed one
        logger.warning("outreach review: state unreadable: %s", e)
        return None


def _save(session_id: str, state: dict) -> None:
    r = _redis()
    if r is not None:
        r.set(_key(session_id), json.dumps(state), ex=TTL_S)


def _close(session_id: str) -> None:
    r = _redis()
    if r is not None:
        r.delete(_key(session_id))


def _row(draft_id: int):
    from backend.models import SocialOutreachLog, db

    db.session.expire_all()
    return db.session.get(SocialOutreachLog, int(draft_id))


def _waiting_count() -> int:
    from backend.models import SocialOutreachLog

    return SocialOutreachLog.query.filter(SocialOutreachLog.status.in_(ANSWERABLE)).count()


# --- the review --------------------------------------------------------------

def start(
    session_id: str,
    follow: Optional[dict] = None,
    draft_ids: Optional[list] = None,
    waiting: bool = False,
    platform: Optional[str] = None,
) -> dict[str, Any]:
    """Open a review for this chat: over the drafts a queued scout will write
    (``follow``, as /intent returned it), over named drafts, or over
    everything that waits (``waiting``, optionally one platform): drafts not
    yet approved and approved ones that were never posted."""
    if not session_id:
        return {"open": False, "error": "A review belongs to a chat; no chat session was named."}
    if _redis() is None:
        return {"open": False, "error": "Redis is not running, so a review cannot be kept."}
    state: dict[str, Any] = {"follow": None, "queue": [], "pending": None}
    if follow and follow.get("task_ids"):
        state["follow"] = {
            "task_ids": [int(t) for t in follow["task_ids"]],
            "after_id": int(follow.get("after_id") or 0),
            "platform": follow.get("platform") or None,
        }
    elif draft_ids:
        state["queue"] = [int(d) for d in draft_ids]
    elif waiting:
        from backend.models import SocialOutreachLog

        rows = SocialOutreachLog.query.filter(SocialOutreachLog.status.in_(ANSWERABLE))
        if platform:
            rows = rows.filter(SocialOutreachLog.platform == platform.strip().lower())
        state["queue"] = [r.id for r in rows.order_by(SocialOutreachLog.id.asc()).limit(MAX_WAITING).all()]
    _save(session_id, state)
    return view(session_id)


def _resolve(session_id: str, state: dict) -> dict[str, Any]:
    """Bring the review up to date and describe it. Drops drafts that are no
    longer waiting from the front of the queue; closes a review with nothing
    left."""
    from backend.services.social_outreach import run_result

    out: dict[str, Any] = {
        "open": True, "waiting": False, "tasks": [], "posting": False,
        "pending": False, "current": None, "remaining": 0, "last": state.get("last"),
    }
    follow = state.get("follow")
    if follow:
        result = run_result.run_result(follow["task_ids"], follow["after_id"], follow.get("platform"))
        if not result["done"]:
            out.update(waiting=True, tasks=follow["task_ids"])
            return out
        state["queue"] = [d["id"] for d in result["drafts"] if d["status"] == "drafted"]
        state["follow"] = None

    pending = state.get("pending")
    if pending and time.time() - float(pending.get("at") or 0) > PENDING_TTL_S and not pending.get("started"):
        pending = state["pending"] = None
    queue = list(state.get("queue") or [])
    current = None
    while queue:
        row = _row(queue[0])
        mine = bool(pending and pending.get("id") == queue[0])
        if row is not None and mine and pending.get("started") and row.status in IN_FLIGHT:
            current = row
            out["posting"] = True
            break
        if row is not None and row.status in ANSWERABLE:
            current = row
            break
        if mine:
            state["last"] = out["last"] = _outcome(row)
            pending = state["pending"] = None
        queue.pop(0)
    state["queue"] = queue
    if current is None:
        _close(session_id)
        out["open"] = False
        return out
    _save(session_id, state)
    out["current"] = run_result.presentable(current)
    out["remaining"] = len(queue) - 1
    out["pending"] = bool(pending and pending.get("id") == current.id and not out["posting"])
    return out


def _outcome(row) -> Optional[dict]:
    """How the draft this review posted ended, in words for the chat."""
    if row is None:
        return None
    from backend.services.social_outreach import platforms, watched_post

    label = platforms.label_for(row.platform)
    message = None
    try:
        last = watched_post.status().get("last") or {}
        if last.get("event_id") == row.id:
            message = last.get("message")
    except Exception as e:  # noqa: BLE001 - the row's status still says how it ended
        logger.warning("outreach review: last post unreadable: %s", e)
    if not message:
        message = (
            f"Draft #{row.id} is posted on {label}." if row.status == "posted"
            else f"Draft #{row.id} was not posted ({row.abort_reason or row.status})."
        )
    return {"id": row.id, "status": row.status, "posted": row.status == "posted", "message": message}


def view(session_id: str) -> dict[str, Any]:
    """Where this chat's review stands: ``{open, waiting, tasks, posting,
    pending, current, remaining, last}``. ``current`` is the draft to answer."""
    state = _load(session_id)
    if state is None:
        return {"open": False}
    return _resolve(session_id, state)


_FIXED = 'Draft #{id} is already approved, so its text is fixed. Say "yes" to post it, or "reject".'


def _next_words(after: dict) -> str:
    if not after.get("open"):
        return " That was the last one."
    return ""


def answer(session_id: str, text: str, spoken: bool = False) -> dict[str, Any]:
    """Read one chat message as an answer to the draft on show.

    ``handled`` False means no review is open and the message belongs to the
    chat. Otherwise ``say`` is the reply, ``kind`` what the message was read
    as, and ``post`` ({id, delay_ms, needs_screen}) is set when the chat
    should now call ``post``. The view fields describe the review afterwards.
    """
    state = _load(session_id)
    if state is None:
        return {"handled": False}
    before = _resolve(session_id, state)
    if not before["open"]:
        return {"handled": False}
    kind, argument = read_answer(text)

    def reply(say: str, **extra: Any) -> dict[str, Any]:
        after = view(session_id)
        return {"handled": True, "kind": kind, "say": say + _next_words(after), **after, **extra}

    if kind == "stop" and not before["pending"]:
        _close(session_id)
        left = _waiting_count()
        tail = f" {left} draft{'s' if left != 1 else ''} still wait on the Approvals page." if left else ""
        return {"handled": True, "kind": kind, "say": f"Review closed.{tail}", "open": False}
    if before["waiting"]:
        return reply("Still looking for posts to answer. The first draft shows here when it is ready.")
    row_id = before["current"]["id"]
    if before["posting"]:
        return reply(f"Draft #{row_id} is posting now; the result shows here when it ends.")
    if before["pending"] and kind != "post":
        # A "yes" is waiting to post: anything else takes it back and changes nothing.
        state["pending"] = None
        _save(session_id, state)
        kind = "cancel"
        return reply(f"Cancelled; draft #{row_id} has not been posted. {HINT}")

    from backend.services.social_outreach import persona, platforms, transitions, watched_post

    row = _row(row_id)
    label = platforms.label_for(row.platform)
    if kind == "post":
        refusal = watched_post.refusal_before_approval(row, viewer_required=False)
        if refusal:
            return reply(f"Draft #{row_id} was not posted. {refusal}")
        state["pending"] = {"id": row_id, "at": time.time(), "spoken": bool(spoken)}
        _save(session_id, state)
        delay_ms = SPOKEN_DELAY_MS if spoken else 0
        say = (
            f"Posting draft #{row_id} to {label} in {SPOKEN_DELAY_MS // 1000} seconds. Say stop to cancel."
            if spoken else f"Posting draft #{row_id} to {label}."
        )
        needs_screen = watched_post.needs_viewer(row)
        return reply(say, post={"id": row_id, "delay_ms": delay_ms, "needs_screen": needs_screen})
    if kind == "skip":
        state["queue"] = [d for d in state["queue"] if d != row_id]
        _save(session_id, state)
        return reply(f"Left draft #{row_id} in the queue for later.")
    if kind == "reject":
        outcome = transitions.reject(row_id)
        if not outcome.rejected:
            return reply(transitions.reject_refusal(outcome.status))
        return reply(f"Rejected draft #{row_id}. It will not post.")
    if kind == "edit":
        if not argument:
            return reply('Say the new text after "change it to".')
        if not transitions.move(row_id, "drafted", ("drafted",), **transitions.edited_fields(row_id, argument)):
            return reply(_FIXED.format(id=row_id))
        return reply(f'Changed draft #{row_id}. Say "yes" to post it.')
    if kind == "redraft":
        if row.status != "drafted":
            return reply(_FIXED.format(id=row_id))
        shown = before["current"]
        revised = persona.revise_draft(
            row.action, shown.get("source"), shown.get("text") or "", argument, platform=row.platform,
        )
        if not revised["draft"] or revised["draft"].strip() == (shown.get("text") or "").strip():
            return reply(f"The rewrite came back with nothing new, so draft #{row_id} is unchanged.")
        if not transitions.move(row_id, "drafted", ("drafted",), **transitions.edited_fields(row_id, revised["draft"])):
            return reply(_FIXED.format(id=row_id))
        return reply(f'Rewrote draft #{row_id}. Say "yes" to post it.')
    if kind in ("image_off", "image_on"):
        return reply(_change_image(row, attach=kind == "image_on", shown=before["current"]))
    return reply(f"Nothing was changed. {HINT}")


def _change_image(row, attach: bool, shown: dict) -> str:
    """Attach an infographic to the draft on show, swap it for another, or
    take it off; returns what to say."""
    from backend.services.social_outreach import audit, infographics, own_posts, platforms

    if row.status != "drafted":
        return _FIXED.format(id=row.id)
    current = infographics.media_of(row)
    if not attach:
        if not current:
            return f"Draft #{row.id} has no image."
        if not own_posts.posts_without_image(row.platform, row.action):
            return f"Draft #{row.id} is an image post on {platforms.label_for(row.platform)}; it cannot go without one."
        audit.set_media(row.id, None)
        return f'Took the image off draft #{row.id}. Say "yes" to post it.'
    if not own_posts.takes_image(row.platform, row.action):
        return f"A {row.action} on {platforms.label_for(row.platform)} cannot carry an image here yet."
    if row.platform == "reddit":
        from backend.services.social_outreach.reddit_outreach import comments_take_images

        sub = re.search(r"reddit\.com/r/([^/?#]+)", row.target_url or "")
        if sub and comments_take_images(sub.group(1)) is False:
            return f"r/{sub.group(1)} does not allow images in comments, so draft #{row.id} stays as text."
    about = " ".join(str((shown.get("source") or {}).get(k) or "") for k in ("title", "text")) + " " + (shown.get("text") or "")
    item = own_posts.best_for(about, exclude=int(current["document_id"]) if current else None)
    if item is None:
        return (
            "There is no other infographic to use: mark more on the Outreach page; "
            "each is usable once its text has been read back and matches."
        )
    try:
        media = infographics.media_for(int(item["document_id"]))
    except Exception as e:  # noqa: BLE001 - said to the person, not raised into the chat
        return f"That infographic could not be attached: {e}"
    audit.set_media(row.id, media)
    words = " Its words were written for the earlier picture; change them if they no longer fit." if current else ""
    return f'Attached \u201c{item.get("headline") or "an infographic"}\u201d to draft #{row.id}.{words} Say "yes" to post it.'


def post(session_id: str, draft_id: int) -> dict[str, Any]:
    """Approve and post the draft a "yes" marked. ``started`` False leaves a
    reason in ``message``; a draft that could not start is left as it was
    unless the approval itself had already gone through."""
    from backend.services.social_outreach import transitions, watched_post

    state = _load(session_id)
    pending = (state or {}).get("pending")
    if not pending or pending.get("id") != int(draft_id) or pending.get("started"):
        return {"started": False, "cancelled": True, "message": "", **view(session_id)}
    wait_s = SPOKEN_DELAY_MS / 1000 if pending.get("spoken") else 0
    if time.time() < float(pending["at"]) + wait_s - 0.25:
        return {"started": False, "early": True, "message": "", **view(session_id)}

    def ended(started: bool, message: str) -> dict[str, Any]:
        return {"started": started, "message": message, **view(session_id)}

    row = _row(draft_id)
    refusal = None if row is not None and row.status in ANSWERABLE else "The draft is no longer waiting."
    refusal = refusal or watched_post.refusal_before_approval(row)
    if refusal:
        state["pending"] = None
        _save(session_id, state)
        return ended(False, f"Draft #{draft_id} was not posted. {refusal}")
    if row.status == "drafted" and not transitions.approve(int(draft_id)):
        state["pending"] = None
        _save(session_id, state)
        why = transitions.approval_refusal(int(draft_id)) or "It could not be approved."
        return ended(False, f"Draft #{draft_id} was not posted. {why}")
    state["pending"] = {**pending, "started": True}
    _save(session_id, state)
    outcome = watched_post.start(int(draft_id))
    if not outcome.started:
        state["pending"] = None
        state["queue"] = [d for d in state["queue"] if d != int(draft_id)]
        _save(session_id, state)
        still_approved = transitions.current_status(int(draft_id)) == "approved"
        tail = " It is approved and waits on the Approvals page." if still_approved else ""
        return ended(False, f"{outcome.message}{tail}")
    return ended(True, outcome.message)
