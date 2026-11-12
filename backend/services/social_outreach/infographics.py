"""The infographics outreach may attach to a post.

Every infographic the generator files in the Library can be marked a keeper.
A keeper is usable in a post only once a vision model has read the text drawn
on it and that text matches what the image model was asked to draw.

Curation lives on the Library document, under ``file_metadata["outreach"]``::

    {"keeper": bool, "topics": [str],
     "readback": {"status": "unread" | "reading" | "passed" | "failed",
                  "transcript": str, "expected": [str], "missing": [str],
                  "extra": [str], "reasons": [str], "model": str,
                  "checked_at": iso, "error": str}}

``expected`` is always the text the prompt asked for, never what the reader
saw; the reader's words are kept apart in ``transcript``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SOURCE = "infographic"
READBACK_TASK = "social_outreach.read_back_infographic"
READBACK_MODEL_ENV = "GUAARDVARK_OUTREACH_READBACK_MODEL"

# Readers in order of preference; the first one installed is used. Measured
# through read() on eight tall MOSS-Image posters checked by eye (six clean,
# one with a word drawn twice, one with a smeared headline word), Ollama
# 0.40.15, reader on CPU (the GPU was rendering), two calls per image:
#   gemma4:12b    8/8 verdicts right, every transcript letter-true, 17.6 s median
#   gemma4:e4b    8/8 verdicts right, one "GUAARDVARK" read as "GUARDVARK", 12.9 s
#   minicpm-v4.6  0/6 clean posters passed (misreads the headline), 3.9 s
#   qwen3-vl 2b, 4b, 8b-thinking: no transcript; they ignore think=false and
#     spend all 400 output tokens thinking.
# gemma4:12b then read all 78 posters of that run: 63 passed, 15 failed; by
# eye 14 of the 15 are real faults (a dropped or doubled letter, a wrong
# letter, a doubled or smeared word) and one is a misread of small monospace
# ("-fsSL" read as "-fSL"); 14 of the passes looked at were all clean.
READBACK_MODELS: tuple[str, ...] = ("gemma4:12b", "gemma4:e4b")

# A read-back still "reading" after this long was lost (worker restarted, task
# never picked up) and may be queued again.
READING_STALE_S = 600

# Seconds one transcription may take, model load included.
READ_TIMEOUT_S = 240

# Unasked-for text shorter than this (letters and digits of the words that
# were not asked for, summed over one transcribed line) is decoration and does
# not fail an image; see compare(). Not yet measured on real posters: none of
# the 78 above carried any unasked-for text.
STRAY_CHARS = 12
STRAY_WORDS = 3

_INSTRUCTION = (
    "Transcribe every piece of text visible in this image exactly as it is spelled, letter for letter. "
    "Keep misspellings, missing letters, extra letters and garbled words exactly as they appear; do not "
    "correct anything and do not guess what a word was meant to be. Write one text item per line, top to "
    "bottom. Output only the text."
)

_DAMAGE_QUESTION = (
    "Look at every piece of lettering in this image. Is any word damaged: letters smeared, overlapped, "
    "broken, replaced by shapes, or otherwise not cleanly readable? Answer on one line, either "
    "CLEAN or DAMAGED: followed by where the damage is."
)

# Where a pasted markdown block starts inside a freeform prompt: a code fence
# or the "Structured fields" heading. Nothing from there on is text to draw.
_TAIL = re.compile(r"^\s*(?:```|\*\*\s*structured fields)", re.IGNORECASE | re.MULTILINE)
_QUOTED = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')
_TAIL_FIELD = re.compile(r"^\s*-\s*(title|footer)\s*:\s*`([^`]+)`", re.IGNORECASE | re.MULTILINE)
_TAIL_CALLOUTS = re.compile(r"^\s*-\s*callouts\s*:\s*\n\s*```\s*\n(.*?)\n\s*```", re.IGNORECASE | re.MULTILINE | re.DOTALL)

# Prompt markup that has no business on a poster.
_MARKUP = re.compile(
    r"\*\*|`|structured fields|^\s*[-*]\s*(?:title|scene|callouts?|footer|hashtags?)\b"
    r"|^\s*(?:title|scene|callouts?|footer|hashtags?)\s*:",
    re.IGNORECASE,
)
# A line whose punctuation is its content: a command, a path, an address, a
# version number.
_LITERAL = re.compile(r"://|--|[/|\\]|\w\.\w")


class InfographicNotFound(LookupError):
    """No Library document with this id is an infographic."""


class InfographicFileMissing(FileNotFoundError):
    """The document exists but its image file does not."""


class InfographicNotUsable(ValueError):
    """The infographic is not a keeper whose text was read back and matched."""


class ReadbackUnavailable(RuntimeError):
    """The vision model could not be asked, or gave nothing back."""


# --- Metadata -------------------------------------------------------------


def _meta(doc) -> dict[str, Any]:
    try:
        meta = json.loads(doc.file_metadata or "{}")
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def is_infographic(meta: dict[str, Any]) -> bool:
    return meta.get("source") == SOURCE


def _prompt(meta: dict[str, Any]) -> str:
    """The prompt the image was rendered from, in either metadata shape."""
    spec = meta.get("infographic") if isinstance(meta.get("infographic"), dict) else {}
    return str(spec.get("raw_prompt") or meta.get("prompt") or meta.get("original_prompt") or "")


def _unique(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for line in lines:
        line = re.sub(r"\s+", " ", str(line or "")).strip()
        key = _compact(line)
        if key and key not in seen:
            seen.add(key)
            out.append(line)
    return out


def prompt_text(prompt: str) -> list[str]:
    """The strings a freeform prompt tells the model to draw: its quoted
    strings, in order. A markdown block pasted after the prompt (a code fence
    or a "Structured fields" list repeating the same fields) is not read for
    quotes; its Title, Callouts and Footer are used only when the prompt
    itself quotes nothing."""
    match = _TAIL.search(prompt or "")
    head = prompt[:match.start()] if match else (prompt or "")
    lines = [a or b for a, b in _QUOTED.findall(head)]
    if not lines and match:
        tail = prompt[match.start():]
        fields = {name.lower(): value for name, value in _TAIL_FIELD.findall(tail)}
        callouts = _TAIL_CALLOUTS.search(tail)
        lines = [fields.get("title", "")]
        lines += callouts.group(1).splitlines() if callouts else []
        lines.append(fields.get("footer", ""))
    return _unique(lines)


def expected_text(meta: dict[str, Any]) -> list[str]:
    """Every string the image was asked to show, headline first: the title,
    callouts, footer and hashtag line of a structured spec, or the quoted
    strings of a freeform prompt."""
    spec = meta.get("infographic") if isinstance(meta.get("infographic"), dict) else {}
    if spec and not spec.get("raw_prompt"):
        tags = " ".join(
            t if t.startswith("#") else f"#{t}"
            for t in (str(t).strip() for t in spec.get("hashtags") or []) if t
        )
        callouts = [str(c).strip().strip('"') for c in spec.get("callouts") or []]
        lines = _unique([spec.get("title") or "", *callouts, spec.get("footer") or "", tags])
        if lines:
            return lines
    return prompt_text(_prompt(meta))


def _curation(meta: dict[str, Any]) -> dict[str, Any]:
    """``file_metadata["outreach"]`` with every key present. A read-back left
    "reading" past READING_STALE_S reads as unread."""
    raw = meta.get("outreach") if isinstance(meta.get("outreach"), dict) else {}
    readback = dict(raw.get("readback")) if isinstance(raw.get("readback"), dict) else {}
    status = readback.get("status")
    if status not in ("passed", "failed", "reading"):
        status = "unread"
    if status == "reading":
        try:
            age = (datetime.now() - datetime.fromisoformat(readback.get("started_at") or "")).total_seconds()
        except ValueError:
            age = READING_STALE_S + 1
        if age > READING_STALE_S:
            status = "unread"
            readback["error"] = "The read-back did not finish."
    readback["status"] = status
    for key in ("expected", "missing", "extra", "reasons"):
        readback[key] = list(readback.get(key) or [])
    readback.setdefault("transcript", "")
    readback.setdefault("model", None)
    readback.setdefault("checked_at", None)
    topics = [str(t) for t in raw.get("topics") or [] if str(t).strip()]
    return {**raw, "keeper": bool(raw.get("keeper")), "topics": topics, "readback": readback}


def _size(meta: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    if meta.get("width") and meta.get("height"):
        return int(meta["width"]), int(meta["height"])
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", str(meta.get("dimensions") or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _aspect(width: Optional[int], height: Optional[int]) -> Optional[str]:
    if not width or not height:
        return None
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def image_url(document_id: int) -> str:
    return f"/api/social-outreach/infographics/{int(document_id)}/image"


def _item(doc, meta: dict[str, Any]) -> dict[str, Any]:
    spec = meta.get("infographic") if isinstance(meta.get("infographic"), dict) else {}
    lines = expected_text(meta)
    width, height = _size(meta)
    curation = _curation(meta)
    return {
        "document_id": doc.id,
        "image_url": image_url(doc.id),
        "headline": lines[0] if lines else "",
        "lines": lines[1:],
        "aspect": spec.get("aspect") or _aspect(width, height),
        "width": width,
        "height": height,
        "model": meta.get("model") or meta.get("model_used"),
        "created_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        "keeper": curation["keeper"],
        "topics": curation["topics"],
        "readback": curation["readback"],
        "usable": curation["keeper"] and curation["readback"]["status"] == "passed",
    }


def _document(document_id: int, lock: bool = False):
    from backend.models import Document, db

    query = db.session.query(Document).filter(Document.id == int(document_id))
    if lock:
        query = query.with_for_update()
    doc = query.first()
    meta = _meta(doc) if doc is not None else {}
    if doc is None or not is_infographic(meta):
        raise InfographicNotFound(f"Document {document_id} is not an infographic.")
    return doc, meta


def _change(document_id: int, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Apply ``change`` to the document's curation and save it. The row is
    read again under a lock and only the ``outreach`` key is replaced, so a
    slow read-back does not undo a keeper mark made meanwhile, and no other
    metadata key is touched."""
    from backend.models import db

    doc, meta = _document(document_id, lock=True)
    curation = _curation(meta)
    change(curation)
    meta["outreach"] = curation
    doc.file_metadata = json.dumps(meta)
    db.session.commit()
    return _item(doc, meta)


# --- Library --------------------------------------------------------------


def items(keepers_only: bool = False) -> list[dict[str, Any]]:
    """Every infographic in the Library, newest first, as ``{document_id,
    image_url, headline, lines, aspect, width, height, model, created_at,
    keeper, topics, readback, usable}``. ``usable`` is a keeper whose
    read-back passed."""
    from backend.models import Document

    docs = (
        Document.query
        .filter(Document.file_metadata.like(f"%{SOURCE}%"))
        .order_by(Document.uploaded_at.desc(), Document.id.desc())
        .all()
    )
    out = []
    for doc in docs:
        meta = _meta(doc)
        if not is_infographic(meta):
            continue
        item = _item(doc, meta)
        if keepers_only and not item["keeper"]:
            continue
        out.append(item)
    return out


def describe(document_id: int) -> dict[str, Any]:
    """One infographic, in the shape items() lists."""
    doc, meta = _document(document_id)
    return _item(doc, meta)


def file_path(document_id: int) -> Path:
    """The image file. Raises InfographicFileMissing when it is gone or its
    recorded path leaves the uploads folder."""
    from backend.config import UPLOAD_DIR
    from backend.utils.path_guard import PathEscapesRoot, contained

    doc, _ = _document(document_id)
    try:
        path = contained(UPLOAD_DIR, doc.path)
        # A symlink must not lead out of the uploads folder either.
        contained(Path(UPLOAD_DIR).resolve(), path.resolve())
    except PathEscapesRoot:
        raise InfographicFileMissing(f"Infographic {document_id} is not stored in the uploads folder.") from None
    if not path.is_file():
        raise InfographicFileMissing(f"The image file of infographic {document_id} is missing: {doc.path}")
    return path


def mark(document_id: int, keep: bool, topics: Optional[list[str]] = None) -> dict[str, Any]:
    """Mark or unmark a keeper (and set its topics when given). A new keeper
    that was never read back gets its read-back queued."""
    def change(curation: dict[str, Any]) -> None:
        curation["keeper"] = bool(keep)
        if topics is not None:
            curation["topics"] = [str(t).strip() for t in topics if str(t).strip()]

    item = _change(document_id, change)
    if keep and item["readback"]["status"] == "unread":
        return queue_readback(document_id)
    return item


def queue_readback(document_id: int, dispatch: Optional[Callable[[int], None]] = None) -> dict[str, Any]:
    """Queue the read-back on the worker and answer at once with the item,
    its read-back "reading". If it cannot be queued the status goes back to
    "unread" with the reason."""
    def reading(curation: dict[str, Any]) -> None:
        curation["readback"] = {"status": "reading", "started_at": datetime.now().isoformat()}

    item = _change(document_id, reading)
    try:
        (dispatch or _dispatch)(int(document_id))
    except Exception as e:  # noqa: BLE001 - an unqueued read-back must not stay "reading"
        logger.error("infographic %s: read-back could not be queued: %s", document_id, e)
        return _store(document_id, {"status": "unread", "error": f"The read-back could not be queued: {e}"})
    return item


def _dispatch(document_id: int) -> None:
    from backend.celery_app import celery

    celery.send_task(READBACK_TASK, args=[document_id], queue="default")


def _store(document_id: int, readback_result: dict[str, Any]) -> dict[str, Any]:
    def change(curation: dict[str, Any]) -> None:
        curation["readback"] = readback_result

    return _change(document_id, change)


# --- Read-back ------------------------------------------------------------


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    for old, new in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-")):
        text = text.replace(old, new)
    return text.upper()


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Z0-9]+", _fold(text))


def _compact(text: str) -> str:
    """Letters and digits only: case, spacing, line breaks and punctuation
    do not count."""
    return "".join(_words(text))


def _literal(text: str) -> str:
    """Spacing and case dropped, punctuation kept."""
    return re.sub(r"\s+", "", _fold(text))


def _shown(line: str, transcript: str) -> bool:
    if _LITERAL.search(line):
        return _literal(line) in _literal(transcript)
    return _compact(line) in _compact(transcript)


def compare(expected: list[str], transcript: str, prompt: str = "") -> dict[str, Any]:
    """Check a transcript against the text that was asked for. Returns
    ``{passed, missing, extra, reasons}``.

    missing: an expected line that is not in the transcript. Case, spacing,
    line breaks and punctuation are ignored, so a headline wrapped over two
    lines still matches; a misspelled line does not. A line whose punctuation
    is its content (a command, a path, an address, a version number) must
    also show its punctuation.

    extra: each transcribed line holding a word that was not asked for, or a
    word drawn more often than it was asked for.

    An image passes when nothing is missing and no extra is one of:
      - prompt markup (``**``, backticks, "Structured fields", a "Title:" or
        "Callouts:" field label);
      - words of the prompt's own instructions drawn as text;
      - an asked-for word drawn more often than asked (a label drawn twice,
        a stray second "OR" above its box);
      - unasked-for text of STRAY_WORDS words or STRAY_CHARS characters or
        more on one line.
    Shorter unasked-for text (a chip reading "AI", a step number, the
    lettering of a sign or tattoo in the artwork) is listed in ``extra`` and
    does not fail the image.

    A vision model now and then reads a misspelled word as the correct one,
    so "passed" means "no fault found", and a person still looks before
    posting.
    """
    expected = [line for line in expected if _compact(line)]
    left = Counter(word for line in expected for word in _words(line))
    asked = set(left)
    prompt_compact = _compact(prompt)
    missing = [line for line in expected if not _shown(line, transcript)]
    reasons = [f'Missing or misspelled: "{line}"' for line in missing]

    extra: list[str] = []
    for raw in (transcript or "").splitlines():
        line = raw.strip()
        unasked, repeated = [], []
        for word in _words(line):
            if word not in asked:
                unasked.append(word)
            elif left[word] > 0:
                left[word] -= 1
            else:
                repeated.append(word)
        markup = bool(_MARKUP.search(line))
        if not (unasked or repeated or markup):
            continue
        extra.append(line)
        if markup:
            reasons.append(f'Prompt markup drawn on the image: "{line}"')
        elif unasked and len(_compact(line)) >= STRAY_CHARS and _compact(line) in prompt_compact:
            reasons.append(f'Prompt instructions drawn on the image: "{line}"')
        elif len(unasked) >= STRAY_WORDS or len("".join(unasked)) >= STRAY_CHARS:
            reasons.append(f'Text that was not asked for: "{line}"')
        elif repeated:
            reasons.append(f'Drawn more often than asked: "{" ".join(repeated)}" in "{line}"')

    return {"passed": not reasons, "missing": missing, "extra": extra, "reasons": reasons}


def readback_model() -> Optional[str]:
    """The vision model that reads infographics: the READBACK_MODEL_ENV
    override, else the first of READBACK_MODELS that is installed and takes
    images, else the vision model the product picks for itself."""
    override = (os.environ.get(READBACK_MODEL_ENV) or "").strip()
    if override:
        return override
    try:
        from backend.services.model_capability_resolver import _installed, sees_natively

        installed = set(_installed())
        for name in READBACK_MODELS:
            if name in installed and sees_natively(name):
                return name
        if any(sees_natively(name) for name in installed):
            from backend.utils.vision_analyzer import VisionAnalyzer

            return VisionAnalyzer().default_model
    except Exception as e:  # noqa: BLE001
        logger.warning("infographic read-back: no vision model could be chosen: %s", e)
    return None


def _ask(path: Path, question: str, model: Optional[str], num_predict: int) -> tuple[str, str]:
    """One question about the image to the vision model: (answer, model)."""
    from PIL import Image

    from backend.utils.vision_analyzer import VisionAnalyzer

    model = model or readback_model()
    if not model:
        raise ReadbackUnavailable("No vision model is installed to read the image.")
    analyzer = VisionAnalyzer(default_model=model, timeout=READ_TIMEOUT_S)
    with Image.open(path) as image:
        image.load()
        # A reader sometimes answers with an empty message; a little sampling
        # gets an answer on the retry.
        for temperature in (0.0, 0.2, 0.4):
            result = analyzer.analyze_fullsize(image, question, model=model, num_predict=num_predict,
                                               temperature=temperature)
            if not result.success:
                raise ReadbackUnavailable(result.error or "the vision model failed")
            if result.description.strip():
                return result.description.strip(), model
    raise ReadbackUnavailable(f"{model} returned no text")


def transcribe(path: Path, model: Optional[str] = None) -> tuple[str, str]:
    """All text visible in the image, one item per line, and the model that
    read it. Raises ReadbackUnavailable when no model could be asked or the
    model answered nothing."""
    # The longest real transcription is ~150 tokens; the cap stops a
    # repetition loop from running for minutes.
    return _ask(path, _INSTRUCTION, model, 400)


def damage(path: Path, model: Optional[str] = None) -> str:
    """Where lettering in the image is damaged (smeared, overlapped, broken),
    or "" when the reader finds it clean. A reader transcribes a damaged
    word as the word it was meant to be, so the transcript alone does not
    show this. Raises ReadbackUnavailable when the answer is neither."""
    answer, _ = _ask(path, _DAMAGE_QUESTION, model, 120)
    first = answer.splitlines()[0].strip().strip("*").strip()
    if first.upper().startswith("CLEAN"):
        return ""
    if first.upper().startswith("DAMAGED"):
        return first[len("DAMAGED"):].lstrip(" :").strip() or "damaged lettering"
    raise ReadbackUnavailable(f"the lettering check gave no verdict: {answer[:80]}")


def read(meta: dict[str, Any], path: Path,
         transcriber: Optional[Callable[[Path], tuple[str, str]]] = None,
         inspector: Optional[Callable[[Path], str]] = None) -> dict[str, Any]:
    """Read one image back and judge it, without touching the database:
    the ``readback`` object to store. The transcript is compared with the
    expected text (compare()); an image that gets through that is then
    checked for damaged lettering (damage()). A model that could not be
    asked, or an image whose prompt names no text, leaves the status
    "unread" with ``error``; nothing passes unread."""
    expected = expected_text(meta)
    if not expected:
        return {"status": "unread", "expected": [],
                "error": "The prompt names no text to draw, so there is nothing to check the image against."}
    try:
        transcript, model = (transcriber or transcribe)(path)
        verdict = compare(expected, transcript, _prompt(meta))
        damaged = (inspector or damage)(path) if verdict["passed"] else ""
    except Exception as e:  # noqa: BLE001 - any failure to read leaves the image unread
        return {"status": "unread", "expected": expected, "error": f"The image could not be read: {e}"}
    reasons = verdict["reasons"] + ([f"Damaged lettering: {damaged}"] if damaged else [])
    return {
        "status": "failed" if reasons else "passed",
        "transcript": transcript,
        "expected": expected,
        "missing": verdict["missing"],
        "extra": verdict["extra"],
        "reasons": reasons,
        "model": model,
        "checked_at": datetime.now().isoformat(),
    }


def readback(document_id: int,
             transcriber: Optional[Callable[[Path], tuple[str, str]]] = None,
             inspector: Optional[Callable[[Path], str]] = None) -> dict[str, Any]:
    """Read the image's text back now, store the result on the document and
    return the item. Takes as long as the vision model does; HTTP callers
    use queue_readback()."""
    _, meta = _document(document_id)
    try:
        path = file_path(document_id)
    except InfographicFileMissing as e:
        return _store(document_id, {"status": "unread", "expected": expected_text(meta), "error": str(e)})
    return _store(document_id, read(meta, path, transcriber, inspector))


# --- Media on an outreach row ---------------------------------------------


def media_for(document_id: int) -> dict[str, Any]:
    """The ``media_json`` object for an outreach row: ``{document_id, path,
    alt}``; ``path`` is relative to the uploads folder, as the Library keeps
    it. Only a usable infographic (a keeper whose read-back passed) can be
    attached."""
    doc, meta = _document(document_id)
    item = _item(doc, meta)
    if not item["usable"]:
        raise InfographicNotUsable(
            f"Infographic {document_id} is not a keeper whose text was read back and matched."
        )
    file_path(document_id)
    alt = ". ".join(part.rstrip(".") for part in [item["headline"], *item["lines"]] if part)
    return {"document_id": doc.id, "path": doc.path, "alt": alt}


def media_of(row) -> Optional[dict[str, Any]]:
    """The image an outreach row carries, parsed from ``media_json``, or None."""
    raw = getattr(row, "media_json", None)
    if not raw:
        return None
    try:
        media = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(media, dict):
        return None
    try:
        document_id = int(media.get("document_id"))
    except (TypeError, ValueError):
        return None
    return {"document_id": document_id, "path": str(media.get("path") or ""), "alt": str(media.get("alt") or "")}
