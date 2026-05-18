"""
Second-opinion grader for outreach drafts.

Why: Gemma4 self-grades the drafts it just wrote. That score is a measure of
its own confidence, not objective quality — it's biased toward what it
believes is good. An independent grader, run on a different model family
with a fixed rubric, catches drafts that the writer overrated.

This is intentionally NOT a generation model — it's a binary fitness check.
Grade is 0-1, threshold is at the call site (see content_agent.MIN_EXTERNAL_GRADE).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# The grader follows a 4-item rubric, not writing anything, so a smaller
# variant is plenty.
DEFAULT_GRADER_MODEL = "gemma4:e2b"
"""Override via env GUAARDVARK_OUTREACH_GRADER_MODEL if you want a different one.
Falls back to gemma4:e2b if the configured model isn't loaded — same family but
smaller params, still gives some independence from the main e4b drafter."""

FALLBACK_GRADER_MODEL = "gemma4:e2b"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

GRADER_SYSTEM = """You are a strict outreach-comment grader. You did NOT write this draft. You score it against the rubric below and return only JSON.

Rubric (each 0 or 1):
  engages: Does the comment substantively engage with the specific thread, not generic boilerplate?
  on_topic: Is the comment relevant to the thread's actual subject?
  appropriate_tone: Does the tone fit a casual Reddit thread (not corporate, not sycophantic, not spammy)?
  concise: Is it under ~120 words? Reddit favors short comments.

Final grade = sum / 4 (so 0.0, 0.25, 0.50, 0.75, 1.00).

Return ONLY this JSON shape:
{"grade": 0.75, "engages": 1, "on_topic": 1, "appropriate_tone": 1, "concise": 0, "reason": "Solid engagement and on-topic, but too long for Reddit's casual feel."}"""


def _list_loaded_models() -> list[str]:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


def _resolve_grader_model() -> Optional[str]:
    """Pick the configured grader model if loaded, else the fallback, else None."""
    configured = os.environ.get("GUAARDVARK_OUTREACH_GRADER_MODEL", DEFAULT_GRADER_MODEL)
    loaded = _list_loaded_models()
    if configured in loaded:
        return configured
    if FALLBACK_GRADER_MODEL in loaded and FALLBACK_GRADER_MODEL != configured:
        logger.warning(
            "external grader: %s not loaded, falling back to %s",
            configured, FALLBACK_GRADER_MODEL,
        )
        return FALLBACK_GRADER_MODEL
    logger.warning(
        "external grader: neither %s nor %s loaded; will skip the second-opinion gate",
        configured, FALLBACK_GRADER_MODEL,
    )
    return None


def grade_draft_externally(draft_text: str, thread_context: str) -> dict:
    """Score a draft against the rubric using a different-family LLM.

    Returns:
        {
            "grade": float in [0, 1],
            "engages": int 0/1,
            "on_topic": int 0/1,
            "appropriate_tone": int 0/1,
            "concise": int 0/1,
            "reason": str,
            "skipped": bool,    # true if we couldn't grade (model unavailable, parse error)
            "model": str | None,
        }

    A "skipped" result means the call didn't fail per se — there's just no
    independent signal to gate on. The caller should treat skipped as
    "trust the self-grade", not "reject".
    """
    model = _resolve_grader_model()
    if model is None:
        return {"grade": 0.0, "skipped": True, "model": None, "reason": "no_grader_model_loaded"}

    user_msg = (
        f"DRAFT:\n{draft_text}\n\n"
        f"THREAD CONTEXT:\n{thread_context[:2000]}\n\n"
        "Grade this draft. Return JSON only."
    )
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": GRADER_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                "options": {"temperature": 0.1, "num_ctx": 4096},
                "format": "json",
            },
            timeout=60,
        )
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "").strip()
        # Extract outermost JSON object — same pattern as _parse_decision
        start = content.find("{")
        end = content.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError(f"no JSON in grader output: {content[:200]!r}")
        data = json.loads(content[start:end])
    except Exception as e:
        logger.warning("external grader call failed: %s", e)
        return {"grade": 0.0, "skipped": True, "model": model, "reason": f"grader_call_failed: {e}"}

    grade = float(data.get("grade") or 0.0)
    return {
        "grade": max(0.0, min(1.0, grade)),
        "engages": int(data.get("engages") or 0),
        "on_topic": int(data.get("on_topic") or 0),
        "appropriate_tone": int(data.get("appropriate_tone") or 0),
        "concise": int(data.get("concise") or 0),
        "reason": (data.get("reason") or "")[:300],
        "skipped": False,
        "model": model,
    }


RELEVANCE_SYSTEM = """You judge whether a Reddit thread is a good fit for an outreach comment about a specific feature/product. The keyword filter already matched — your job is to spot threads where the keyword match is misleading: thematic but hostile (rant against the topic), already-answered (OP reports it works fine), unrelated tangent, or a sub that won't tolerate self-promo on this topic.

Return ONLY this JSON shape:
{"grade": 0.8, "verdict": "good_fit", "reason": "OP is asking for setup advice on local-LLM stack, comment can add value"}
or
{"grade": 0.2, "verdict": "skip", "reason": "OP is venting about a different vendor; not a fit for an outreach comment"}

Grade scale: 0.0 (don't comment), 0.5 (could go either way), 1.0 (clearly worth commenting)."""


def score_thread_relevance(
    title: str,
    selftext: str,
    top_comments: list[str],
    feature_hint: str,
    subreddit: str = "",
) -> dict:
    """LLM judge for whether a keyword-matched thread is actually a good
    outreach target. Catches the "keyword present but context hostile" cases
    that pure regex matching can't see.

    Same skip-on-infra-failure semantics as grade_draft_externally — if the
    model isn't loaded or the call fails, we return skipped=True and the
    caller treats that as "trust the keyword match" rather than a hard reject.
    """
    model = _resolve_grader_model()
    if model is None:
        return {"grade": 0.0, "skipped": True, "model": None, "reason": "no_grader_model_loaded"}

    sub_part = f"r/{subreddit}\n" if subreddit else ""
    comments_part = "\n---\n".join(c[:400] for c in top_comments[:3])
    user_msg = (
        f"FEATURE_HINT: {feature_hint}\n"
        f"{sub_part}"
        f"TITLE: {title}\n\n"
        f"OP BODY:\n{selftext[:1200] or '(link-only post)'}\n\n"
        f"TOP COMMENTS:\n{comments_part}\n\n"
        "Should we comment here?"
    )
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": RELEVANCE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                "options": {"temperature": 0.1, "num_ctx": 4096},
                "format": "json",
            },
            timeout=45,
        )
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "").strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError(f"no JSON in relevance output: {content[:200]!r}")
        data = json.loads(content[start:end])
    except Exception as e:
        logger.warning("relevance scorer call failed: %s", e)
        return {"grade": 0.0, "skipped": True, "model": model, "reason": f"relevance_call_failed: {e}"}

    grade = float(data.get("grade") or 0.0)
    return {
        "grade": max(0.0, min(1.0, grade)),
        "verdict": (data.get("verdict") or "")[:40],
        "reason": (data.get("reason") or "")[:300],
        "skipped": False,
        "model": model,
    }
