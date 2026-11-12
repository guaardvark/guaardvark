"""
Outward-facing persona block for social outreach.

Lives here so every social-outreach surface (Discord cog, Reddit loop, self-share)
pulls from the same source of truth. Don't fork these strings.

Facts about what is being promoted come from the local profile
(backend/services/social_outreach/profile.py) and, when present,
data/agent/PITCH.md. Nothing here names a product: a fresh install drafts
nothing until its profile is filled in.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import ollama

from backend.services.social_outreach import profile
from backend.utils.hosts import host_matches

logger = logging.getLogger(__name__)

# Path to the human-editable pitch sheet. When present it is what the model
# reads at draft time, ahead of the sheet built from the profile. Edits go
# live on the next draft call without a restart.
_PITCH_MD_PATH = Path(
    os.environ.get("GUAARDVARK_ROOT") or Path(__file__).resolve().parents[3]
) / "data" / "agent" / "PITCH.md"

# (mtime, content) cache. Re-read when the file's mtime changes — edits
# to PITCH.md are live without a process restart, which is the whole
# point of moving the pitch out of code.
_pitch_cache: Tuple[float, str] = (0.0, "")


def _load_pitch_md() -> str:
    """Return the current PITCH.md content. Caches by mtime so the common
    case (every draft call in a short window) is a stat + dict lookup.

    Returns "" if the file is missing or unreadable — the system blocks
    still function without it, just without injected facts, which is
    better than crashing the outreach loop on a missing file.
    """
    global _pitch_cache
    try:
        st = _PITCH_MD_PATH.stat()
    except FileNotFoundError:
        return ""
    except OSError as e:
        logger.warning("PITCH.md stat failed: %s", e)
        return _pitch_cache[1]  # fall back to last good copy
    cached_mtime, cached_content = _pitch_cache
    if cached_mtime == st.st_mtime and cached_content:
        return cached_content
    try:
        content = _PITCH_MD_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("PITCH.md read failed: %s", e)
        return cached_content  # fall back to last good copy
    _pitch_cache = (st.st_mtime, content)
    return content


# Operator identity for outward-facing copy. A fresh install must NEVER post under
# a real person's name, so this defaults to a generic, name-free reference. Set
# GUAARDVARK_OPERATOR_NAME in your .env to personalize the voice (recommended — so
# your outreach reads as a person, not a faceless brand).
_OPERATOR_NAME = (os.environ.get("GUAARDVARK_OPERATOR_NAME") or "").strip()


def _product_name() -> str:
    """The profile's name, or a neutral stand-in when none is set."""
    return profile.load().name or "the project"


def _account_ref() -> str:
    name = profile.load().name
    if not name:
        return f"{_OPERATOR_NAME}'s account" if _OPERATOR_NAME else "your own account"
    return f"{_OPERATOR_NAME}'s {name} account" if _OPERATOR_NAME else f"the {name} account"


def _video_owner_ref() -> str:
    name = profile.load().name
    if not name:
        return f"{_OPERATOR_NAME}'s own YouTube videos" if _OPERATOR_NAME else "your own YouTube videos"
    return (
        f"{_OPERATOR_NAME}'s own {name} YouTube videos" if _OPERATOR_NAME
        else f"the {name} channel's own YouTube videos"
    )


# Framing prepended to the pitch sheet when generating outward-facing copy.
# It carries no voice rules, no forbidden-phrase list and no opinionated
# rubric: those kneecapped friendlier model families and forced every output
# through one guarded register. The model writes in its own voice and the
# pitch sheet carries the facts. If a model's natural voice drifts off-tone,
# nudge the sheet, not the code.
_OUTWARD_FACING_FRAMING = """The pitch sheet below is the source of truth — don't invent features
that aren't in it, don't fabricate URLs, don't claim more than what's
written. Within those guardrails, write in your own voice.

Self-grade the draft 0.0-1.0 on TWO axes — take the LOWER of the two:
  (1) Does this add real value to the thread without reading as promotion?
  (2) Would a generic, copy-paste version of this draft work on any
      other thread about the same topic? If yes, grade it down. That's
      the templated/robotic failure mode.

A post-worthy draft has to respond to specifics in THIS thread — the OP's
exact question, their hardware, their stack, the comments above. Vague
replies that could fit anywhere fail axis (2) even if they sound fine.

0.7+ is post-worthy; below that, hold. If nothing in the pitch sheet
credibly fits the thread, return draft="" and grade<0.3 — better to skip
than to force a mention.

Return JSON: {"draft": "<comment text>", "grade": 0.0-1.0, "reason": "<one line>"}.
"""

# Framing for SELF-SHARE posts (we're submitting our own link to a community,
# not commenting on someone else's thread). Distinct from _OUTWARD_FACING_FRAMING
# because that one tells the model to grade against "does this add value to the
# THREAD", and to return draft="" when nothing fits — both of which cause the
# model to refuse a legitimate share task ("there's no thread, so I'll skip").
# We also leave the JSON SHAPE to the user prompt so reddit can ask for
# {title, body} and discord/etc can ask for {draft}.
_SHARE_FRAMING_SYSTEM = """This is a fresh top-level post (or message), NOT a reply to an existing
thread. The pitch sheet below is the source of truth — don't invent
features that aren't in it, don't fabricate URLs, don't claim more than
what's written. Within those guardrails, write in your own voice.

Self-grade the draft 0.0-1.0 on "does this introduce {name} in a way
that respects the target community's vibe and doesn't read as low-effort
spam?". 0.7+ is post-worthy. The user prompt specifies the exact JSON
schema to return — follow that schema, not a generic one.
"""


# Framing for a post of the account's own that carries an infographic.
_POST_FRAMING = """This is a new post of your own in a community, NOT a reply. An image is
attached to it: an infographic. Readers see the image, so the words with it
do not repeat it line for line. They say, the way this person would, what it
is and why someone in this community would care.

The pitch sheet below is the source of truth: claim nothing beyond what it
and the image state, and don't fabricate URLs.

Self-grade the draft 0.0-1.0 on "would a member of this community read this
as one of them sharing something they built, not as an advert?". 0.7+ is
post-worthy. The user prompt specifies the exact JSON schema to return.
"""


# Framing for REPLIES on the account's OWN YouTube videos. Different audience
# (already engaged, watched the video) so different posture (no pitch,
# no link, no marketing gate). Same factual ground from the pitch sheet.
_REPLY_FRAMING = """The viewer already watched and engaged — you
are NOT pitching to a stranger. Be human about it. Answer questions if asked,
take critique on the merits, acknowledge kindness briefly without overselling
gratitude.

No link, no "check out", no invitation to try or download anything, no
disclosure line, no recap of the video. The one exception: the viewer asked
how it was made or where to get it; then say so plainly, and name the tool
or give one link only if they asked for it.

The reference sheet is for facts only: use a specific from it when the
viewer asked about a feature. Its voice rules, its disclosure line and its
links are written for comments on other people's threads and do not apply to
a reply on your own video.

Plain praise with nothing to answer (a few emoji, "cool", "this is so good")
gets a thank-you: one short line, twelve words at most, in the plain way a
person thanks a viewer. Pick up on what they said or on the video if there is
anything to pick up on; no exclamation-mark pile-up, no question tacked on
to drive engagement, and nothing after the thanks.

Self-grade 0.0-1.0 on "does this read like a real creator actually engaging
with their viewer, or like a templated auto-response?". 0.6+ means post.
If the incoming comment should get no reply at all (spam, hostile,
off-topic), return draft="" and grade=0.0.

Return JSON: {"draft": "<reply text>", "grade": 0.0-1.0, "reason": "<one line>"}.
"""


def _pitch_sheet() -> str:
    """PITCH.md when present; otherwise a sheet built from the profile. ""
    when neither exists: there is nothing to promote."""
    pitch = _load_pitch_md().strip()
    if pitch:
        return pitch
    return profile.load().pitch_sheet()


def _compose_outward_facing_system(platform: Optional[str] = None) -> str:
    """Build the system message: framing + pitch sheet. Both are re-read on
    every call (mtime-cached), so edits propagate without restarting the worker."""
    framing = f"You are writing a comment or post under {_account_ref()}.\n{_OUTWARD_FACING_FRAMING}"
    pitch = _pitch_sheet()
    if not pitch:
        return _voiced_system(framing, platform)
    return _voiced_system(f"{framing}\n\n--- PITCH SHEET ---\n{pitch}\n", platform)


def _voiced_system(system: str, platform: Optional[str] = None) -> str:
    """``system`` with the account owner's voice sheet after it, when they keep one."""
    from backend.services.social_outreach import voice

    block = voice.block(platform=platform)
    return f"{system}\n\n{block}" if block else system


def _draft_in_voice(
    llm, system: str, prompt: str, keys: tuple, platform: Optional[str] = None,
) -> tuple[dict, str]:
    """Call the drafter and return (result, draft text). A draft that uses
    something the voice sheet lists under Never is written once more with
    those named; the version with fewer of them is kept."""
    from backend.services.social_outreach import voice

    def text_of(result: dict) -> str:
        return next((result.get(k) for k in keys if result.get(k)), "") or ""

    result = llm(system, prompt)
    draft = text_of(result)
    found = voice.hits(draft, platform)
    if not found:
        return result, draft
    shown = ", ".join(repr(f[3:].strip() if f.startswith("re:") else f) for f in found)
    again = llm(system, (
        f"{prompt}\n\nYour previous draft was:\n{draft}\n\n"
        f"It used {shown}, which this person never writes. Write it again in their voice "
        "without those, saying the same things."
    ))
    second = text_of(again)
    if second and len(voice.hits(second, platform)) < len(found):
        return again, second
    return result, draft


def _compose_reply_system(platform: Optional[str] = None) -> str:
    """Same idea as _compose_outward_facing_system but for replies-on-own-video."""
    framing = f"You are writing a REPLY to a comment left on one of {_video_owner_ref()}.\n{_REPLY_FRAMING}"
    pitch = _pitch_sheet()
    if not pitch:
        return framing
    # The sheet goes first and the reply rules last: a long sheet of sales
    # voice rules after the framing was followed instead of it (measured
    # 2026-11-11: every reply to plain praise carried a link).
    return _voiced_system(
        "--- REFERENCE SHEET (facts only; its voice rules and links are not for this reply) ---\n"
        f"{pitch}\n--- END OF REFERENCE SHEET ---\n\n{framing}",
        platform,
    )


def _compose_post_system(platform: Optional[str] = None) -> str:
    """The system message for a post of the account's own with an infographic."""
    framing = f"You are writing a post under {_account_ref()}.\n{_POST_FRAMING}"
    pitch = _pitch_sheet()
    if not pitch:
        return _voiced_system(framing, platform)
    return _voiced_system(f"{framing}\n\n--- PITCH SHEET ---\n{pitch}\n", platform)


def _build_post_prompt(platform: str, community: str, infographic: dict, link_url: str) -> str:
    """User-side prompt for a post with an infographic: where it goes, what the
    image says, and the shape the words take on that platform."""
    said = [str(infographic.get("headline") or "").strip()]
    said += [str(line).strip() for line in infographic.get("lines") or []]
    image_text = "\n".join(f"  {line}" for line in said if line) or "  (its text is not recorded)"
    if platform == "reddit":
        shape = (
            "Write the post's TITLE: one line of at most 150 characters, plain words, "
            "no link, no hashtags, no emoji. It says what the image shows."
        )
    else:
        shape = (
            "Write one to three short sentences to go above the image"
            + (f", ending with this link on the same line: {link_url}" if link_url else "")
            + ". No hashtags."
        )
    return f"""\
PLATFORM: {platform}
COMMUNITY: {community}

THE ATTACHED INFOGRAPHIC SAYS:
{image_text}

INSTRUCTIONS:
{shape}
Do not copy the infographic's lines; the reader has them in front of them.

Respond with JSON: {{"draft": "...", "grade": 0.0-1.0, "reason": "..."}}
"""


def _compose_share_system() -> str:
    """Same idea as _compose_outward_facing_system but for self-share posts."""
    framing = (
        f"You are writing a SELF-SHARE post under {_account_ref()}.\n"
        + _SHARE_FRAMING_SYSTEM.format(name=_product_name())
    )
    pitch = _pitch_sheet()
    if not pitch:
        return framing
    return f"{framing}\n\n--- PITCH SHEET ---\n{pitch}\n"


# Back-compat: anywhere in the codebase that imported these constants
# directly still works. The value is framing-only (no facts and no account
# line) — callers that want the full prompt should use draft_outreach_text,
# which always composes the pitch sheet in.
OUTWARD_FACING_SYSTEM_BLOCK = _OUTWARD_FACING_FRAMING
REPLY_TO_OWN_VIDEO_SYSTEM_BLOCK = _REPLY_FRAMING


# Tone presets selectable in the OutreachPage UI. Each one is a small extra
# instruction we splice into the prompt — they shape the draft without
# overriding the core voice rules above.
TONE_GUIDES = {
    "default": "",  # use voice rules as-is
    "engaging": "Lean toward warm, curious, conversational. Ask a clarifying question if the OP left a gap.",
    "technical": "Lean technical. Precise terms over folksy ones. Mention specifics (model names, RAM, latency) when relevant.",
    "casual": "Casual and brief. Short sentences. Sound like you're typing on your phone.",
    "formal": "Slightly more formal — full sentences, no contractions. Still concise; never stiff.",
    "humorous": "Land one dry, understated joke if the thread tone allows. Skip the joke if it would feel out of place.",
}


# Per-platform framing for self-share posts; {name} is the profile's name.
SHARE_FRAMING = {
    "reddit": (
        "Write a Reddit post title (under 100 chars) and body (under 400 chars) "
        "introducing {name} to a relevant subreddit. The title must be a real "
        "hook, not a pitch. The body explains what it is in plain language and what "
        "specifically might interest THIS subreddit's audience. End with the link."
    ),
    "discord": (
        "Write a 2–4 sentence Discord message introducing {name} to a topical "
        "channel. Conversational. Mention what specifically might interest this "
        "channel's audience. Drop the link inline."
    ),
}

# Phrases in a subreddit's rules-sidebar that mean "do not post promotional content here".
# Used by the Reddit loop to abort gracefully before commenting/sharing.
NO_PROMO_RULE_PATTERNS = [
    r"no\s+self[\s-]?promo",
    r"no\s+self[\s-]?promotion",
    r"no\s+advertis(ing|ements?)",
    r"no\s+marketing",
    r"no\s+links\s+to\s+(your|own)",
    r"posts\s+from\s+content\s+creators",
    r"\b9[\s-]?to[\s-]?1\s+rule\b",
]


def find_relevant_feature(text: str) -> str | None:
    """Key of the first profile feature with a keyword in the text, or None."""
    return profile.find_relevant_feature(text)


def drafter_model() -> str:
    """The model outreach drafts are written with: the default chat model."""
    from backend.config import get_default_llm
    return get_default_llm()


def _ollama_json_chat(system: str, user: str, model: Optional[str] = None) -> Dict[str, Any]:
    """One-shot Ollama call that returns parsed JSON. Best-effort.

    Forces format=json so the model emits valid JSON; we still try/except
    around the parse because models occasionally truncate or wrap.
    """
    if model is None:
        model = drafter_model()

    from backend.utils.ollama_resource_manager import think_payload
    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format="json",
            options={"temperature": 0.6},
            **think_payload(model),
        )
    except Exception as e:
        logger.error("ollama.chat failed: %s", e)
        return {"draft": "", "grade": 0.0, "reason": f"LLM unavailable: {e}"}

    msg = getattr(response, "message", None)
    if msg is None and isinstance(response, dict):
        msg = response.get("message")
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content", "")
    raw = (content or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning("ollama returned non-JSON: %s", raw[:200])
        return {"draft": "", "grade": 0.0, "reason": "model returned malformed JSON"}


def _build_reply_prompt(
    parent_text: str,
    incoming_text: str,
    incoming_author: str = "",
    video_title: str = "",
    tone: Optional[str] = None,
) -> str:
    """Compose the user-side prompt for drafting a REPLY to an incoming
    comment on the account's own video. Used with REPLY_TO_OWN_VIDEO_SYSTEM_BLOCK.

    No feature-blurb / no link / no UTM tags — replies don't pitch.
    """
    tone_guide = TONE_GUIDES.get((tone or "default").lower(), "").strip()
    tone_line = f"\nTONE: {tone_guide}\n" if tone_guide else ""
    author_line = f" by @{incoming_author}" if incoming_author else ""
    video_line = f"VIDEO: {video_title}\n" if video_title else ""
    if (parent_text or "").strip():
        exchange = (
            f"YOUR EARLIER COMMENT (the one they replied to):\n{parent_text}\n\n"
            f"THEIR REPLY{author_line}:\n{incoming_text}"
        )
    else:
        exchange = f"THEIR COMMENT ON YOUR VIDEO{author_line}:\n{incoming_text}"
    return f"""\
{video_line}{exchange}
{tone_line}
Draft a reply to them. Follow the voice rules from the system message.
Return JSON: {{"draft": "<reply text>", "grade": 0.0-1.0, "reason": "<one line>"}}.
"""


def _requested_feature_block(requested_feature: Optional[str], fits: str) -> str:
    """Prompt lines asking the drafter to lead with a feature a person chose.

    A profile feature key is shown with its blurb; anything else is passed as
    the person wrote it. ``fits`` names what the feature has to suit ("this
    thread", "this community"). Returns "" when no feature was requested.
    """
    feature = (requested_feature or "").strip()[:200]
    if not feature:
        return ""
    blurb = profile.feature_blurbs().get(feature.lower())
    label = f"{feature} ({blurb})" if blurb else feature
    return (
        f"\nREQUESTED FEATURE: {label}\n"
        f"The person asked for a draft that leads with this feature. If it fits "
        f"{fits} and the pitch sheet backs it, lead with it. If it does not fit, "
        f"do not force it: grade below 0.3 and say in reason why it does not fit.\n"
    )


def _build_user_prompt(
    platform: str,
    thread_context: str,
    target_url: Optional[str],
    tone: Optional[str] = None,
    include_link: bool = False,
    link_url: Optional[str] = None,
    requested_feature: Optional[str] = None,
) -> str:
    """Compose the user-side prompt for the LLM.

    Facts about the product live in the system message (the pitch sheet);
    the drafter picks the talking point that fits the thread from it,
    unless a person passed `requested_feature` for it to lead with. This
    side carries the thread, that request and the output contract.

    `include_link=True` asks for a natural cite of `link_url`. Low-grade
    spam still fails the gate.
    """
    tone_guide = TONE_GUIDES.get((tone or "default").lower(), "").strip()
    tone_block = f"\nTONE OVERRIDE: {tone_guide}\n" if tone_guide else ""
    name = _product_name()

    if include_link:
        closing_line = (
            f"Draft a comment for this thread. Lead with real value about "
            f"the topic, then include a link to {(link_url or '').strip()} where it fits "
            f"naturally — one short mention of {name} plus the URL. "
            f"Prefer the repo link when the viewer might want to try or "
            f"clone the project. If you can't make the link feel natural, "
            f"set grade < 0.7 (the human reviewer would rather hold than "
            f"ship spam)."
        )
    else:
        closing_line = (
            f"Draft a comment for this thread. Add real value first; a "
            f"mention of {name} is optional and only fits sometimes."
        )

    feature_block = _requested_feature_block(requested_feature, "this thread")

    return f"""\
PLATFORM: {platform}
TARGET URL: {target_url or "(unknown)"}

THREAD CONTEXT:
\"\"\"
{thread_context.strip()[:4000]}
\"\"\"
{tone_block}
{closing_line}
{feature_block}
Respond with JSON: {{"draft": "...", "grade": 0.0-1.0, "reason": "..."}}.
"""


def _draft_already_has_cta(draft_text: str, link_url: str) -> bool:
    """True if draft already cites the CTA link, the profile's site or its repo."""
    lower = (draft_text or "").lower()
    if any(link in lower for link in profile.load().own_links()):
        return True
    needle = (link_url or "").strip().lower()
    if needle and needle in lower:
        return True
    # Accept short forms / watch vs shorts variants of the same video id.
    if "youtube.com/" in needle or "youtu.be/" in needle:
        vid = re.search(r"(?:shorts/|watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})", needle)
        if vid and vid.group(1).lower() in lower:
            return True
    return False


def _unpack_reddit_share(result: Dict[str, Any]) -> Tuple[str, str]:
    """Pull (title, body) from a Reddit share LLM result, surviving the
    schema variations models actually produce in the wild:

      - {"title": "...", "body": "..."} — what we asked for
      - {"draft": "Title\\n\\nBody..."} — the comment-shape default; split it
      - {"draft": "single line"} — title only, empty body
      - {"post": {"title": "...", "body": "..."}} — nested wrapper

    Returns ("", "") only when nothing usable was produced.
    """
    title = (result.get("title") or "").strip()
    body = (result.get("body") or "").strip()
    if title or body:
        return title[:300], body[:1500]

    nested = result.get("post") or result.get("submission")
    if isinstance(nested, dict):
        title = (nested.get("title") or "").strip()
        body = (nested.get("body") or nested.get("selftext") or "").strip()
        if title or body:
            return title[:300], body[:1500]

    draft = (result.get("draft") or result.get("text") or result.get("content") or "").strip()
    if draft:
        # Split on first blank line if present, else first newline. Whatever's
        # before becomes the title; the rest is body. Models writing prose by
        # default use this layout, so the heuristic almost always matches.
        parts = re.split(r"\n\s*\n", draft, maxsplit=1)
        if len(parts) == 2:
            return parts[0].strip()[:300], parts[1].strip()[:1500]
        line, _, rest = draft.partition("\n")
        return line.strip()[:300], rest.strip()[:1500]

    return "", ""


def _build_share_prompt(
    platform: str,
    target: str,
    link_url: str,
    requested_feature: Optional[str] = None,
) -> str:
    """User-side prompt for self-share posts. Facts come from the pitch sheet
    via the system message; this side carries the platform/target/link and a
    feature a person asked the post to lead with, if any.
    """
    framing = SHARE_FRAMING.get(platform, SHARE_FRAMING["reddit"]).format(name=_product_name())
    feature_block = _requested_feature_block(requested_feature, "this community")
    return f"""\
PLATFORM: {platform}
TARGET COMMUNITY: {target}
LINK: {link_url}

INSTRUCTIONS:
{framing}
{feature_block}
Respond with JSON: {{"title": "...", "body": "...", "grade": 0.0-1.0, "reason": "..."}} for reddit, or {{"draft": "...", "grade": 0.0-1.0, "reason": "..."}} for other platforms.
"""


def _not_drafted(reason: str) -> dict:
    """The shape of a draft result when no draft was made."""
    return {"comment": "", "draft": "", "grade": 0.0, "rationale": reason, "reason": reason}


def draft_outreach_text(
    platform: str,
    context: dict,
    tone: Optional[str] = None,
    mode: str = "comment",
    feature_hint: Optional[str] = None,
    llm: Optional[Any] = None,
    campaign: str = "v253",
    include_link: bool = False,
    link_url: Optional[str] = None,
    requested_feature: Optional[str] = None,
) -> dict:
    """Unified entry point for all outreach LLM calls.

    The system message is always the mode's framing plus the pitch sheet
    (PITCH.md, else the profile). Returns parsed JSON dict with keys:
    - comment/draft: the text
    - grade: 0.0-1.0
    - rationale/reason: explanation

    A comment or share with no pitch sheet at all (no profile, no PITCH.md)
    is not drafted: the result carries draft="" and the reason. A share, or
    a comment asked to carry a link, with no link to give does the same.

    Args:
        platform: "reddit", "discord", "facebook", "youtube", etc.
        context: dict with keys depending on mode:
            - comment mode: {"url", "title", "body", "thread_context"}
            - share mode: {"target", "link_url"}
            - post mode: {"community", "infographic": {"headline", "lines"}}
        tone: optional tone preset from TONE_GUIDES
        mode: "comment", "share", "reply", or "post" (the account's own post
            with an infographic attached)
        feature_hint: accepted from callers that label a thread with a
            feature key; it is not put in the prompt — the drafter chooses
            its angle from the pitch sheet.
        requested_feature: comment and share modes. A feature a person
            asked the draft to lead with (a profile feature key or their own
            words). The prompt asks the drafter to lead with it when it fits
            and to grade below 0.3, saying why, when it does not. None leaves
            the angle to the pitch sheet.
        llm: optional LLM callable (for testing)
        campaign: UTM campaign tag (default "v253")
        include_link: comment-mode only. When True the user prompt asks the
            persona to include ``link_url`` (default: the profile's repo on
            YouTube, its site otherwise) where it fits naturally.
        link_url: CTA URL when include_link is True.
    """
    if llm is None:
        llm = _ollama_json_chat

    if mode in ("share", "comment", "post") and not _pitch_sheet():
        return _not_drafted(profile.no_product_reason())

    if mode == "post":
        # A title carries no link; elsewhere the profile's link closes the post.
        cta = "" if platform == "reddit" else (
            (link_url or context.get("link_url") or "").strip() or profile.cta_url(platform)
        )
        prompt = _build_post_prompt(
            platform, context.get("community") or "(unspecified)", context.get("infographic") or {}, cta,
        )
        result, draft_text = _draft_in_voice(
            llm, _compose_post_system(platform), prompt, ("draft", "title", "body"), platform,
        )
        draft_text = (draft_text or "").strip()
        if platform == "reddit":
            draft_text = " ".join(draft_text.split())
        elif draft_text and cta and not _draft_already_has_cta(draft_text, cta):
            draft_text = f"{draft_text} {cta}"
        draft_text = apply_utm_tags(draft_text, platform=platform, campaign=campaign)
        reason = result.get("reason", "") or result.get("rationale", "")
        return {
            "comment": draft_text,
            "draft": draft_text,
            "grade": float(result.get("grade", 0.0) or 0.0),
            "rationale": reason,
            "reason": reason,
        }

    if mode == "share":
        target = context.get("target", "(unspecified)")
        share_link = (context.get("link_url") or "").strip() or profile.site_url()
        if not share_link:
            return _not_drafted(profile.no_link_reason())
        prompt = _build_share_prompt(platform, target, share_link, requested_feature)
        # Use the share-specific system block — the comment-focused one
        # (_compose_outward_facing_system) tells the model to skip when
        # there's "no thread to add value to," which makes it refuse
        # legitimate share tasks.
        result = llm(_compose_share_system(), prompt)

        if platform == "reddit":
            title, body = _unpack_reddit_share(result)
            draft_text = json.dumps({
                "title": title,
                "body": body,
                "link_url": share_link,
            })
        else:
            draft_text = result.get("draft", "") or result.get("body", "")
    elif mode == "reply":
        # Reply-to-own-video path. Different system block, no feature-blurb,
        # no UTM tagging — see REPLY_TO_OWN_VIDEO_SYSTEM_BLOCK rationale.
        prompt = _build_reply_prompt(
            parent_text=context.get("parent_text", ""),
            incoming_text=context.get("incoming_text", ""),
            incoming_author=context.get("incoming_author", ""),
            video_title=context.get("video_title", ""),
            tone=tone,
        )
        result, draft_text = _draft_in_voice(
            llm, _compose_reply_system(platform), prompt, ("draft", "reply"), platform,
        )
        # Replies skip the UTM-tag pass entirely — we're not handing out
        # links here. Return early so the apply_utm_tags call below is
        # bypassed (it would no-op on text with no profile links anyway,
        # but skipping is cleaner and avoids future surprises if someone
        # adds a link auto-detector to apply_utm_tags).
        return {
            "comment": draft_text,
            "draft": draft_text,
            "grade": float(result.get("grade", 0.0) or 0.0),
            "rationale": result.get("reason", "") or result.get("rationale", ""),
            "reason": result.get("reason", "") or result.get("rationale", ""),
        }
    else:
        thread_context = context.get("thread_context", "")
        if not thread_context:
            title = context.get("title", "")
            body = context.get("body", "")
            thread_context = f"{title}\n\n{body}"

        cta = (link_url or context.get("link_url") or "").strip() or profile.cta_url(platform)
        if include_link and not cta:
            return _not_drafted(profile.no_link_reason())

        prompt = _build_user_prompt(
            platform=platform,
            thread_context=thread_context,
            target_url=context.get("url"),
            tone=tone,
            include_link=include_link,
            link_url=cta,
            requested_feature=requested_feature,
        )
        result, draft_text = _draft_in_voice(
            llm, _compose_outward_facing_system(platform), prompt, ("draft", "comment"), platform,
        )

        # When the caller asked for a link but the model omitted it, append
        # the URL on a new line. The model's grade still reflects its honest
        # read of how the comment-without-link feels; the caller opted in to
        # this hardening, so we honor the contract. Skip when the draft is
        # empty (model declined entirely — appending a bare URL would just
        # be a link drop, which is exactly what we don't want).
        if include_link and draft_text.strip() and not _draft_already_has_cta(draft_text, cta):
            draft_text = draft_text.rstrip() + f"\n\n{cta}"

    # Apply UTM tags to any link into the profile's own hosts
    draft_text = apply_utm_tags(draft_text, platform=platform, campaign=campaign)

    return {
        "comment": draft_text,
        "draft": draft_text,
        "grade": float(result.get("grade", 0.0) or 0.0),
        "rationale": result.get("reason", "") or result.get("rationale", ""),
        "reason": result.get("reason", "") or result.get("rationale", ""),
    }


def revise_draft(
    action: str, source: Optional[dict], current_text: str, note: str, platform: Optional[str] = None,
) -> dict:
    """Rewrite an existing draft the way its approver asked ("shorter",
    "less salesy"). The system message is the one the draft was written
    under, so the rewrite keeps the same rules. Returns {"draft", "reason"};
    draft is "" when the model gave nothing usable."""
    reply = (action or "").lower() == "reply"
    system = _compose_reply_system(platform) if reply else _compose_outward_facing_system(platform)
    source = source or {}
    answered = "\n".join(
        part for part in (
            f"TITLE: {source['title']}" if source.get("title") else "",
            source.get("text") or "",
        ) if part
    ) or "(not recorded)"
    user = (
        f"WHAT THE DRAFT ANSWERS:\n{answered}\n\n"
        f"CURRENT DRAFT:\n{current_text}\n\n"
        f"THE APPROVER ASKED FOR THIS CHANGE: {note}\n\n"
        "Rewrite the draft with that change and nothing else changed in what it claims. "
        'Respond with JSON: {"draft": "...", "reason": "what you changed"}'
    )
    result = _ollama_json_chat(system, user)
    return {"draft": str(result.get("draft") or "").strip(), "reason": str(result.get("reason") or "")}


def apply_utm_tags(
    text: str, *, platform: str, campaign: str, hosts: Optional[Iterable[str]] = None,
) -> str:
    """Inject UTM params on any URL in text whose host is one of ``hosts``
    (default: the profile's site host) or a subdomain of one.

    Third-party links are left alone, as is everything when the profile has
    no site. Existing query params are preserved.
    """
    own_hosts = tuple(hosts) if hosts is not None else tuple(profile.link_hosts())
    if not own_hosts or not text:
        return text

    def _tag(match):
        url = match.group(0)
        parsed = urlparse(url)
        if not host_matches(parsed.hostname, *own_hosts):
            return url
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params.setdefault("utm_source", platform)
        params.setdefault("utm_medium", "outreach")
        params.setdefault("utm_campaign", campaign)
        return urlunparse(parsed._replace(query=urlencode(params)))

    return re.sub(r"https?://[^\s\)\]>'\"]+", _tag, text)
