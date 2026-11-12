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
backing: the sentence from the pitch sheet, copied exactly. A line says what its sentence says, in the
sentence's own words where it can; a sentence about something else does not back it.
scene: one sentence describing the artwork behind the text. No real people, no logos, no words in the artwork."""


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


# A heading like "Never say" opens a list of things that must not be claimed.
_FORBIDDEN_HEADING = re.compile(r"\b(never|do not|don't|not to)\b", re.I)

_FILLER = frozenset(
    "the and for with from your you our its it's that this than then are was will can not but all any "
    "about into over what why how who when where which does have has make made get use using "
    "them they their there here more most just also very own".split()
)


def _quotable(sheet: str) -> str:
    """The pitch sheet without the sections that list what must not be said."""
    kept: list[str] = []
    skipping_below: Optional[int] = None
    for line in sheet.splitlines():
        heading = re.match(r"\s*(#+)\s+(.*)", line)
        if heading:
            depth = len(heading.group(1))
            if skipping_below is not None and depth <= skipping_below:
                skipping_below = None
            if skipping_below is None and _FORBIDDEN_HEADING.search(heading.group(2)):
                skipping_below = depth
        if skipping_below is None:
            kept.append(line)
    return "\n".join(kept)


def _content_words(text: str, ignore: frozenset = frozenset()) -> list[str]:
    words = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return [w for w in words if w not in _FILLER and w not in ignore]


def _found(word: str, among: set[str]) -> bool:
    """``word`` or another form of it (install, installs, installed) is in ``among``."""
    stem = word[:4] if len(word) > 4 else word
    return any(other == word or (len(other) >= 4 and other.startswith(stem)) for other in among)


def _backed(text: str, backing: str, sheet: str, ignore: frozenset = frozenset()) -> bool:
    """The quoted sentence is in the sheet and is about what the line says: at
    least half the line's own words appear in it."""
    quote = _fold(backing).strip(" .\"'")
    if len(quote) < MIN_BACKING_CHARS or quote not in sheet:
        return False
    said = _content_words(text, ignore)
    if not said:
        return False
    quoted = set(re.findall(r"[a-z0-9]{3,}", quote))
    return 2 * sum(_found(word, quoted) for word in said) >= len(said)


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
    when there is no pitch sheet, the sheet has nothing on the topic, or no
    headline and two labels survive.
    """
    from backend.services.social_outreach import persona, profile

    pitch = persona._pitch_sheet()
    if not pitch:
        return {"ok": False, "reason": profile.no_product_reason()}
    sheet = _fold(_quotable(pitch))
    sheet_words = set(re.findall(r"[a-z0-9]{3,}", sheet))
    # The product's own name is on every line and proves nothing about one.
    name = frozenset(_content_words(profile.load().name or ""))
    topic_words = _content_words(topic, name)
    unknown = [word for word in topic_words if not _found(word, sheet_words)]
    if unknown:
        return {
            "ok": False, "dropped": [],
            "reason": "The pitch sheet says nothing about: " + ", ".join(unknown)
            + ". Add the fact to the product profile, then ask again.",
        }
    if llm is None:
        llm = persona._ollama_json_chat
    system = _SYSTEM.format(pitch=pitch, headline_words=MAX_HEADLINE_WORDS, label_words=MAX_LABEL_WORDS)
    ask = f"TOPIC: {topic.strip() or 'what it is and who it is for'}"
    backing: dict[str, str] = {}
    dropped: list[str] = []

    def keep(entry: Any, max_words: int) -> str:
        text, quote = _line(entry, max_words)
        if text and _backed(text, quote, sheet, name):
            backing[text] = " ".join(quote.split())
            return text
        raw = " ".join(str((entry or {}).get("text") or "").split()) if isinstance(entry, dict) else ""
        if raw and raw not in dropped:
            dropped.append(raw)
        return ""

    def on_topic(lines: list[str]) -> bool:
        if not topic_words:
            return True
        covered = set(re.findall(r"[a-z0-9]{3,}", _fold(" ".join(lines + [backing[l] for l in lines]))))
        return any(_found(word, covered) for word in topic_words)

    title, labels, scene = "", [], ""
    # One more try with the refused lines named; a drafter that still cannot
    # back its lines has nothing true to draw.
    for attempt in range(2):
        result = llm(system, ask) or {}
        title = keep(result.get("headline"), MAX_HEADLINE_WORDS)
        labels = [t for t in (keep(e, MAX_LABEL_WORDS) for e in (result.get("labels") or [])[:MAX_LABELS + 2]) if t]
        labels = list(dict.fromkeys(labels))[:MAX_LABELS]
        scene = " ".join(str(result.get("scene") or "").split())
        if title and len(labels) >= 2 and on_topic([title, *labels]):
            break
        title = ""
        ask = (
            f"{ask}\n\nThese lines were refused because the sentence quoted for them is not in the sheet's "
            "facts or says something else: " + "; ".join(dropped or ["(none written)"])
            + ".\nWrite the poster again. Build each line from the words of one fact sentence and quote that sentence."
        )
    if not title:
        return {
            "ok": False, "dropped": dropped,
            "reason": "The drafter could not back a headline and two labels on that topic with the pitch sheet.",
        }
    backing = {line: backing[line] for line in [title, *labels]}
    dropped = [line for line in dropped if line not in backing]
    site = (profile.site_url() or "").split("://")[-1].strip("/")
    return {
        "ok": True,
        "title": title,
        "callouts": labels,
        "footer": site,
        "scene": scene or "an abstract dark background with soft glowing lines",
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
