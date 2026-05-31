"""Shared parser for auto-editor's kdenlive (MLT XML) export.

auto-editor's ``--export kdenlive`` writes the kept (non-cut) segments onto
BOTH a video and an audio track, so each kept segment appears as two mirrored
``<entry>`` elements. A naive ``iter("entry")`` therefore double-counts every
segment. This helper dedupes by rounded (in, out) and returns a single
chronological list — the canonical parse used by both ``auto_editor_runner``
and ``analyze`` (previously each had its own copy; ``analyze``'s deduped,
``auto_editor_runner``'s did not — the source of the duplicate-clips bug).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def smpte_to_seconds(smpte: Optional[str]) -> Optional[float]:
    """Convert an MLT 'HH:MM:SS.mmm' (or bare seconds) timecode to float seconds."""
    if not smpte:
        return None
    try:
        parts = smpte.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        return float(smpte)
    except (ValueError, TypeError):
        return None


def parse_kept_ranges(kdenlive_path: str | Path) -> list[tuple[float, float]]:
    """Return deduped, chronologically-sorted (start, end) kept segments in seconds.

    Mirrored video/audio entries collapse to one (start, end) via rounded-key
    dedup. Returns ``[]`` if the file is missing or not valid XML.
    """
    from lxml import etree

    try:
        tree = etree.parse(str(kdenlive_path))
    except (etree.XMLSyntaxError, OSError):
        return []

    seen: set[tuple[float, float]] = set()
    ranges: list[tuple[float, float]] = []
    for entry in tree.getroot().iter("entry"):
        in_s = smpte_to_seconds(entry.get("in"))
        out_s = smpte_to_seconds(entry.get("out"))
        if in_s is None or out_s is None or out_s <= in_s:
            continue
        key = (round(in_s, 4), round(out_s, 4))
        if key in seen:
            continue
        seen.add(key)
        ranges.append((in_s, out_s))
    ranges.sort(key=lambda r: r[0])
    return ranges
