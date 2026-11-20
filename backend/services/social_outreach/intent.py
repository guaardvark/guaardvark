"""Natural-language outreach intent → classify then dispatch.

Single entry used by HTTP /intent, chat tool outreach_execute_intent,
GUI slash freeform, and `guaardvark outreach`.

The LLM classifies; a scout is queued only for a scout_and_draft or
reply_comments intent with sufficient confidence. Topics are the ones the
person named; when they named none, the search phrases come from the outreach
profile's own keywords, never from this module. A link in the text is drafted
for directly, and "post draft N" starts that one post; neither asks the LLM.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

from backend.services import nl_control_plane
from backend.services.social_outreach import audit, kill_switch, platforms, transitions
from backend.services.social_outreach.job_service import queue_outreach_run

logger = logging.getLogger(__name__)

_VALID_INTENTS = frozenset({
    "status",
    "list_queue",
    "approve",
    "reject",
    "post",
    "scout_and_draft",
    "reply_comments",
    "refuse",
})
# Facebook is scouted in the groups the signed-in account is in.
_SCOUT_PLATFORMS = frozenset({"youtube", "reddit", "discord", "facebook"})
# Platforms the classifier may name.
_NAMED_PLATFORMS = _SCOUT_PLATFORMS
# Posts one request looks for when the person gave no number, and the most it takes.
DEFAULT_SCOUT_COUNT = 3
MAX_SCOUT_COUNT = 10
# Search phrases taken from the profile when the person named no topic.
PROFILE_QUERY_COUNT = 3
_SCOUT_ACTIONS = frozenset({
    "comment", "reply", "recon", "scout", "scout_and_draft", "draft",
})
_MIN_SCOUT_CONFIDENCE = 0.7

_CLASSIFY_SYSTEM = """You classify social-outreach operator requests for Guaardvark.
Return ONLY a JSON object with these keys:
  intent: one of status | list_queue | approve | reject | post | scout_and_draft | reply_comments | refuse
  platform: youtube | reddit | discord | facebook | null
  topics: array of the topic strings the user named (empty if they named none)
  count: how many posts the user asked for, as an integer, or null
  draft_id: integer or null
  confidence: number 0.0-1.0
  reason: short explanation

Hard rules:
- Questions about state, queue, jobs, drafts, cadence, kill switch → status or list_queue. NEVER scout_and_draft.
- Off-topic, nonsense, jokes, nursery rhymes → refuse.
- scout_and_draft when the user asks to find, reply to, comment on or engage with OTHER people's posts, threads or videos on a platform. Topics are optional: copy the ones the user named and leave the array empty when they named none. Do NOT invent topics.
- reply_comments when the user asks to answer the comments people left on their OWN videos or channel. Platform is youtube.
- post when the user asks to publish or send one numbered draft now.
- approve, reject and post require a numeric draft_id from the user text.
- If unsure what they want → refuse with a clarifying question in reason.
- Never name a platform the user did not name.
"""


ClassifierFn = Callable[[str, str], dict[str, Any]]


def classify_outreach_utterance(
    text: str,
    *,
    classifier: Optional[ClassifierFn] = None,
) -> dict[str, Any]:
    """LLM-classify freeform text into a structured intent (no side effects)."""
    raw = (text or "").strip()
    if not raw:
        return {
            "intent": "refuse",
            "platform": None,
            "topics": [],
            "draft_id": None,
            "confidence": 1.0,
            "reason": "Empty request. Ask for status, list the queue, or describe a scout (platform + topics).",
        }

    llm = classifier or _default_classifier
    try:
        result = llm(_CLASSIFY_SYSTEM, raw) or {}
    except Exception as e:
        logger.exception("outreach classify failed")
        return {
            "intent": "refuse",
            "platform": None,
            "topics": [],
            "draft_id": None,
            "confidence": 0.0,
            "reason": f"Classifier unavailable: {e}",
        }

    return _normalize_classification(result, raw_text=raw)


def _default_classifier(system: str, user: str) -> dict[str, Any]:
    # Shared NL primitive (was a per-feature copy of persona._ollama_json_chat).
    return nl_control_plane.json_chat(system, user, on_error={})


_DRAFT_ID_RX = re.compile(r"(?:\b(?:draft|item|id)\b\s*#?|#)\s*(\d+)", re.I)
# Digit runs that stand alone as a whole number: not part of a longer number
# or of a decimal such as 2.5.
_WHOLE_NUMBER_RX = re.compile(r"(?<![\d.])\d+(?!\d|\.\d)")


def _typed_draft_id(model_id: Optional[int], raw_text: str) -> Optional[int]:
    """The draft id the user typed, or None.

    Approve and reject act on this id. A draft the text names explicitly
    ("draft 42", "id 42", "#42") wins over any other number in it and over the
    classifier's draft_id. Without one, the classifier's draft_id counts only
    when that number appears in the text as a whole number, so an id it
    inferred ("the newest draft") is dropped.
    """
    m = _DRAFT_ID_RX.search(raw_text or "")
    if m:
        return int(m.group(1))
    if model_id is not None and any(
        int(n) == model_id for n in _WHOLE_NUMBER_RX.findall(raw_text or "")
    ):
        return model_id
    return None


def _normalize_classification(raw: dict[str, Any], *, raw_text: str) -> dict[str, Any]:
    # Generic skeleton (intent whitelist, confidence clamp, int coercion) from the
    # shared control plane; outreach-specific validation layered on top.
    base = nl_control_plane.normalize(
        raw,
        valid_intents=_VALID_INTENTS,
        default_intent="refuse",
        int_fields=("draft_id",),
    )
    intent = base["intent"]

    platform = raw.get("platform")
    if platform is not None:
        platform = str(platform).strip().lower() or None
        if platform not in _NAMED_PLATFORMS:
            platform = None

    topics: list[str] = []
    for t in raw.get("topics") or []:
        s = str(t).strip()
        if s and s.lower() not in (
            "youtube", "reddit", "discord", "facebook", "videos", "video", "posts", "post",
        ):
            topics.append(s)
    topics = topics[:8]

    draft_id = _typed_draft_id(base.get("draft_id"), raw_text)

    reason = str(raw.get("reason") or "").strip() or intent

    return {
        "intent": intent,
        "platform": platform,
        "topics": topics,
        "draft_id": draft_id,
        "count": _asked_count(raw.get("count")),
        "confidence": base["confidence"],
        "reason": reason,
        "raw_text": raw_text,
    }


def _asked_count(value: Any) -> Optional[int]:
    """How many posts the person asked for, within what one request takes."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(count, MAX_SCOUT_COUNT)) if count > 0 else None


_URL_RX = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_POST_NOW_RX = re.compile(r"^\s*(?:please\s+)?(?:post|publish|send)\b", re.I)
# Hosts a pasted link is drafted for, by platform.
_LINK_PLATFORMS = (
    ("reddit", ("reddit.com", "redd.it")),
    ("youtube", ("youtube.com", "youtu.be")),
    ("facebook", ("facebook.com", "fb.com")),
    ("discord", ("discord.com",)),
)


def _link_platform(url: str) -> Optional[str]:
    from backend.utils.hosts import url_host_matches

    for platform, hosts in _LINK_PLATFORMS:
        if url_host_matches(url, *hosts):
            return platform
    return None


# "post an infographic in my Facebook groups", "share infographics on r/x".
# "make an infographic about the Docker install". Read only at the start of a
# request that is not a question, so a sentence that merely mentions an
# infographic is left to the classifier.
_MAKE_INFOGRAPHIC_RX = re.compile(
    r"^\s*(?:please\s+)?(?:make|create|generate|design|draw|render)\s+(?:me\s+)?"
    r"(?:an?|another|one|a new)?\s*infographics?\b"
    r"(?:\s+(?:about|on|for|showing|that shows)\s+(?P<topic>.+?))?"
    r"(?:\s*,?\s+(?:and|then)\s+(?:post|share|publish)\b.*)?\s*$",
    re.IGNORECASE | re.DOTALL,
)
# "post an infographic in my Facebook groups", "share infographics on r/x",
# "create a post with an infographic in my groups".
_OWN_POST_RX = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"(?:post|share|put|publish)\s+(?:an?|another|one|some|the|my|our)?\s*infographics?\b"
    r"|(?:create|make|write|draft)\s+(?:a\s+|some\s+)?posts?\s+with\s+(?:an?\s+|the\s+)?infographics?\b"
    r")",
    re.IGNORECASE,
)


def _typed_request(text: str) -> Optional[dict[str, Any]]:
    """The classification of a request whose words settle it, or None.

    A link is a request to draft a reply to that page; "post draft 12" starts
    that post; "post an infographic in ..." asks for posts of the account's own. Neither needs the model, and neither can be talked into
    something else by the rest of the sentence.
    """
    raw = (text or "").strip()
    link = _URL_RX.search(raw)
    base = {
        "platform": None, "topics": [], "draft_id": None, "count": None,
        "confidence": 1.0, "raw_text": raw,
    }
    if _POST_NOW_RX.match(raw):
        draft_id = _typed_draft_id(None, raw)
        if draft_id is not None:
            return {**base, "intent": "post", "draft_id": draft_id, "reason": "post a numbered draft"}
    if not raw.endswith("?"):
        made = _MAKE_INFOGRAPHIC_RX.match(raw)
        if made:
            return {**base, "intent": "make_infographic", "topic": (made.group("topic") or "").strip(" .!"),
                    "reason": "make an infographic"}
        if _OWN_POST_RX.match(raw):
            from backend.services.social_outreach import facebook_outreach as fb

            subreddit = _extract_subreddit(raw)
            on_reddit = bool(subreddit) or "reddit" in raw.lower()
            group = fb.group_ref(link.group(0)) if link and not on_reddit else None
            return {**base, "intent": "own_posts", "platform": "reddit" if on_reddit else "facebook",
                    "communities": [c for c in (subreddit if on_reddit else group,) if c],
                    "reason": "post an infographic"}
    if link:
        url = link.group(0).rstrip(".,;")
        return {**base, "intent": "draft_url", "url": url, "platform": _link_platform(url),
                "reason": "a link was given"}
    return None


def _profile_queries(count: int = PROFILE_QUERY_COUNT) -> list[str]:
    """Search phrases from the outreach profile's feature keywords, a
    different run of features each call."""
    from backend.services.social_outreach import profile

    phrases = []
    for feature in profile.load().features:
        keywords = [str(k).strip() for k in (feature.get("keywords") or []) if str(k).strip()]
        if keywords:
            # The longest keyword is the most specific search.
            phrases.append(max(keywords, key=len))
    if not phrases:
        return []
    from backend.tasks.social_outreach_tasks import _next_target

    first = _next_target("intent_profile_queries", phrases)
    start = phrases.index(first) if first in phrases else 0
    picked = [phrases[(start + i) % len(phrases)] for i in range(min(count, len(phrases)))]
    return list(dict.fromkeys(picked))


def _structured_scout_plan(
    *,
    text: str,
    platform: Optional[str],
    action: Optional[str],
    topics: Optional[list[str]],
    max_candidates: Optional[int],
    chain_draft: Optional[bool],
) -> Optional[dict[str, Any]]:
    """When caller already passed platform + topics, skip the LLM."""
    plat = (platform or "").strip().lower() or None
    tops = [t.strip() for t in (topics or []) if t and str(t).strip()]
    act = (action or "").strip().lower() or "comment"
    if not plat or plat not in _SCOUT_PLATFORMS:
        return None
    if not tops:
        return None  # platform alone goes to the classifier with the text
    if act not in _SCOUT_ACTIONS and act != "share":
        return None
    if act == "share":
        return None  # share is not scout_and_draft; handle separately if needed
    return {
        "intent": "scout_and_draft",
        "platform": plat,
        "topics": tops[:8],
        "draft_id": None,
        "confidence": 1.0,
        "reason": "structured override (platform + topics)",
        "raw_text": (text or "").strip(),
        "max_candidates": max(1, min(int(max_candidates or DEFAULT_SCOUT_COUNT), MAX_SCOUT_COUNT)),
        "chain_draft": True if chain_draft is None else bool(chain_draft),
        "subreddit": _extract_subreddit(text or ""),
        "action": act if act != "scout_and_draft" else "comment",
    }


def _extract_subreddit(text: str) -> Optional[str]:
    m = re.search(r"\br/([A-Za-z0-9_]+)", text or "")
    return m.group(1) if m else None


def execute_outreach_intent(
    text: str = "",
    *,
    platform: Optional[str] = None,
    action: Optional[str] = None,
    topics: Optional[list[str]] = None,
    max_candidates: Optional[int] = None,
    chain_draft: Optional[bool] = None,
    created_by: str = "intent",
    classifier: Optional[ClassifierFn] = None,
) -> dict[str, Any]:
    """Classify (or use structured overrides) then dispatch to real outreach ops."""
    structured = _structured_scout_plan(
        text=text,
        platform=platform,
        action=action,
        topics=topics,
        max_candidates=max_candidates,
        chain_draft=chain_draft,
    )

    if structured:
        classification = structured
    else:
        # Explicit action=draft with no topics → draft pass (structured, no LLM)
        act = (action or "").strip().lower()
        if act == "draft" and not (text or "").strip():
            classification = {
                "intent": "scout_and_draft",
                "platform": "reddit",
                "topics": ["__draft_only__"],
                "draft_id": None,
                "confidence": 1.0,
                "reason": "structured draft pass",
                "raw_text": "",
                "max_candidates": max(1, min(int(max_candidates or 5), 25)),
                "chain_draft": False,
                "subreddit": None,
                "action": "draft",
            }
        elif act == "share" and (platform or "").strip().lower() in ("reddit", "self_share", "share"):
            classification = {
                "intent": "scout_and_draft",
                "platform": "reddit",
                "topics": ["__self_share__"],
                "draft_id": None,
                "confidence": 1.0,
                "reason": "structured self_share",
                "raw_text": (text or "").strip(),
                "max_candidates": 5,
                "chain_draft": False,
                "subreddit": _extract_subreddit(text or ""),
                "action": "share",
            }
        elif act == "reply_comments" or (
            not (text or "").strip() and (platform or "").strip().lower() in _SCOUT_PLATFORMS
        ):
            # The caller said what to do; no words to classify.
            wanted = max(1, min(int(max_candidates or DEFAULT_SCOUT_COUNT), MAX_SCOUT_COUNT))
            replying = act == "reply_comments"
            classification = {
                "intent": "reply_comments" if replying else "scout_and_draft",
                "platform": "youtube" if replying else platform.strip().lower(),
                "topics": [],
                "draft_id": None,
                "confidence": 1.0,
                "reason": "structured request",
                "raw_text": (text or "").strip(),
                "max_candidates": wanted,
                "chain_draft": True,
                "subreddit": _extract_subreddit(text or ""),
                "action": "comment",
            }
        else:
            classification = _typed_request(text) or classify_outreach_utterance(
                text, classifier=classifier,
            )
            asked = max_candidates or classification.get("count") or DEFAULT_SCOUT_COUNT
            classification["max_candidates"] = max(1, min(int(asked), MAX_SCOUT_COUNT))
            classification["chain_draft"] = True if chain_draft is None else bool(chain_draft)
            classification["subreddit"] = _extract_subreddit(text or "")
            # Allow optional platform override from caller without forcing scout
            if platform and not classification.get("platform"):
                plat = str(platform).strip().lower()
                if plat in _SCOUT_PLATFORMS:
                    classification["platform"] = plat
            if topics:
                extra = [t.strip() for t in topics if t and str(t).strip()]
                if extra and classification.get("intent") == "scout_and_draft":
                    classification["topics"] = extra[:8]

    return nl_control_plane.dispatch(
        classification,
        {
            "status": _dispatch_status,
            "list_queue": _dispatch_list_queue,
            "approve": _dispatch_approve,
            "reject": _dispatch_reject,
            "post": _dispatch_post,
            "draft_url": lambda c: _dispatch_draft_url(c, created_by=created_by),
            "scout_and_draft": lambda c: _dispatch_scout(c, created_by=created_by),
            "reply_comments": lambda c: _dispatch_reply_comments(c, created_by=created_by),
            "own_posts": lambda c: _dispatch_own_posts(c, created_by=created_by),
            "make_infographic": lambda c: _dispatch_make_infographic(c, created_by=created_by),
        },
        default_handler=_dispatch_refuse,
    )


def _base(classification: dict[str, Any], **extra: Any) -> dict[str, Any]:
    out = {
        "ok": False,
        "intent": classification.get("intent"),
        "classification": {
            "intent": classification.get("intent"),
            "platform": classification.get("platform"),
            "topics": classification.get("topics") or [],
            "draft_id": classification.get("draft_id"),
            "confidence": classification.get("confidence"),
            "reason": classification.get("reason"),
        },
        "plan": {
            "raw_text": classification.get("raw_text") or "",
            "platform": classification.get("platform"),
            "topics": classification.get("topics") or [],
            "intent": classification.get("intent"),
        },
        "queued": [],
        "task_ids": [],
        "refused": False,
    }
    out.update(extra)
    return out


def _dispatch_refuse(classification: dict[str, Any]) -> dict[str, Any]:
    reason = classification.get("reason") or "Could not interpret that as an outreach action."
    return _base(
        classification,
        ok=False,
        refused=True,
        error=reason,
        message=f"Outreach: {reason}",
    )


def _dispatch_status(classification: dict[str, Any]) -> dict[str, Any]:
    enabled = kill_switch.is_enabled()
    cadence = kill_switch.cadence_status()
    recent_jobs = _recent_outreach_tasks(limit=5)
    queue_counts = _queue_counts(platform=classification.get("platform"))

    lines = [
        f"**Outreach:** {'Enabled' if enabled else 'Disabled'} "
        "(every post waits for your approval)",
        "",
    ]
    for plat, value in (cadence or {}).items():
        if value.get("redis") == "unavailable":
            lines.append(f"- {plat}: Redis offline")
        else:
            posts = value.get("posts_in_24h") or 0
            cap = value.get("daily_cap") or 0
            lines.append(f"- {plat}: {posts}/{cap} today")
    lines.append("")
    lines.append(
        f"Queue: {queue_counts.get('drafted', 0)} drafted, "
        f"{queue_counts.get('approved', 0)} approved"
        + (
            f" (platform={classification['platform']})"
            if classification.get("platform")
            else ""
        )
    )
    if recent_jobs:
        lines.append("Recent outreach jobs:")
        for j in recent_jobs:
            lines.append(
                f"- task #{j['id']} {j.get('status')} — {j.get('name') or j.get('type')}"
            )

    return _base(
        classification,
        ok=True,
        message="\n".join(lines),
        status={
            "enabled": enabled,
            "supervised": True,
            "cadence": cadence,
            "queue_counts": queue_counts,
            "recent_jobs": recent_jobs,
        },
    )


def _queue_counts(*, platform: Optional[str] = None) -> dict[str, int]:
    try:
        from backend.models import SocialOutreachLog
        counts: dict[str, int] = {}
        for status in ("drafted", "approved", "candidate"):
            q = SocialOutreachLog.query.filter(SocialOutreachLog.status == status)
            if platform:
                q = q.filter(SocialOutreachLog.platform == platform)
            counts[status] = q.count()
        return counts
    except Exception:
        logger.exception("queue_counts failed")
        return {}


def _recent_outreach_tasks(limit: int = 5) -> list[dict[str, Any]]:
    try:
        from backend.models import Task
        rows = (
            Task.query
            .filter(Task.type.like("social_outreach%"))
            .order_by(Task.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception:
        logger.exception("recent_outreach_tasks failed")
        return []


def _dispatch_list_queue(classification: dict[str, Any]) -> dict[str, Any]:
    try:
        from backend.models import SocialOutreachLog
        statuses = ("drafted", "approved")
        q = SocialOutreachLog.query.filter(SocialOutreachLog.status.in_(statuses))
        plat = classification.get("platform")
        if plat:
            q = q.filter(SocialOutreachLog.platform == plat)
        rows = q.order_by(SocialOutreachLog.created_at.desc()).limit(15).all()
        items = []
        for r in rows:
            items.append({
                "id": r.id,
                "platform": r.platform,
                "status": r.status,
                "grade": r.grade_score,
                "draft": (r.draft_text or "")[:160],
                "target_url": r.target_url,
            })
        if not items:
            msg = "Outreach queue is empty (no drafted/approved rows)."
        else:
            lines = [f"**Outreach queue** ({len(items)} rows):"]
            for it in items:
                lines.append(
                    f"- #{it['id']} [{it['platform']}/{it['status']}] "
                    f"grade={it['grade']} — {it['draft'] or '(no draft)'}"
                )
            msg = "\n".join(lines)
        return _base(
            classification,
            ok=True,
            message=msg,
            queue=items,
        )
    except Exception as e:
        logger.exception("list_queue failed")
        return _base(classification, ok=False, error=str(e), message=str(e))


def _dispatch_approve(classification: dict[str, Any]) -> dict[str, Any]:
    draft_id = classification.get("draft_id")
    if draft_id is None:
        return _dispatch_refuse({
            **classification,
            "intent": "refuse",
            "reason": "Approve needs a draft id (e.g. 'approve draft 42').",
        })
    try:
        from backend.models import SocialOutreachLog
        if not transitions.approve(int(draft_id)):
            status = transitions.current_status(int(draft_id))
            if status is None:
                return _base(
                    classification,
                    ok=False,
                    error=f"draft {draft_id} not found",
                    message=f"Draft #{draft_id} not found.",
                )
            refusal = transitions.approval_refusal(int(draft_id))
            return _base(
                classification,
                ok=False,
                error=refusal,
                message=f"Draft #{draft_id} was not approved: {refusal}.",
            )
        row = SocialOutreachLog.query.get(int(draft_id))
        return _base(
            classification,
            ok=True,
            message=f"Approved draft #{draft_id}. {platforms.after_approval(row.platform)}",
            draft={"id": row.id, "status": row.status, "platform": row.platform},
        )
    except Exception as e:
        logger.exception("approve via intent failed")
        return _base(classification, ok=False, error=str(e), message=str(e))


def _dispatch_reject(classification: dict[str, Any]) -> dict[str, Any]:
    draft_id = classification.get("draft_id")
    if draft_id is None:
        return _dispatch_refuse({
            **classification,
            "intent": "refuse",
            "reason": "Reject needs a draft id (e.g. 'reject draft 42').",
        })
    try:
        from backend.models import SocialOutreachLog
        outcome = transitions.reject(int(draft_id))
        if outcome.status is None:
            return _base(
                classification,
                ok=False,
                error=f"draft {draft_id} not found",
                message=f"Draft #{draft_id} not found.",
            )
        if not outcome.rejected:
            refusal = transitions.reject_refusal(outcome.status)
            return _base(
                classification,
                ok=False,
                error=refusal,
                message=f"Draft #{draft_id} was not rejected: {refusal}.",
            )
        row = SocialOutreachLog.query.get(int(draft_id))
        message = f"Rejected draft #{draft_id}."
        if outcome.status == "processing":
            message += " It had been picked up for posting and was stopped before it was submitted."
        return _base(
            classification,
            ok=True,
            message=message,
            draft={"id": row.id, "status": row.status, "platform": row.platform},
        )
    except Exception as e:
        logger.exception("reject via intent failed")
        return _base(classification, ok=False, error=str(e), message=str(e))


def _dispatch_post(classification: dict[str, Any]) -> dict[str, Any]:
    """Start the one post for an approved draft, as Post now does."""
    draft_id = classification.get("draft_id")
    if draft_id is None:
        return _dispatch_refuse({
            **classification,
            "intent": "refuse",
            "reason": "Post needs a draft id (e.g. 'post draft 42').",
        })
    try:
        from backend.services.social_outreach import watched_post

        outcome = watched_post.start(int(draft_id))
    except Exception as e:
        logger.exception("post via intent failed")
        return _base(classification, ok=False, error=str(e), message=str(e))
    if outcome.status is None:
        return _base(
            classification, ok=False,
            error=f"draft {draft_id} not found", message=f"Draft #{draft_id} not found.",
        )
    return _base(
        classification,
        ok=bool(outcome.started),
        error=None if outcome.started else outcome.message,
        message=outcome.message,
        draft_ids=[int(draft_id)],
    )


def _dispatch_facebook_link(
    classification: dict[str, Any], url: str, *, created_by: str,
) -> Optional[dict[str, Any]]:
    """Queue the read a link into a Facebook group needs: a post is read and
    answered, a group is scouted. None for any other Facebook link."""
    from backend.services.social_outreach import facebook_outreach as fb

    post = fb.group_post_ref(url)
    group = None if post else fb.group_ref(url)
    if not post and not group:
        return None
    if not kill_switch.is_enabled():
        return _kill_off(classification)
    after_id = audit.latest_id()
    try:
        if post:
            queued = [queue_outreach_run(
                "facebook", post_url=fb.group_post_url(*post), batch_size=1,
                chain_draft=True, created_by=created_by,
            )]
            looking = "Reading that Facebook post to draft a reply"
        else:
            wanted = int(classification.get("max_candidates") or DEFAULT_SCOUT_COUNT)
            queued = [queue_outreach_run(
                "facebook", groups=[group], batch_size=wanted,
                chain_draft=True, created_by=created_by,
            )]
            looking = "Looking in that Facebook group for posts that fit your outreach profile"
    except (ValueError, RuntimeError) as e:
        return _base(classification, ok=False, error=str(e), message=str(e))
    return _queued_ok(classification, queued, plat="facebook", after_id=after_id, looking=looking)


def _dispatch_draft_url(classification: dict[str, Any], *, created_by: str = "outreach") -> dict[str, Any]:
    """Draft a reply to the page a pasted link points at; it waits for approval."""
    url = classification.get("url") or ""
    refusal = platforms.refusal_for_url(url)
    if refusal:
        return _dispatch_refuse({**classification, "intent": "refuse", "reason": refusal})
    platform = classification.get("platform")
    if platform == "facebook":
        # A group's pages are read through the signed-in agent browser.
        queued = _dispatch_facebook_link(classification, url, created_by=created_by)
        if queued is not None:
            return queued
    if not platform:
        return _dispatch_refuse({
            **classification,
            "intent": "refuse",
            "reason": "That link is not on Reddit, YouTube, Facebook or Discord, the places outreach replies.",
        })
    try:
        from backend.tools.outreach_tools import OutreachDraftPostTool

        result = OutreachDraftPostTool().execute(platform=platform, mode="comment", target_url=url)
    except Exception as e:
        logger.exception("draft for a link via intent failed")
        return _base(classification, ok=False, error=str(e), message=str(e))
    output = result.output if isinstance(result.output, dict) else {}
    if not result.success or not output.get("audit_id"):
        reason = result.error or "No draft was written for that link."
        return _base(classification, ok=False, error=reason, message=reason)
    return _base(
        classification,
        ok=True,
        message=(
            f"Drafted a reply for {url} as draft #{output['audit_id']}. "
            "It waits for your approval; nothing posts until you approve it."
        ),
        draft_ids=[output["audit_id"]],
        supervised=True,
        posts_require_approve=True,
    )


def _dispatch_reply_comments(classification: dict[str, Any], *, created_by: str) -> dict[str, Any]:
    """Queue a read of the new comments on the person's own YouTube videos,
    with a reply drafted for each."""
    from backend.services.social_outreach import api_post

    if api_post.account() is None:
        return _dispatch_refuse({
            **classification,
            "intent": "refuse",
            "reason": (
                "Reading the comments on your videos needs your YouTube account: connect it under "
                "Social Accounts and switch on Outreach comments."
            ),
        })
    if not kill_switch.is_enabled():
        return _kill_off(classification)
    after_id = audit.latest_id()
    try:
        queued = [
            queue_outreach_run(
                "youtube_replies",
                batch_size=classification.get("max_candidates") or DEFAULT_SCOUT_COUNT,
                chain_draft=True,
                created_by=created_by,
            )
        ]
    except (ValueError, RuntimeError) as e:
        return _base(classification, ok=False, error=str(e), message=str(e))
    return _queued_ok(
        classification, queued, plat="youtube", after_id=after_id,
        looking="Reading the new comments on your YouTube videos",
    )


def _dispatch_make_infographic(classification: dict[str, Any], *, created_by: str) -> dict[str, Any]:
    """Queue one new infographic written from the pitch sheet."""
    if not kill_switch.is_enabled():
        return _kill_off(classification)
    after_id = audit.latest_id()
    try:
        queued = [queue_outreach_run("make_infographic", topic=classification.get("topic") or "", created_by=created_by)]
    except (ValueError, RuntimeError) as e:
        return _base(classification, ok=False, error=str(e), message=str(e))
    task_ids = [q.get("task_id") for q in queued if q.get("task_id")]
    return _base(
        classification,
        ok=True,
        queued=queued,
        task_ids=task_ids,
        message=(
            f"Writing an infographic from your pitch sheet and rendering it (task #{task_ids[0]}). "
            "It is not used for outreach until you mark it on the Outreach page."
        ),
        supervised=True,
        follow={"task_ids": task_ids, "after_id": after_id, "platform": None},
    )


def _dispatch_own_posts(classification: dict[str, Any], *, created_by: str) -> dict[str, Any]:
    """Queue posts of the account's own, each with an infographic the person kept."""
    from backend.services.social_outreach import infographics

    if not kill_switch.is_enabled():
        return _kill_off(classification)
    if not any(item.get("usable") for item in infographics.items(keepers_only=True)):
        return _dispatch_refuse({
            **classification,
            "intent": "refuse",
            "reason": (
                "There is no infographic to post yet. On the Outreach page, mark the ones you want "
                "used; each is usable once its text has been read back and matches."
            ),
        })
    plat = classification.get("platform") or "facebook"
    after_id = audit.latest_id()
    try:
        queued = [
            queue_outreach_run(
                "own_posts",
                post_on=plat,
                communities=classification.get("communities") or None,
                batch_size=classification.get("count") or None,
                chain_draft=True,
                created_by=created_by,
            )
        ]
    except (ValueError, RuntimeError) as e:
        return _base(classification, ok=False, error=str(e), message=str(e))
    where = "your subreddits" if plat == "reddit" else "your Facebook groups"
    return _queued_ok(
        classification, queued, plat=plat, after_id=after_id,
        looking=f"Choosing an infographic for {where} and writing the post",
    )


def _dispatch_scout(classification: dict[str, Any], *, created_by: str) -> dict[str, Any]:
    conf = float(classification.get("confidence") or 0.0)
    topics = list(classification.get("topics") or [])
    plat = classification.get("platform")
    action = (classification.get("action") or "comment").strip().lower()

    # Structured draft-only / self_share sentinels
    if topics == ["__draft_only__"]:
        if not kill_switch.is_enabled():
            return _kill_off(classification)
        after_id = audit.latest_id()
        try:
            queued = [queue_outreach_run("draft", created_by=created_by)]
        except (ValueError, RuntimeError) as e:
            return _base(classification, ok=False, error=str(e), message=str(e))
        return _queued_ok(
            classification, queued, plat=None, after_id=after_id,
            looking="Drafting the candidates already found",
        )

    if topics == ["__self_share__"] or action == "share":
        if not kill_switch.is_enabled():
            return _kill_off(classification)
        after_id = audit.latest_id()
        try:
            queued = [
                queue_outreach_run(
                    "self_share",
                    subreddit=classification.get("subreddit"),
                    created_by=created_by,
                )
            ]
        except (ValueError, RuntimeError) as e:
            return _base(classification, ok=False, error=str(e), message=str(e))
        return _queued_ok(
            classification, queued, plat="reddit", after_id=after_id,
            looking="Preparing a share post for Reddit",
        )

    if conf < _MIN_SCOUT_CONFIDENCE:
        return _dispatch_refuse({
            **classification,
            "intent": "refuse",
            "reason": (
                f"Not confident enough to scout (confidence={conf:.2f} < "
                f"{_MIN_SCOUT_CONFIDENCE}). Say which platform, e.g. "
                "'reply to some posts on Reddit about ComfyUI'."
            ),
        })

    topics = [t for t in topics if t and not t.startswith("__")]
    if plat not in _SCOUT_PLATFORMS:
        return _dispatch_refuse({
            **classification,
            "intent": "refuse",
            "reason": "Say where to look: Reddit, YouTube, Facebook or Discord.",
        })

    if not kill_switch.is_enabled():
        return _kill_off(classification)

    max_c = max(1, min(int(classification.get("max_candidates") or DEFAULT_SCOUT_COUNT), MAX_SCOUT_COUNT))
    subreddit = classification.get("subreddit")
    from_profile = False
    queued: list[dict[str, Any]] = []
    after_id = audit.latest_id()

    try:
        if plat == "youtube":
            if not topics:
                topics, from_profile = _profile_queries(), True
            if not topics:
                return _no_profile_keywords(classification)
            queued.append(
                queue_outreach_run(
                    "youtube",
                    keyword_profiles=topics,
                    batch_size=max_c,
                    chain_draft=True,
                    created_by=created_by,
                )
            )
        elif plat == "reddit":
            searches = topics
            if not searches and not subreddit:
                searches, from_profile = _profile_queries(), True
                if not searches:
                    return _no_profile_keywords(classification)
            # With topics (or nothing named) Reddit's search is read; a named
            # subreddit with no topic is scouted from its front listing.
            queued.append(
                queue_outreach_run(
                    "recon",
                    subreddit=subreddit,
                    search_queries=searches or None,
                    batch_size=max_c,
                    chain_draft=True,
                    created_by=created_by,
                )
            )
            topics = searches
        elif plat == "discord":
            queued.append(
                queue_outreach_run("discord", created_by=created_by)
            )
        elif plat == "facebook":
            # The newest posts of the saved groups, or of the groups the
            # account is in that match the profile; named topics narrow them.
            queued.append(
                queue_outreach_run(
                    "facebook",
                    keyword_profiles=topics or None,
                    batch_size=max_c,
                    chain_draft=True,
                    created_by=created_by,
                )
            )
    except (ValueError, RuntimeError) as e:
        return _base(classification, ok=False, error=str(e), message=str(e))

    label = platforms.label_for(plat)
    where = f" in r/{subreddit}" if plat == "reddit" and subreddit else ""
    place = "in your Facebook groups" if plat == "facebook" else f"on {label}{where}"
    if topics:
        about = ", ".join(topics)
        source = " (from your outreach profile, since you named no topic)" if from_profile else ""
        looking = f"Looking {place} for posts about {about}{source}"
    else:
        looking = f"Looking {place} for posts that fit your outreach profile"
    return _queued_ok(
        classification, queued, plat=plat, after_id=after_id, looking=looking, topics=topics,
        topics_from_profile=from_profile,
    )


def _no_profile_keywords(classification: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_refuse({
        **classification,
        "intent": "refuse",
        "reason": (
            "You named no topic and the outreach profile has no keywords to search with. "
            "Name a topic, or add keywords to the profile on the Outreach page."
        ),
    })


def _kill_off(classification: dict[str, Any]) -> dict[str, Any]:
    err = (
        "outreach is disabled (kill switch is off). "
        "Enable it from /outreach or the Outreach page first."
    )
    return _base(classification, ok=False, error=err, message=err)


def _queued_ok(
    classification: dict[str, Any],
    queued: list[dict[str, Any]],
    *,
    plat: Optional[str],
    after_id: int,
    looking: str,
    topics: Optional[list[str]] = None,
    topics_from_profile: bool = False,
) -> dict[str, Any]:
    task_ids = [q.get("task_id") for q in queued if q.get("task_id")]
    tasks = ", ".join(f"#{t}" for t in task_ids)
    summary = (
        f"{looking} (task {tasks}). Drafts wait for your approval; "
        "nothing posts until you approve it."
    )
    out = _base(
        classification,
        ok=True,
        queued=queued,
        task_ids=task_ids,
        message=summary,
        supervised=True,
        posts_require_approve=True,
        # What a caller needs to show this run's drafts once the tasks end
        # (run_result.run_result).
        follow={"task_ids": task_ids, "after_id": after_id, "platform": plat},
    )
    out["plan"]["topics"] = topics or []
    out["plan"]["topics_from_profile"] = topics_from_profile
    return out
