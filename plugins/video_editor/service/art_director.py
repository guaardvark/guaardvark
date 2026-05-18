"""Art Director — vision-model client that turns sampled frames into a ClipAnalysis.

Uses Ollama's /api/generate endpoint with the `images` parameter (list of
base64-encoded JPEGs) and `format: "json"` for structured output. JSON parsing
is tolerant — if the model returns extra prose or fenced code blocks, we
extract the first JSON object we can find.

Failure modes:
  - Ollama unreachable / model not loaded → return neutral defaults, log warning
  - Model returns invalid JSON → strip code fences, retry parsing; if still
    bad, return neutral defaults with the model's text in `mood` for debug
  - Vision model rejects empty image list → return neutral defaults
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_TIMEOUT_S = 60.0


# Allowed enum values — keep these in sync with the prompt schema below.
_ALLOWED_SUBJECTS = {"wide-landscape", "character-closeup", "object-detail", "crowd", "text-or-ui", "abstract"}
_ALLOWED_ENERGY = {"calm", "medium", "high", "frenetic"}
_ALLOWED_PALETTES = {"warm", "cool", "neutral", "high-contrast"}
_ALLOWED_MOTION = {"static", "slow", "medium", "fast"}
_ALLOWED_MOODS = {"uplifting", "tense", "nostalgic", "aggressive", "mysterious", "playful"}
_ALLOWED_SECTIONS = {"intro", "build", "drop", "outro", "any"}


PROMPT_TEMPLATE = """You are an Art Director analyzing a B-roll clip for inclusion in a music video.
Look at these {n_frames} frames sampled evenly across the clip.
Return JSON only, no prose, no markdown fences:

{{
  "subject": one of {subjects},
  "energy": one of {energies},
  "dominant_palette": one of {palettes},
  "motion": one of {motions},
  "mood": one of {moods},
  "recommended_filter": one of {filters} or "none",
  "best_section_fit": array of one or more from {sections}
}}"""


def build_prompt(n_frames: int, available_filter_slugs: list[str]) -> str:
    return PROMPT_TEMPLATE.format(
        n_frames=n_frames,
        subjects=sorted(_ALLOWED_SUBJECTS),
        energies=sorted(_ALLOWED_ENERGY),
        palettes=sorted(_ALLOWED_PALETTES),
        motions=sorted(_ALLOWED_MOTION),
        moods=sorted(_ALLOWED_MOODS),
        filters=available_filter_slugs,
        sections=sorted(_ALLOWED_SECTIONS),
    )


def analyze_frames(
    frame_paths: list[Path],
    *,
    available_filter_slugs: list[str],
    model: str = DEFAULT_MODEL,
    ollama_url: str = "http://localhost:11434",
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Run a single vision-model call on `frame_paths` and return a normalized analysis dict.

    Returns the dict shape that ClipAnalysis expects (subject, energy,
    dominant_palette, motion, mood, recommended_filter, best_section_fit).
    Never raises — failure returns the neutral default with a logged warning.
    """
    if not frame_paths:
        return _neutral_defaults()

    encoded = []
    for p in frame_paths:
        try:
            encoded.append(base64.b64encode(p.read_bytes()).decode("ascii"))
        except OSError as e:
            logger.warning("could not read frame %s: %s", p, e)
    if not encoded:
        return _neutral_defaults()

    prompt = build_prompt(len(encoded), available_filter_slugs)
    payload = {
        "model": model,
        "prompt": prompt,
        "images": encoded,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.3, "top_p": 0.9},
    }

    try:
        response = httpx.post(f"{ollama_url}/api/generate", json=payload, timeout=timeout_s)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Art Director Ollama call failed: %s", e)
        return _neutral_defaults()

    raw = (body.get("response") or "").strip()
    parsed = _parse_response_json(raw)
    if parsed is None:
        logger.warning("Art Director response was not parseable as JSON: %r", raw[:200])
        return _neutral_defaults()

    return _normalize(parsed, available_filter_slugs)


def _parse_response_json(raw: str) -> Optional[dict[str, Any]]:
    """Tolerantly extract the first JSON object from `raw`."""
    if not raw:
        return None
    # Strip markdown fences.
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # Find the first {...} block.
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", stripped, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _normalize(parsed: dict[str, Any], available_filter_slugs: list[str]) -> dict[str, Any]:
    """Clamp every field to an allowed value; unknown values → neutral default."""
    def pick(value: Any, allowed: set[str], default: str) -> str:
        s = str(value).strip().lower() if value is not None else ""
        return s if s in allowed else default

    section_fit = parsed.get("best_section_fit") or []
    if not isinstance(section_fit, list):
        section_fit = [section_fit] if section_fit else []
    cleaned_sections = [
        str(s).strip().lower() for s in section_fit
        if str(s).strip().lower() in _ALLOWED_SECTIONS
    ]
    if not cleaned_sections:
        cleaned_sections = ["any"]

    rec_filter = parsed.get("recommended_filter") or "none"
    rec_filter_str = str(rec_filter).strip().lower()
    if rec_filter_str != "none" and available_filter_slugs and rec_filter_str not in available_filter_slugs:
        rec_filter_str = "none"

    return {
        "subject":            pick(parsed.get("subject"), _ALLOWED_SUBJECTS, "abstract"),
        "energy":             pick(parsed.get("energy"), _ALLOWED_ENERGY, "medium"),
        "dominant_palette":   pick(parsed.get("dominant_palette"), _ALLOWED_PALETTES, "neutral"),
        "motion":             pick(parsed.get("motion"), _ALLOWED_MOTION, "medium"),
        "mood":               pick(parsed.get("mood"), _ALLOWED_MOODS, "uplifting"),
        "recommended_filter": rec_filter_str,
        "best_section_fit":   cleaned_sections,
    }


def _neutral_defaults() -> dict[str, Any]:
    return {
        "subject": "abstract",
        "energy": "medium",
        "dominant_palette": "neutral",
        "motion": "medium",
        "mood": "uplifting",
        "recommended_filter": "none",
        "best_section_fit": ["any"],
    }
