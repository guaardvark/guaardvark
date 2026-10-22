"""Facts a person stated in a music description, pulled out before ACE-Step 1.5 plans.

The 1.7B planner writes the caption, the lyrics, and any tempo or key nobody
named. A number or key that is already in the description is not a suggestion:
it is passed through as planner metadata so constrained decoding cannot replace
it. The parser does not invent a genre. Genre wording stays in the text for
the planner, which was trained against ACE-Step's genre list.

Ranges match ACE-Step 1.5 (acestep/constants.py): bpm 30–300, meters 2/4, 3/4,
4/4 and 6/8, language codes from VALID_LANGUAGES. An explicit field outside
those ranges is an error. A match inside free text that falls outside is
ignored, so a stray number does not reject the whole description.
"""
from __future__ import annotations

import re
from typing import Any

# Language codes ACE-Step 1.5 accepts. Names below are the ones people type;
# an explicit field may also be the code itself ("en", "zh").
LANGUAGE_CODES = frozenset({
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en",
    "es", "fa", "fi", "fr", "he", "hi", "hr", "ht", "hu", "id",
    "is", "it", "ja", "ko", "la", "lt", "ms", "ne", "nl", "no",
    "pa", "pl", "pt", "ro", "ru", "sa", "sk", "sr", "sv", "sw",
    "ta", "te", "th", "tl", "tr", "uk", "ur", "vi", "yue", "zh",
})

_LANGUAGE_NAMES: tuple[tuple[str, str], ...] = (
    ("cantonese", "yue"),
    ("mandarin", "zh"),
    ("portuguese", "pt"),
    ("indonesian", "id"),
    ("ukrainian", "uk"),
    ("japanese", "ja"),
    ("spanish", "es"),
    ("english", "en"),
    ("chinese", "zh"),
    ("bengali", "bn"),
    ("arabic", "ar"),
    ("german", "de"),
    ("french", "fr"),
    ("italian", "it"),
    ("korean", "ko"),
    ("russian", "ru"),
    ("hindi", "hi"),
    ("dutch", "nl"),
    ("polish", "pl"),
    ("turkish", "tr"),
    ("swedish", "sv"),
    ("greek", "el"),
    ("hebrew", "he"),
    ("thai", "th"),
    ("vietnamese", "vi"),
)

_LANGUAGE_RE = re.compile(
    r"\b(" + "|".join(re.escape(name) for name, _code in sorted(_LANGUAGE_NAMES, key=lambda item: len(item[0]), reverse=True)) + r")\b",
    re.IGNORECASE,
)
_LANGUAGE_BY_NAME = {name: code for name, code in _LANGUAGE_NAMES}

# "84 bpm". Two or three digits so a year or a 4-digit number is not a tempo.
_BPM_RE = re.compile(r"\b(\d{2,3})\s*bpm\b", re.IGNORECASE)

# "A minor", "F# major", "Bb min". The bare word "am" is English, so a minor
# suffix of one letter only counts as a key when the note is uppercase ("Am")
# or the accidental is written ("F#m").
_KEY_WORD_RE = re.compile(
    r"\b([A-Ga-g])\s*([#b♯♭]?)\s*(major|minor|maj|min)\b",
    re.IGNORECASE,
)
_KEY_SHORT_ACC_RE = re.compile(r"\b([A-Ga-g])([#b♯♭])m\b")
_KEY_SHORT_PLAIN_RE = re.compile(r"\b([A-G])m\b")

_METER_RE = re.compile(r"\b([2346])\s*/\s*([48])\b")
_METER_PAIRS = {("2", "4"): "2", ("3", "4"): "3", ("4", "4"): "4", ("6", "8"): "6"}

_INSTRUMENTAL_RE = re.compile(
    r"\b(?:instrumental|no vocals|without vocals|no singing)\b",
    re.IGNORECASE,
)

_ACCIDENTAL = {"♯": "#", "♭": "b", "#": "#", "b": "b", "": ""}
_MODE = {"major": "major", "minor": "minor", "maj": "major", "min": "minor", "m": "minor"}


def require_bpm(value: Any) -> int:
    """A tempo the caller set on purpose. Outside 30–300 is an error."""
    try:
        bpm = int(value)
    except (TypeError, ValueError):
        raise ValueError("Tempo must be a whole number of bpm between 30 and 300.") from None
    if bpm < 30 or bpm > 300:
        raise ValueError("Tempo must be between 30 and 300 bpm.")
    return bpm


def canonical_key(value: str, *, exact: bool = False) -> str:
    """The first key in ``value``, as ``F# minor``. Empty when none is there.

    ``exact`` requires the whole string to be the key, for a field the person
    edited. A description may name the key in a sentence.
    """
    text = (value or "").strip()
    if not text:
        return ""
    found: list[tuple[int, int, str]] = []
    for pattern, kind in (
        (_KEY_WORD_RE, "word"),
        (_KEY_SHORT_ACC_RE, "acc"),
        (_KEY_SHORT_PLAIN_RE, "plain"),
    ):
        for match in pattern.finditer(text):
            if kind == "word":
                key = _format_key(match.group(1), match.group(2) or "", match.group(3))
            elif kind == "acc":
                key = _format_key(match.group(1), match.group(2), "m")
            else:
                key = _format_key(match.group(1), "", "m")
            if key:
                found.append((match.start(), match.end(), key))
    if not found:
        return ""
    start, end, key = min(found, key=lambda item: item[0])
    if exact and (start != 0 or end != len(text)):
        return ""
    return key


def require_key(value: str) -> str:
    key = canonical_key(value, exact=True)
    if not key:
        raise ValueError("Key must be a note plus major or minor, for example A minor or F# major.")
    return key


def canonical_meter(value: str, *, exact: bool = False) -> str:
    """``4`` for 4/4, ``6`` for 6/8. Empty when the text names no supported meter."""
    text = (value or "").strip()
    if not text:
        return ""
    if exact and text in {"2", "3", "4", "6"}:
        return text
    match = _METER_RE.fullmatch(text) if exact else _METER_RE.search(text)
    if match is None:
        return ""
    return _METER_PAIRS.get((match.group(1), match.group(2)), "")


def require_meter(value: str) -> str:
    meter = canonical_meter(value, exact=True)
    if not meter:
        raise ValueError("Time signature must be 2/4, 3/4, 4/4, or 6/8.")
    return meter


def canonical_language(value: str, *, exact: bool = False) -> str:
    """A language code. Names are matched in prose; a field may be the code."""
    text = (value or "").strip().lower()
    if not text or text == "unknown":
        return ""
    if exact:
        if text in LANGUAGE_CODES:
            return text
        return _LANGUAGE_BY_NAME.get(text, "")
    match = _LANGUAGE_RE.search(text)
    if match is None:
        return ""
    return _LANGUAGE_BY_NAME.get(match.group(1).lower(), "")


def require_language(value: str) -> str:
    code = canonical_language(value, exact=True)
    if not code:
        raise ValueError("Vocal language must be a language ACE-Step knows, for example English or en.")
    return code


def extract_music_facts(text: str) -> dict[str, Any]:
    """Facts named in free text. Absent keys were not stated.

    ``instrumental`` is reported and not applied over the instrumental switch:
    the switch is the explicit choice when the request carries it.
    """
    raw = text or ""
    facts: dict[str, Any] = {}
    bpm_match = _BPM_RE.search(raw)
    if bpm_match is not None:
        bpm = int(bpm_match.group(1))
        if 30 <= bpm <= 300:
            facts["bpm"] = bpm
    key = canonical_key(raw)
    if key:
        facts["keyscale"] = key
    meter = canonical_meter(raw)
    if meter:
        facts["timesignature"] = meter
    language = canonical_language(raw)
    if language:
        facts["vocal_language"] = language
    if _INSTRUMENTAL_RE.search(raw):
        facts["instrumental"] = True
    return facts


def resolve_plan_params(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fill empty constraints from the description. An explicit field wins.

    Returns the params to hand to the planner, and the locks that came from
    the description (so the page can say which numbers were kept). Raises
    ValueError when an explicit field is not one the model can use.
    """
    out = dict(params)
    facts = extract_music_facts(str(params.get("style_prompt") or ""))
    locks: dict[str, Any] = {}

    if _present(params.get("bpm")):
        out["bpm"] = require_bpm(params.get("bpm"))
    elif "bpm" in facts:
        out["bpm"] = facts["bpm"]
        locks["bpm"] = facts["bpm"]

    if _present(params.get("keyscale")):
        out["keyscale"] = require_key(str(params.get("keyscale")))
    elif facts.get("keyscale"):
        out["keyscale"] = facts["keyscale"]
        locks["keyscale"] = facts["keyscale"]

    if _present(params.get("timesignature")):
        out["timesignature"] = require_meter(str(params.get("timesignature")))
    elif facts.get("timesignature"):
        out["timesignature"] = facts["timesignature"]
        locks["timesignature"] = facts["timesignature"]

    if _present(params.get("vocal_language")):
        out["vocal_language"] = require_language(str(params.get("vocal_language")))
    elif facts.get("vocal_language"):
        out["vocal_language"] = facts["vocal_language"]
        locks["vocal_language"] = facts["vocal_language"]

    if "instrumental_only" not in params and facts.get("instrumental"):
        out["instrumental_only"] = True
        locks["instrumental"] = True

    return out, locks


def resolve_accepted_params(params: dict[str, Any]) -> dict[str, Any]:
    """Validate a card the person edited. The description is not parsed again."""
    out = dict(params)
    if _present(params.get("bpm")):
        out["bpm"] = require_bpm(params.get("bpm"))
    else:
        out.pop("bpm", None)
    if _present(params.get("keyscale")):
        out["keyscale"] = require_key(str(params.get("keyscale")))
    else:
        out.pop("keyscale", None)
    if _present(params.get("timesignature")):
        out["timesignature"] = require_meter(str(params.get("timesignature")))
    else:
        out.pop("timesignature", None)
    if _present(params.get("vocal_language")):
        out["vocal_language"] = require_language(str(params.get("vocal_language")))
    else:
        out.pop("vocal_language", None)
    return out


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _format_key(note: str, accidental: str, mode: str) -> str:
    acc = _ACCIDENTAL.get(accidental, "")
    if accidental and acc == "" and accidental not in _ACCIDENTAL:
        return ""
    kind = _MODE.get(mode.lower())
    if not kind or note.upper() not in "ABCDEFG":
        return ""
    return f"{note.upper()}{acc} {kind}"
