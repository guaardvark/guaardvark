"""The rhythm an outreach post is typed with.

A post typed at one constant delay per key does not look like a person
typing. The posters ask ``delays(text)`` for the wait before each character
and type one key at a time. The waits are drawn from a profile: the gaps
between a person's own keystrokes, recorded on the Outreach page and kept in
``data/social_outreach/typing_rhythm.json``, split by what was typed just
before (a letter, a space, punctuation, a line break), because people pause
differently after each.

Until a profile is recorded the waits come from ``DEFAULT_PROFILE``. Those
numbers are an assumption (a steady typist at roughly 60 words a minute),
not a measurement; recording replaces them.

Only gap lengths and the class of key are stored, never what was typed.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(os.environ.get("GUAARDVARK_ROOT") or Path(__file__).resolve().parents[3])
PROFILE_PATH = _REPO_ROOT / "data" / "social_outreach" / "typing_rhythm.json"

CLASSES = ("letter", "space", "punct", "newline")
# A recording shorter than this says too little about a person's rhythm.
MIN_KEYS = 200
# A gap longer than this was the person stopping, not typing.
STOP_MS = 5000
# Gaps from here up to STOP_MS are thinking pauses, kept apart from the rhythm.
THINK_MS = 1500
QUANTILES = 21

# Gap quantiles in milliseconds, evenly spaced from the fastest to the
# slowest, per class of the key typed before. Assumed, not measured.
DEFAULT_PROFILE = {
    "source": "default",
    "quantiles": {
        "letter": [60, 75, 85, 95, 102, 110, 118, 125, 132, 140, 148, 156, 165, 175, 186, 198, 214, 235, 265, 320, 480],
        "space": [80, 100, 115, 128, 140, 152, 164, 176, 190, 205, 220, 238, 258, 280, 310, 345, 390, 450, 540, 700, 1100],
        "punct": [120, 160, 190, 220, 250, 280, 310, 345, 380, 420, 465, 515, 570, 640, 720, 820, 950, 1100, 1250, 1380, 1480],
        "newline": [300, 380, 450, 520, 590, 660, 730, 800, 870, 940, 1010, 1080, 1150, 1220, 1290, 1340, 1380, 1410, 1440, 1460, 1490],
    },
    "think": {"per_key": 0.004, "quantiles": [1500, 1800, 2200, 2700, 3400, 4300]},
}

_cache: tuple[float, dict] = (-1.0, DEFAULT_PROFILE)


def key_class(char: str) -> str:
    """The class a typed character falls in."""
    if char == "\n":
        return "newline"
    if char.isspace():
        return "space"
    if char.isalnum():
        return "letter"
    return "punct"


def _quantiles(values: list[float], count: int = QUANTILES) -> list[int]:
    ordered = sorted(values)
    last = len(ordered) - 1
    return [int(round(ordered[round(i * last / (count - 1))])) for i in range(count)]


def from_keystrokes(events: Iterable[dict]) -> Optional[dict]:
    """Build a profile from a recording: ``[{"c": class, "t": ms}, ...]`` in
    the order typed, where ``c`` is one of ``CLASSES`` (anything else, a
    backspace for one, breaks the chain) and ``t`` a millisecond clock.
    None when the recording is shorter than ``MIN_KEYS`` usable gaps."""
    gaps: dict[str, list[float]] = {c: [] for c in CLASSES}
    thinks: list[float] = []
    previous: Optional[dict] = None
    total = 0
    for event in events:
        cls, at = event.get("c"), event.get("t")
        if cls not in CLASSES or not isinstance(at, (int, float)):
            previous = None
            continue
        if previous is not None:
            gap = float(at) - float(previous["t"])
            if 0 < gap < THINK_MS:
                gaps[previous["c"]].append(gap)
                total += 1
            elif THINK_MS <= gap < STOP_MS:
                thinks.append(gap)
        previous = {"c": cls, "t": at}
    if total < MIN_KEYS or len(gaps["letter"]) < MIN_KEYS // 2:
        return None
    quantiles = {
        # A class the recording barely touched keeps the default's shape.
        cls: _quantiles(values) if len(values) >= 12 else DEFAULT_PROFILE["quantiles"][cls]
        for cls, values in gaps.items()
    }
    return {
        "source": "recorded",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "keys": total,
        "quantiles": quantiles,
        "think": {
            "per_key": round(len(thinks) / total, 5),
            "quantiles": _quantiles(thinks, 6) if len(thinks) >= 3 else DEFAULT_PROFILE["think"]["quantiles"],
        },
    }


def save(profile: dict) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=1), encoding="utf-8")


def clear() -> None:
    try:
        PROFILE_PATH.unlink()
    except FileNotFoundError:
        pass


def load() -> dict:
    """The recorded profile, else ``DEFAULT_PROFILE``. Re-read when the file changes."""
    global _cache
    try:
        mtime = PROFILE_PATH.stat().st_mtime
    except OSError:
        return DEFAULT_PROFILE
    if _cache[0] == mtime:
        return _cache[1]
    try:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        if not all(len(profile["quantiles"][c]) >= 2 for c in CLASSES):
            raise ValueError("a class has no quantiles")
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("typing rhythm unreadable, using the default: %s", e)
        return DEFAULT_PROFILE
    _cache = (mtime, profile)
    return profile


def summary() -> dict:
    """What the Outreach page shows about the rhythm in use."""
    profile = load()
    letters = profile["quantiles"]["letter"]
    return {
        "recorded": profile.get("source") == "recorded",
        "recorded_at": profile.get("recorded_at"),
        "keys": profile.get("keys"),
        "median_letter_ms": letters[len(letters) // 2],
        "min_keys": MIN_KEYS,
    }


def _draw(quantiles: list, rng: random.Random) -> float:
    """A value from the distribution the evenly spaced quantiles describe."""
    position = rng.random() * (len(quantiles) - 1)
    low = int(position)
    high = min(low + 1, len(quantiles) - 1)
    return quantiles[low] + (quantiles[high] - quantiles[low]) * (position - low)


def delays(text: str, rng: Optional[random.Random] = None) -> list[float]:
    """Seconds to wait before each character of ``text``, in order."""
    rng = rng or random.Random()
    profile = load()
    think = profile.get("think") or DEFAULT_PROFILE["think"]
    out: list[float] = []
    previous = "newline"
    for char in text:
        wait = _draw(profile["quantiles"][previous], rng)
        # A thinking pause lands between words, not inside one.
        if previous != "letter" and rng.random() < float(think.get("per_key") or 0) * 4:
            wait = _draw(think["quantiles"], rng)
        out.append(wait / 1000.0)
        previous = key_class(char)
    if out:
        out[0] = 0.0
    return out
