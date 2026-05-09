"""
Outward-facing persona block + canonical Guaardvark copy.

Lives here so every social-outreach surface (Discord cog, Reddit loop, self-share)
pulls from the same source of truth. Don't fork these strings.
"""

import json
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import ollama

logger = logging.getLogger(__name__)

SITE_URL = "https://guaardvark.com"
GITHUB_URL = "https://github.com/guaardvark/guaardvark"
GOTHAM_RISING_URL = "https://www.youtube.com/watch?v=8MdtM3HurJo"

# Paraphrased from README.md. If README changes meaningfully, refresh this.
GUAARDVARK_PITCH = (
    "Guaardvark is a self-hosted AI workstation. Autonomous agents see your screen "
    "and drive your apps. Three-tier neural routing picks the fastest path. Parallel "
    "agent swarms run across isolated git worktrees. Video gen, 4K/8K upscaling, RAG "
    "over your docs, voice — all local. Your machine, your data, your rules."
)

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

# Voice rules — distilled from CLAUDE.md "Code Style" + feedback_mimic_user_style memory.
# This is the system block prepended when generating outward-facing copy.
OUTWARD_FACING_SYSTEM_BLOCK = """\
You are writing a comment or post under Guaardvark Dev's account. Match his voice exactly.

Voice rules:
- Direct, competent, occasionally funny. No corporate hedging. No exclamation marks.
- Never lead with the link. Add value first, then mention the relevant Guaardvark piece naturally if it fits.
- Don't write like a marketer. Don't say "check it out" or "you should try". Don't pitch.
- Skip emojis unless the surrounding thread uses them.
- Keep it short. 1–4 sentences for comments. A comment that reads like an actual person sharing a tip is the goal.
- If the thread is asking a question, answer the question first. The Guaardvark mention is a footnote, not the headline.
- Never claim Guaardvark does something it doesn't. Stick to the feature blurb you were given.
- If you can't add real value to the thread, return an empty draft and a low grade.

Forbidden phrases: "game changer", "revolutionary", "next level", "100%", "this is the way", "I built", "I made", "shameless plug", "DM me".

After drafting, grade your own comment 0.0-1.0 on the question: "would a typical reader of this thread upvote this, or would they smell promotion?" Be honest. 0.7+ means post; below means hold.

Return JSON: {"draft": "<comment text>", "grade": 0.0-1.0, "reason": "<one line on why this grade>"}.
"""

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


def _build_user_prompt(
    platform: str,
    thread_context: str,
    target_url: Optional[str],
    feature_hint: Optional[str],
    tone: Optional[str] = None,
) -> str:
    """Compose the user-side prompt for the LLM. Keeps the system block clean."""
    feature = feature_hint or find_relevant_feature(thread_context) or "local_ai"
    feature_blurb = FEATURE_BLURBS.get(feature, FEATURE_BLURBS["local_ai"])
    tone_guide = TONE_GUIDES.get((tone or "default").lower(), "").strip()
    tone_block = f"\nTONE OVERRIDE: {tone_guide}\n" if tone_guide else ""

    return f"""\
PLATFORM: {platform}
TARGET URL: {target_url or "(unknown)"}

THREAD CONTEXT:
\"\"\"
{thread_context.strip()[:4000]}
\"\"\"

GUAARDVARK PITCH (one line, only paraphrase if needed):
{GUAARDVARK_PITCH}

RELEVANT FEATURE TO MENTION (only if it actually fits):
{feature}: {feature_blurb}

LINK TO INCLUDE (only if natural — don't force it):
{SITE_URL}
{tone_block}
Write a comment that adds value to this thread first. If a Guaardvark mention fits, drop it as a casual reference, not a pitch. If nothing fits, return draft="" and grade < 0.3.

Respond with JSON: {{"draft": "...", "grade": 0.0-1.0, "reason": "..."}}.
"""


def _build_share_prompt(platform: str, target: str, link_url: str) -> str:
    framing = SHARE_FRAMING.get(platform, SHARE_FRAMING["reddit"])
    return f"""\
PLATFORM: {platform}
TARGET COMMUNITY: {target}
LINK: {link_url}

GUAARDVARK PITCH:
{GUAARDVARK_PITCH}

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
        result = llm(OUTWARD_FACING_SYSTEM_BLOCK, prompt)
        
        if platform == "reddit":
            draft_text = json.dumps({
                "title": result.get("title", ""),
                "body": result.get("body", ""),
                "link_url": link_url,
            })
        else:
            draft_text = result.get("draft", "")
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
        result = llm(OUTWARD_FACING_SYSTEM_BLOCK, prompt)
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
