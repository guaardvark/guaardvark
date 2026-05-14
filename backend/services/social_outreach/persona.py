"""
Outward-facing persona block + canonical Guaardvark copy.

Lives here so every social-outreach surface (Discord cog, Reddit loop, self-share)
pulls from the same source of truth. Don't fork these strings.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import ollama

logger = logging.getLogger(__name__)

SITE_URL = "https://guaardvark.com"
GITHUB_URL = "https://github.com/guaardvark/guaardvark"
GOTHAM_RISING_URL = "https://www.youtube.com/watch?v=8MdtM3HurJo"

# One-line pitch — kept for the /social-outreach/persona API endpoint and
# any legacy import that still expects a string here. The CANONICAL pitch
# (and the thing the model actually reads at draft time) lives in
# data/agent/PITCH.md and is loaded via _load_pitch_md() below. Edits to
# PITCH.md go live on the next draft call without a restart.
GUAARDVARK_PITCH = (
    "Guaardvark is a self-hosted AI workstation. Local-first. Your machine, "
    "your data, your rules."
)

# Path to the human-editable pitch sheet. The recon agent's regex feature
# matcher (RELEVANCE_KEYWORDS) is structural and stays in code; this file
# is for the LLM-facing facts the model reads every draft.
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
        logger.warning("PITCH.md not found at %s", _PITCH_MD_PATH)
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

# One-line hooks for each feature. Pick whichever maps to the thread context.
FEATURE_BLURBS = {
    "local_ai": "everything runs locally on your hardware — no cloud, no API keys",
    "screen_control": "the agent sees your screen and drives apps via vision + servo, not just chat",
    "video_gen": "video generation pipeline runs on a single desktop GPU (the Gotham Rising short was made entirely with it)",
    "upscaling": "image and video upscaling to 4K/8K locally",
    "rag": "RAG over your own documents, indexed locally with LlamaIndex + Postgres",
    "three_tier_brain": "three-tier neural routing — reflexes fire under 100ms, instinct in one LLM call, deliberation only when the problem actually needs it",
    "swarms": "parallel Claude/Ollama agents in isolated git worktrees with a real dependency DAG",
    "voice": "voice interface so you can talk to it",
    "ollama_native": "uses Ollama as the default LLM backend, with a pluggable abstraction so you're not locked in",
    "open_source": "MIT-licensed, self-hostable, no telemetry",
}

# Words/phrases in a thread that suggest a feature is relevant. Order matters
# (first match wins).
RELEVANCE_KEYWORDS = [
    (r"\b(ollama|local\s*llm|llama\.cpp|self\s*host(ed)?|local\s*ai)\b", "local_ai"),
    (r"\b(screen\s*control|computer\s*use|browser\s*agent|automate\s*click|gui\s*agent)\b", "screen_control"),
    (r"\b(comfy\s*ui|comfyui|stable\s*diffusion|video\s*gen|text2video|sora|runway)\b", "video_gen"),
    (r"\b(upscal(e|ing)|esrgan|4k|8k)\b", "upscaling"),
    (r"\b(rag|retrieval|llamaindex|vector\s*db|chat\s*with\s*docs)\b", "rag"),
    (r"\b(swarm|multi[\s-]?agent|parallel\s*agent|crewai|autogen)\b", "swarms"),
    (r"\b(voice|whisper|tts|speech)\b", "voice"),
    (r"\b(react\s*loop|tool\s*use|agent\s*router|routing\s*engine)\b", "three_tier_brain"),
]

# Framing prepended to PITCH.md when generating outward-facing copy. The
# old version of this block carried voice rules + a forbidden-phrases
# blocklist + an opinionated grading rubric — all of which kneecapped
# friendlier model families and forced every output through Gemma4's
# guarded register. Now we trust the model's natural voice and let
# PITCH.md carry the facts. If a model's natural voice drifts off-tone,
# nudge the file, not the code.
_OUTWARD_FACING_FRAMING = """\
You are writing a comment or post under Dean Albenze's Guaardvark account.
The pitch sheet below is the source of truth — don't invent features
that aren't in it, don't fabricate URLs, don't claim more than what's
written. Within those guardrails, write in your own voice.

Self-grade the draft 0.0-1.0 on "does this add real value to the thread
without reading as promotion?". 0.7+ is post-worthy; below that, hold.
If nothing in the pitch sheet credibly fits the thread, return draft=""
and grade<0.3 — better to skip than to force a mention.

Return JSON: {"draft": "<comment text>", "grade": 0.0-1.0, "reason": "<one line>"}.
"""

# Framing for REPLIES on Guaardvark's OWN YouTube videos. Different audience
# (already engaged, watched the video) so different posture (no pitch,
# no link, no marketing gate). Same factual ground from PITCH.md.
_REPLY_FRAMING = """\
You are writing a REPLY to a comment left on one of Dean Albenze's own
Guaardvark YouTube videos. The viewer already watched and engaged — you
are NOT pitching to a stranger. Be human about it. Don't paste the link,
don't recap the video, don't sell. Answer questions if asked, take critique
on the merits, acknowledge kindness briefly without overselling gratitude.

The pitch sheet below is for factual reference only — use specifics from it
if the viewer asked about a feature, otherwise it stays out of the reply.

Self-grade 0.0-1.0 on "does this read like a real creator actually engaging
with their viewer, or like a templated auto-response?". 0.6+ means post.
If the incoming comment doesn't merit a substantive reply (spam, hostile,
off-topic, wholly generic praise), return draft="" and grade=0.0.

Return JSON: {"draft": "<reply text>", "grade": 0.0-1.0, "reason": "<one line>"}.
"""


def _compose_outward_facing_system() -> str:
    """Build the system message: framing + PITCH.md. Re-read on every call
    via the mtime-cached loader, so edits to PITCH.md propagate without
    restarting the worker."""
    pitch = _load_pitch_md().strip()
    if not pitch:
        return _OUTWARD_FACING_FRAMING
    return f"{_OUTWARD_FACING_FRAMING}\n\n--- PITCH SHEET ---\n{pitch}\n"


def _compose_reply_system() -> str:
    """Same idea as _compose_outward_facing_system but for replies-on-own-video."""
    pitch = _load_pitch_md().strip()
    if not pitch:
        return _REPLY_FRAMING
    return f"{_REPLY_FRAMING}\n\n--- PITCH SHEET (factual reference only) ---\n{pitch}\n"


# Back-compat: anywhere in the codebase that imported these constants
# directly still works. The value is now framing-only (no facts) — callers
# that want the full pitch should use draft_outreach_text, which always
# composes facts in via PITCH.md.
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


# Per-platform framing for self-share posts (link to guaardvark.com or Gotham Rising).
SHARE_FRAMING = {
    "reddit": (
        "Write a Reddit post title (under 100 chars) and body (under 400 chars) "
        "introducing Guaardvark to a relevant subreddit. The title must be a real "
        "hook, not a pitch. The body explains what it is in plain language and what "
        "specifically might interest THIS subreddit's audience. End with the link."
    ),
    "discord": (
        "Write a 2–4 sentence Discord message introducing Guaardvark to a topical "
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
    """Return the first feature key whose keyword matches the text, or None."""
    if not text:
        return None
    lowered = text.lower()
    for pattern, feature in RELEVANCE_KEYWORDS:
        if re.search(pattern, lowered, re.IGNORECASE):
            return feature
    return None


def _ollama_json_chat(system: str, user: str, model: Optional[str] = None) -> Dict[str, Any]:
    """One-shot Ollama call that returns parsed JSON. Best-effort.

    Forces format=json so the model emits valid JSON; we still try/except
    around the parse because models occasionally truncate or wrap.
    """
    if model is None:
        from backend.config import get_default_llm
        model = get_default_llm()

    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format="json",
            options={"temperature": 0.6},
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
    comment on Guaardvark's own video. Used with REPLY_TO_OWN_VIDEO_SYSTEM_BLOCK.

    No feature-blurb / no link / no UTM tags — replies don't pitch.
    """
    tone_guide = TONE_GUIDES.get((tone or "default").lower(), "").strip()
    tone_line = f"\nTONE: {tone_guide}\n" if tone_guide else ""
    author_line = f" by @{incoming_author}" if incoming_author else ""
    video_line = f"VIDEO: {video_title}\n" if video_title else ""
    return f"""\
{video_line}YOUR EARLIER COMMENT (the one they replied to):
{parent_text}

THEIR REPLY{author_line}:
{incoming_text}
{tone_line}
Draft a reply to them. Follow the voice rules from the system message.
Return JSON: {{"draft": "<reply text>", "grade": 0.0-1.0, "reason": "<one line>"}}.
"""


def _build_user_prompt(
    platform: str,
    thread_context: str,
    target_url: Optional[str],
    feature_hint: Optional[str],
    tone: Optional[str] = None,
) -> str:
    """Compose the user-side prompt for the LLM.

    Facts about Guaardvark live in the system message (PITCH.md). This
    side carries thread-specific context + an optional soft hint about
    which talking point the recon agent thought was relevant. The hint
    is annotation, not instruction — the model is free to ignore it if
    a different point fits the thread better.
    """
    # find_relevant_feature returns None on no match; that's now allowed
    # to propagate (instead of defaulting to "local_ai" which would falsely
    # imply local AI is the relevant angle for every off-topic thread).
    feature = feature_hint or find_relevant_feature(thread_context)
    hint_block = ""
    if feature:
        hint_block = (
            f"\nRECON HINT (use only if it actually fits the thread; "
            f"ignore otherwise): {feature}\n"
        )
    tone_guide = TONE_GUIDES.get((tone or "default").lower(), "").strip()
    tone_block = f"\nTONE OVERRIDE: {tone_guide}\n" if tone_guide else ""

    return f"""\
PLATFORM: {platform}
TARGET URL: {target_url or "(unknown)"}

THREAD CONTEXT:
\"\"\"
{thread_context.strip()[:4000]}
\"\"\"
{hint_block}{tone_block}
Draft a comment for this thread. Add real value first; a Guaardvark mention
is optional and only fits sometimes.

Respond with JSON: {{"draft": "...", "grade": 0.0-1.0, "reason": "..."}}.
"""


def _build_share_prompt(platform: str, target: str, link_url: str) -> str:
    """User-side prompt for self-share posts. Facts come from PITCH.md via
    the system message; this side carries the platform/target/link.
    """
    framing = SHARE_FRAMING.get(platform, SHARE_FRAMING["reddit"])
    return f"""\
PLATFORM: {platform}
TARGET COMMUNITY: {target}
LINK: {link_url}

INSTRUCTIONS:
{framing}

Respond with JSON: {{"title": "...", "body": "...", "grade": 0.0-1.0, "reason": "..."}} for reddit, or {{"draft": "...", "grade": 0.0-1.0, "reason": "..."}} for other platforms.
"""


def draft_outreach_text(
    platform: str,
    context: dict,
    tone: Optional[str] = None,
    mode: str = "comment",
    feature_hint: Optional[str] = None,
    llm: Optional[Any] = None,
    campaign: str = "v253",
) -> dict:
    """Unified entry point for all outreach LLM calls.
    
    Guarantees OUTWARD_FACING_SYSTEM_BLOCK + audience-aware FEATURE_BLURBS
    are always injected. Returns parsed JSON dict with keys:
    - comment/draft: the text
    - grade: 0.0-1.0
    - rationale/reason: explanation
    
    Args:
        platform: "reddit", "discord", "facebook", etc.
        context: dict with keys depending on mode:
            - comment mode: {"url", "title", "body", "thread_context"}
            - share mode: {"target", "link_url"}
        tone: optional tone preset from TONE_GUIDES
        mode: "comment" or "share"
        feature_hint: optional feature key override
        llm: optional LLM callable (for testing)
        campaign: UTM campaign tag (default "v253")
    """
    if llm is None:
        llm = _ollama_json_chat
    
    if mode == "share":
        target = context.get("target", "(unspecified)")
        link_url = context.get("link_url", SITE_URL)
        prompt = _build_share_prompt(platform, target, link_url)
        result = llm(_compose_outward_facing_system(), prompt)

        if platform == "reddit":
            draft_text = json.dumps({
                "title": result.get("title", ""),
                "body": result.get("body", ""),
                "link_url": link_url,
            })
        else:
            draft_text = result.get("draft", "")
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
        result = llm(_compose_reply_system(), prompt)
        draft_text = result.get("draft", "") or result.get("reply", "")
        # Replies skip the UTM-tag pass entirely — we're not handing out
        # links here. Return early so the apply_utm_tags call below is
        # bypassed (it would no-op on text with no guaardvark.com links
        # anyway, but skipping is cleaner and avoids future surprises if
        # someone adds a link auto-detector to apply_utm_tags).
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
        
        prompt = _build_user_prompt(
            platform=platform,
            thread_context=thread_context,
            target_url=context.get("url"),
            feature_hint=feature_hint,
            tone=tone,
        )
        result = llm(_compose_outward_facing_system(), prompt)
        draft_text = result.get("draft", "") or result.get("comment", "")
    
    # Apply UTM tags to any guaardvark.com links
    draft_text = apply_utm_tags(draft_text, platform=platform, campaign=campaign)
    
    return {
        "comment": draft_text,
        "draft": draft_text,
        "grade": float(result.get("grade", 0.0) or 0.0),
        "rationale": result.get("reason", "") or result.get("rationale", ""),
        "reason": result.get("reason", "") or result.get("rationale", ""),
    }


GUAARDVARK_DOMAIN_PATTERN = re.compile(r"^([a-z0-9-]+\.)?guaardvark\.com$", re.IGNORECASE)


def apply_utm_tags(text: str, *, platform: str, campaign: str) -> str:
    """Inject UTM params on any guaardvark.com (or *.guaardvark.com) URL in text.
    
    Skips non-guaardvark domains (we don't tag third-party links).
    Preserves existing query params.
    """
    def _tag(match):
        url = match.group(0)
        parsed = urlparse(url)
        if not GUAARDVARK_DOMAIN_PATTERN.match(parsed.netloc):
            return url
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params.setdefault("utm_source", platform)
        params.setdefault("utm_medium", "outreach")
        params.setdefault("utm_campaign", campaign)
        return urlunparse(parsed._replace(query=urlencode(params)))
    
    return re.sub(r"https?://[^\s\)\]>'\"]+", _tag, text)
