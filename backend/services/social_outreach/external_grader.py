"""
Second-opinion grader for outreach drafts.

Why: Gemma4 self-grades the drafts it just wrote. That score is a measure of
its own confidence, not objective quality — it's biased toward what it
believes is good. An independent grader, run on a different model family
with a fixed rubric, catches drafts that the writer overrated. When the
grader resolves to the drafter's own build (the same Ollama manifest digest,
whatever the two names) or to another build of the drafter's family, its
answer is still recorded but marked not independent
(decision_data.INDEPENDENCE, gates.independent_check_label).

This is intentionally NOT a generation model — it's a binary fitness check.
A comment on a thread is asked three yes/no questions (engages, on_topic,
appropriate_tone); a reply on the account's own video is asked two
(on_topic, appropriate_tone; RUBRICS). Concise is counted in code. A draft
passes only when every question asked is answered yes. Grade is those items
plus concise over their count, for display. No answer here posts a draft:
every draft waits for a person, who sees what the check concluded (gates.py).
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Optional

import requests

from backend.services.decision_evidence import check_counts_as_independent
from backend.utils.ollama_resource_manager import think_payload

logger = logging.getLogger(__name__)


# The grader answers three yes/no questions, not writing anything, so a
# smaller variant is plenty.
DEFAULT_GRADER_MODEL = "gemma4:e2b"
"""Override via env GUAARDVARK_OUTREACH_GRADER_MODEL if you want a different one.
Falls back to gemma4:e2b if the configured model isn't installed, then to the
chat model, which writes the drafts on a stock install (gemma4:e2b is no
longer pulled by default). A grade from the drafter's own build or family is
marked not independent, so set the override to a model of another family for
a check that counts."""

FALLBACK_GRADER_MODEL = "gemma4:e2b"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

GRADER_SYSTEM = """You are a strict outreach-comment grader. You did NOT write this draft. Answer each question below about it with 1 (yes) or 0 (no) and return only JSON.

Questions:
  engages: Does the comment substantively engage with the specific thread, not generic boilerplate?
  on_topic: Is the comment relevant to the thread's actual subject?
  appropriate_tone: Does the tone fit a casual Reddit thread (not corporate, not sycophantic, not spammy)?

Return ONLY this JSON shape:
{"engages": 1, "on_topic": 1, "appropriate_tone": 0, "reason": "Engages with the question and stays on topic, but reads like an advert."}"""


RUBRIC_QUESTIONS = ("engages", "on_topic", "appropriate_tone")
"""The items the grader answers about a comment; it passes only when all are yes."""

REPLY_GRADER_SYSTEM = """You are a strict grader of a reply that a video's creator wants to post under their own video, answering a viewer. You did NOT write this reply. Answer each question below about it with 1 (yes) or 0 (no) and return only JSON.

Questions:
  on_topic: Does the reply respond to what the viewer actually said?
  appropriate_tone: Is the tone right for a creator answering a viewer (friendly and genuine, not corporate, not sycophantic, not a sales pitch)?

Return ONLY this JSON shape:
{"on_topic": 1, "appropriate_tone": 0, "reason": "Answers the question but turns into a sales pitch."}"""

REPLY_RUBRIC_QUESTIONS = ("on_topic", "appropriate_tone")
"""The items asked about a reply on the account's own video. Not engages: the
reply answers one viewer and is not trying to add to someone else's thread."""

RUBRICS = {
    "comment": (GRADER_SYSTEM, RUBRIC_QUESTIONS),
    "reply": (REPLY_GRADER_SYSTEM, REPLY_RUBRIC_QUESTIONS),
}
"""kind -> (system prompt, questions) for grade_draft_externally."""

CONCISE_MAX_WORDS = 120
"""A draft of this many words or fewer is concise (Reddit favours short
comments). Counted in code; the grader is not asked."""

_YES = frozenset({"1", "true", "yes"})
_NO = frozenset({"0", "false", "no"})


def _rubric_bit(value) -> Optional[int]:
    """1 for true/yes/1, 0 for false/no/0, None for any other answer."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and value in (0, 1):
        return int(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _YES:
            return 1
        if word in _NO:
            return 0
    return None


def _is_concise(draft_text: str) -> int:
    return int(len((draft_text or "").split()) <= CONCISE_MAX_WORDS)


def _relevance_grade(value) -> Optional[float]:
    """The thread-fit grade as a number in [0, 1], or None when it is not a
    number. A missing grade is 0.0 (do not comment). True/False, NaN and
    infinity are not grades (NaN would come out of the clamp as 1.0)."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return None
    try:
        grade = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(grade):
        return None
    return max(0.0, min(1.0, grade))


def _list_loaded_models() -> list[str]:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


def _drafter_model() -> Optional[str]:
    """The model the outreach drafts are written with (persona.drafter_model)."""
    try:
        from backend.services.social_outreach import persona
        return persona.drafter_model() or None
    except Exception as e:  # noqa: BLE001 - an unknown drafter is reported, not raised
        logger.debug("external grader: drafter model unknown: %s", e)
        return None


def _independence(grader_model: str) -> dict:
    """Whether ``grader_model``'s grade counts as independent of the drafter.

    ``independent`` follows decision_data.INDEPENDENCE: False when the two
    names resolve to the same Ollama build or to builds of one model family,
    True when they differ in both, None when that cannot be told. The same
    build grading its own draft is not an independent check, with or without
    a fresh context.
    """
    drafter = _drafter_model()
    independent = (
        check_counts_as_independent("model", judge=grader_model, authors=[drafter]) if drafter else None
    )
    if independent is False:
        logger.info(
            "external grader %s is the drafter's own model or family (%s): its grade is not independent",
            grader_model, drafter,
        )
    return {"drafter_model": drafter, "independent": independent}


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
    chat = _chat_model()
    if chat and chat in loaded:
        logger.warning(
            "external grader: neither %s nor %s installed, grading with the chat model %s",
            configured, FALLBACK_GRADER_MODEL, chat,
        )
        return chat
    logger.warning(
        "external grader: neither %s nor %s loaded; will skip the second-opinion gate",
        configured, FALLBACK_GRADER_MODEL,
    )
    return None


def _chat_model() -> Optional[str]:
    try:
        from backend.config import get_default_llm
        return get_default_llm()
    except Exception:
        return None


def grade_draft_externally(draft_text: str, thread_context: str, *, kind: str = "comment") -> dict:
    """Score a draft against the rubric for ``kind`` using a second LLM.

    ``kind`` is "comment" (a reply to someone else's thread) or "reply" (a
    reply on the account's own video; ``thread_context`` is then the video
    title, our earlier comment and the viewer's reply). A self-share has no
    thread to engage with, so callers do not grade it.

    Returns:
        {
            "grade": float in [0, 1],   # the asked items plus concise over their count, for display
            "passed": bool,             # every asked question answered yes
            "engages": int 0/1,         # comment only
            "on_topic": int 0/1,
            "appropriate_tone": int 0/1,
            "concise": int 0/1,         # CONCISE_MAX_WORDS or fewer, counted here
            "questions": [str],         # the questions asked (RUBRICS[kind])
            "reason": str,
            "checked": bool,    # true only when the grader's answers came back
            "skipped": bool,    # not checked (model unavailable, call or parse error)
            "model": str | None,
            "drafter_model": str | None,   # checked results: the model that wrote drafts
            "independent": bool | None,    # checked results: False when model is the drafter's
                                           # own build or family, None when that could not be told
        }

    Only the asked answers are read; a "grade" or "concise" in the reply is
    ignored. A question answered with anything but true/yes/1 or false/no/0,
    or not answered, leaves the draft unchecked, reason "grader_reply_unparsed".

    An unchecked result is not a rejection, but it is no independent signal
    either; the person reviewing the draft is told it went unchecked.
    """
    if kind not in RUBRICS:
        raise ValueError(f"unknown rubric kind: {kind!r}")
    system, questions = RUBRICS[kind]
    model = _resolve_grader_model()
    if model is None:
        return {"grade": 0.0, "checked": False, "skipped": True, "model": None,
                "reason": "no_grader_model_loaded"}

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
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "options": {"temperature": 0.1, "num_ctx": 4096},
                "format": "json",
                **think_payload(model),
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
        return {"grade": 0.0, "checked": False, "skipped": True, "model": model,
                "reason": f"grader_call_failed: {e}"}

    answers = {name: _rubric_bit(data.get(name)) for name in questions}
    if None in answers.values():
        logger.warning("external grader reply unparsed: %.300r", data)
        return {"grade": 0.0, "checked": False, "skipped": True, "model": model,
                "reason": "grader_reply_unparsed"}

    concise = _is_concise(draft_text)
    return {
        "grade": (sum(answers.values()) + concise) / (len(questions) + 1),
        "passed": all(answers.values()),
        **answers,
        "concise": concise,
        "questions": list(questions),
        "reason": str(data.get("reason") or "")[:300],
        "checked": True,
        "skipped": False,
        "model": model,
        **_independence(model),
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
    model isn't loaded, the call fails or the reply's grade is not a number
    (reason "relevance_reply_unparsed"), we return skipped=True. That is
    neither a reject nor a pass: recon queues the candidate on its keyword
    match and the scheduled Reddit loop drafts it, both marked unchecked for
    the person who reviews the draft.
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
                **think_payload(model),
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

    grade = _relevance_grade(data.get("grade"))
    if grade is None:
        logger.warning("relevance scorer reply unparsed: %.300r", data)
        return {"grade": 0.0, "skipped": True, "model": model, "reason": "relevance_reply_unparsed"}
    return {
        "grade": grade,
        "verdict": str(data.get("verdict") or "")[:40],
        "reason": str(data.get("reason") or "")[:300],
        "skipped": False,
        "model": model,
    }
