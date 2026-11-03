"""Polish an ACE-Step 1.5 song request without rewriting it.

The style and the sung words go to the model exactly as typed. What the polisher
fills in is what the model would otherwise guess: tempo, key, meter, sung language
and length, plus shorter section tags and the timing sketch. Every value it sets is
returned with where it came from, and a value the person set always wins.

Measured on 2026-11-02 (one blues song, 17 sung lines, five seeds each):
  * ACE-Step's own planner rewrote the same description to 130, 71 and 79 bpm and once
    to a male singer, so it is not used here.
  * A tempo, key and meter given to the model held (52 bpm measured 52.7 on every seed).
  * With all of them set, the timing sketch sang 14.4 of 17 lines on average against
    11.6 without it, and changed none of them.
  * Takes that sang every line used about 1.75 bars per line (42 and 52 bpm); a longer
    song only stretches the lines, so the length is sized to the lyric.
The judgement calls (tempo, key and meter for a description) are made by the local chat
model at temperature 0 with a fixed seed, so the same request polishes the same way.
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional

import requests

from backend.config import OLLAMA_BASE_URL
from backend.utils.llm_service import get_saved_active_model_name
from backend.utils.ollama_resource_manager import think_payload
from plugins.audio_foundry.backends.music_prompt_facts import (
    LANGUAGE_CODES,
    canonical_key,
    canonical_language,
    canonical_meter,
    extract_music_facts,
)

logger = logging.getLogger(__name__)

BARS_PER_LINE = 1.75
ENDING_SECONDS = 12
MIN_SECONDS, MAX_SECONDS = 30, 240
_TIMEOUT_S = 60

_TAG_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")

_SYSTEM_PROMPT = """You set up a song for a music model. You never change the person's words.
Given a style description and the section tags of a lyric, choose what a session musician would choose.

Return JSON with exactly these keys:
- "bpm": whole number, 30-300.
- "key": a note and major or minor, e.g. "C minor", "F# major", "Bb minor".
- "meter": one of "4/4", "3/4", "6/8", "2/4".
- "language": two-letter code of the language the lyrics are written in ("en", "es", ...); if there are no lyrics, the description's language, else "en".
- "tags": an object mapping each tag number given to a short tag.

Tempo: very slow or crawling 40-55; slow ballad 56-75; mid-tempo 76-105; upbeat 106-130; fast 131-170; very fast 171+.
Without a tempo word, use the genre's usual tempo (slow blues 50-65, reggae 70-90, hip-hop 85-100, house 120-128, punk 160-200).
Key: minor for sad, dark, desperate, tense or haunting; major for happy, bright, hopeful or triumphant.
Prefer keys the genre's lead instrument favours: guitar E, A, D, G; piano, horns and soul C, F, Bb, Eb.
Meter: 4/4 unless the style says otherwise: waltz 3/4; a rolling slow blues, doo-wop or shuffle ballad may be 6/8; march 2/4.
Tags: keep the section name and add at most a dash and one or two words on how to perform it,
e.g. "[Chorus: Voice breaks with emotion]" -> "[Chorus - emotional]", "[Guitar Solo: slow, weeping, raw]" -> "[Guitar Solo]".
A tag that is already short stays as it is.
Output only the JSON object."""


def _section_tags(lyrics: str) -> List[str]:
    return [m.group(0).strip() for line in lyrics.splitlines() if (m := _TAG_RE.match(line))]


def _short_tag(original: str, proposed: Any) -> str:
    """The polisher's tag when it starts with the same section name and is short;
    otherwise the section name alone. A tag that is already short is kept."""
    inner = _TAG_RE.match(original).group(1).strip()
    if ":" not in inner and len(inner.split()) <= 3:
        return original
    if ":" in inner:
        plain = "[" + " ".join(inner.split(":", 1)[0].split()[:3]) + "]"
    else:
        plain = "[" + " ".join(re.split(r"\s*[,(\-–—]\s*", inner, maxsplit=1)[0].split()[:2]) + "]"
    m = _TAG_RE.match(str(proposed or "").strip())
    if not m:
        return plain
    text = m.group(1).strip()
    name = re.split(r"\s*[-–—]\s*", text, maxsplit=1)[0].strip().lower()
    if not name or ":" in text or len(text.split()) > 5 or not inner.lower().startswith(name):
        return plain
    return f"[{text}]"


def sung_line_count(lyrics: str) -> int:
    return sum(1 for line in (lyrics or "").splitlines() if line.strip() and not _TAG_RE.match(line))


def song_seconds(lines: int, bpm: int) -> int:
    """Length that fits ``lines`` sung lines at ``bpm`` in 4/4, rounded up to 10 s."""
    seconds = lines * BARS_PER_LINE * 240.0 / bpm + ENDING_SECONDS
    return int(min(MAX_SECONDS, max(MIN_SECONDS, math.ceil(seconds / 10.0) * 10)))


def _ask_model(style: str, tags: List[str], lyrics: str, model: str) -> Optional[Dict[str, Any]]:
    numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(tags)) or "(none)"
    sample = "\n".join(l for l in lyrics.splitlines() if l.strip() and not _TAG_RE.match(l))[:600]
    user = f"Style:\n{style}\n\nTags:\n{numbered}\n\nFirst lyric lines:\n{sample or '(instrumental)'}"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user}],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0, "seed": 7, "top_k": 1, "num_ctx": 4096},
        **think_payload(model),
    }
    try:
        resp = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=_TIMEOUT_S)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("Song polish: chat model unavailable: %s", e)
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        block = re.search(r"\{.*\}", content or "", re.S)
        try:
            parsed = json.loads(block.group(0)) if block else None
        except json.JSONDecodeError:
            parsed = None
    return parsed if isinstance(parsed, dict) else None


def polish_song(style: str, lyrics: str = "", instrumental: bool = False,
                given: Optional[Dict[str, Any]] = None, model: Optional[str] = None) -> Dict[str, Any]:
    """Settings for a song, with the words left as typed.

    ``given`` holds values the person set (bpm, keyscale, timesignature, vocal_language,
    duration_s); they are returned unchanged. Without the chat model, only the parts that
    need no judgement are filled and ``fallback`` says why.
    """
    given = {k: v for k, v in (given or {}).items() if v not in (None, "")}
    style = (style or "").strip()
    lyrics = lyrics or ""
    sings = bool(lyrics.strip()) and not instrumental
    facts = extract_music_facts(style)
    chosen = model or get_saved_active_model_name() or "gemma4:e4b"
    tags = _section_tags(lyrics) if sings else []
    answer = _ask_model(style, tags, lyrics if sings else "", chosen) or {}

    fields: Dict[str, Any] = {}
    set_by: Dict[str, str] = {}

    def put(key: str, value: Any, source: str) -> None:
        if value not in (None, ""):
            fields[key], set_by[key] = value, source

    for key, fact, parse, raw in (
        ("bpm", "bpm", lambda v: int(v) if 30 <= int(v) <= 300 else None, answer.get("bpm")),
        ("keyscale", "keyscale", lambda v: canonical_key(str(v), exact=True) or None, answer.get("key")),
        ("timesignature", "timesignature", lambda v: canonical_meter(str(v), exact=True) or None, answer.get("meter")),
    ):
        if key in given:
            put(key, given[key], "you")
        elif fact in facts:
            put(key, facts[fact], "description")
        else:
            try:
                put(key, parse(raw), "polisher")
            except (TypeError, ValueError):
                pass

    if sings:
        language = given.get("vocal_language")
        if language:
            put("vocal_language", language, "you")
        elif facts.get("vocal_language"):
            put("vocal_language", facts["vocal_language"], "description")
        else:
            code = str(answer.get("language") or "").strip().lower()
            code = code if code in LANGUAGE_CODES else canonical_language(code, exact=True)
            put("vocal_language", code or "en", "polisher" if code else "default")

    if "duration_s" in given:
        put("duration_s", given["duration_s"], "you")
    elif sings and fields.get("bpm"):
        put("duration_s", song_seconds(sung_line_count(lyrics), int(fields["bpm"])), "lyrics length")

    proposals = answer.get("tags")
    if isinstance(proposals, list):
        proposals = {str(i): v for i, v in enumerate(proposals)}
    if not isinstance(proposals, dict):
        proposals = {}
    tag_changes = []
    polished_lyrics = lyrics
    if tags:
        lines = lyrics.splitlines()
        index = 0
        for i, line in enumerate(lines):
            if _TAG_RE.match(line):
                new = _short_tag(line.strip(), proposals.get(str(index)))
                if new != line.strip():
                    tag_changes.append([line.strip(), new])
                    lines[i] = new
                index += 1
        polished_lyrics = "\n".join(lines)

    notes = []
    if sings and fields.get("bpm") and "duration_s" not in given:
        needed = sung_line_count(lyrics) * BARS_PER_LINE * 240.0 / int(fields["bpm"]) + ENDING_SECONDS
        if needed > MAX_SECONDS:
            notes.append("too_long")

    result: Dict[str, Any] = {
        "fields": {**fields, "audio_sketch": sings},
        "set_by": {**set_by, **({"audio_sketch": "polisher"} if sings else {})},
        "lyrics": polished_lyrics if sings else "",
        "tag_changes": tag_changes,
        "notes": notes,
        "model": chosen,
    }
    if not answer:
        result.update({"fallback": True, "reason": "chat_model_unavailable"})
    return result
