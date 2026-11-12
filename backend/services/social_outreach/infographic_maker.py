"""New infographics for outreach, written from the pitch sheet.

The drafter writes a headline and a few short labels, and for each one quotes
the sentence of the pitch sheet that makes it true. A line whose sentence is
not in the sheet is dropped before anything is drawn. The picture is rendered
by the Infographic Generator and its text is read back. The result is not a
keeper: the person looks at it and marks it, like one they made themselves.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger(__name__)

ASPECT = "9:16"
STYLE = "editorial"
MAX_LABELS = 3
# Short lines are what the image models spell reliably.
MAX_HEADLINE_WORDS = 6
MAX_LABEL_WORDS = 5
# A quoted sentence shorter than this could match the sheet by accident.
MIN_BACKING_CHARS = 25
RENDER_TIMEOUT_S = 900

_SYSTEM = """You write the text for one infographic poster about a product, for the person who makes it.
Every line on the poster must be true by the PITCH SHEET below, and for every line you quote the
sentence of the sheet that makes it true, copied exactly. A line you cannot back with a sentence
from the sheet is left out. Do not use numbers, names or claims that are not in the sheet.

--- PITCH SHEET ---
{pitch}
--- END OF PITCH SHEET ---

Return ONLY JSON in this shape:
{{"headline": {{"text": "...", "backing": "..."}},
  "labels": [{{"text": "...", "backing": "..."}}],
  "scene": "..."}}

headline: at most {headline_words} words, plain and specific to the topic.
labels: two or three, each at most {label_words} words, each a different point.
backing: the sentence from the pitch sheet, copied exactly.
scene: one sentence describing the artwork behind the text. No real people, no logos, no words in the artwork."""


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _backed(backing: str, sheet: str) -> bool:
    quote = _fold(backing).strip(" .\"'")
    return len(quote) >= MIN_BACKING_CHARS and quote in sheet


def _line(entry: Any, max_words: int) -> tuple[str, str]:
    if not isinstance(entry, dict):
        return "", ""
    text = " ".join(str(entry.get("text") or "").split()).strip(" \"")
    if not text or len(text.split()) > max_words:
        return "", ""
    return text, str(entry.get("backing") or "")


def write_spec(topic: str, llm: Optional[Callable[[str, str], dict]] = None) -> dict:
    """The text of an infographic about ``topic``, each line with the pitch
    sheet sentence behind it.

    Returns ``{"ok": True, "title", "callouts", "footer", "scene", "backing":
    {line: sentence}, "dropped": [line, ...]}``, or ``{"ok": False, "reason"}``
    when there is no pitch sheet, or no headline and two labels survive.
    """
    from backend.services.social_outreach import persona, profile

    pitch = persona._pitch_sheet()
    if not pitch:
        return {"ok": False, "reason": profile.no_product_reason()}
    if llm is None:
        llm = persona._ollama_json_chat
    system = _SYSTEM.format(pitch=pitch, headline_words=MAX_HEADLINE_WORDS, label_words=MAX_LABEL_WORDS)
    result = llm(system, f"TOPIC: {topic.strip() or 'what it is and who it is for'}") or {}

    sheet = _fold(pitch)
    backing: dict[str, str] = {}
    dropped: list[str] = []

    def keep(entry: Any, max_words: int) -> str:
        text, quote = _line(entry, max_words)
        if text and _backed(quote, sheet):
            backing[text] = " ".join(quote.split())
            return text
        raw = " ".join(str((entry or {}).get("text") or "").split()) if isinstance(entry, dict) else ""
        if raw:
            dropped.append(raw)
        return ""

    title = keep(result.get("headline"), MAX_HEADLINE_WORDS)
    labels = [t for t in (keep(e, MAX_LABEL_WORDS) for e in (result.get("labels") or [])[:MAX_LABELS + 2]) if t]
    labels = list(dict.fromkeys(labels))[:MAX_LABELS]
    if not title or len(labels) < 2:
        return {
            "ok": False, "dropped": dropped,
            "reason": "The drafter could not back a headline and two labels with the pitch sheet.",
        }
    site = (profile.site_url() or "").split("://")[-1].strip("/")
    return {
        "ok": True,
        "title": title,
        "callouts": labels,
        "footer": site,
        "scene": " ".join(str(result.get("scene") or "").split())
        or "an abstract dark background with soft glowing lines",
        "backing": backing,
        "dropped": dropped,
    }


def make(topic: str, llm: Optional[Callable[[str, str], dict]] = None) -> dict:
    """Write, render and read back one infographic about ``topic``.

    Returns the report a run shows: ``kind`` "made_infographic", and on success
    ``document_id``, the lines with their backing, and the read-back.
    """
    from backend.services.social_outreach import infographics
    from backend.services.social_outreach.reddit_outreach import backend_headers, backend_url

    report: dict[str, Any] = {"kind": "made_infographic", "topic": topic, "made": 0, "reason": None}
    spec = write_spec(topic, llm)
    report["dropped"] = spec.get("dropped") or []
    if not spec.get("ok"):
        report["reason"] = "no_backed_text"
        report["detail"] = spec.get("reason")
        return report
    report.update(title=spec["title"], callouts=spec["callouts"], footer=spec["footer"], backing=spec["backing"])

    try:
        response = requests.post(
            f"{backend_url()}/infographic/generate",
            json={
                "title": spec["title"], "callouts": spec["callouts"], "footer": spec["footer"],
                "scene": spec["scene"], "style": STYLE, "aspect": ASPECT,
            },
            headers=backend_headers(),
            timeout=RENDER_TIMEOUT_S,
        )
        rendered = response.json()
    except Exception as e:
        report["reason"] = "render_failed"
        report["detail"] = str(e)[:300]
        return report
    if not rendered.get("success") or not rendered.get("document_id"):
        report["reason"] = "render_failed"
        report["detail"] = str(rendered.get("error") or "the generator returned no image")[:300]
        return report

    document_id = int(rendered["document_id"])
    report.update(document_id=document_id, model=rendered.get("model_name") or rendered.get("model"), made=1)
    try:
        report["readback"] = infographics.readback(document_id).get("readback") or {}
    except Exception as e:
        logger.warning("infographic %s: read-back failed: %s", document_id, e)
        report["readback"] = {"status": "unread", "error": str(e)[:200]}
    return report
