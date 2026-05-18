"""Subprocess wrapper for auto-editor (wyattblue/auto-editor).

auto-editor 29.x removed --export json. Valid export targets are 'premiere',
'kdenlive', and 'resolve'. We use 'kdenlive' because Kdenlive's project
format is MLT XML — the same engine Shotcut uses — so the output is directly
mergeable into our pipeline.

Two run modes:
  mode='mp4'      → emit a trimmed .mp4 (auto-editor default)
  mode='kdenlive' → emit a .kdenlive (MLT XML) cut list for downstream merge
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AutoEditorClip:
    """One non-silent segment in the original timeline (seconds, source-relative)."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class AutoEditorResult:
    source_path: Path
    output_path: Path
    mode: str
    clips: list[AutoEditorClip]
    threshold: float


def run_auto_editor(
    input_path: str | Path,
    *,
    output_dir: str | Path,
    auto_editor_path: str = "auto-editor",
    threshold: float = 0.04,
    margin: str = "0.2sec",
    mode: str = "mp4",
    timeout_s: float = 600.0,
) -> AutoEditorResult:
    """Run auto-editor in trim or kdenlive-export mode."""
    source = Path(input_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"input not found: {source}")

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    bin_path = shutil.which(auto_editor_path) or auto_editor_path

    if mode == "mp4":
        out_path = out_dir / f"{source.stem}.trimmed.mp4"
        cmd = [
            bin_path,
            str(source),
            "--edit",
            f"audio:threshold={threshold}",
            "--margin",
            margin,
            "--output",
            str(out_path),
        ]
    elif mode == "kdenlive":
        # auto-editor names kdenlive output after the input stem; we move it
        # afterwards to give the caller a predictable path.
        out_path = out_dir / f"{source.stem}.kdenlive"
        cmd = [
            bin_path,
            str(source),
            "--edit",
            f"audio:threshold={threshold}",
            "--margin",
            margin,
            "--export",
            "kdenlive",
            "--output",
            str(out_path),
        ]
    else:
        raise ValueError(f"unknown mode {mode!r} (expected 'mp4' or 'kdenlive')")

    logger.info("auto-editor: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    if proc.returncode != 0 or not out_path.exists():
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        raise RuntimeError(f"auto-editor failed (rc={proc.returncode}): {tail}")

    clips = _parse_kdenlive_clips(out_path) if mode == "kdenlive" else []

    return AutoEditorResult(
        source_path=source,
        output_path=out_path,
        mode=mode,
        clips=clips,
        threshold=threshold,
    )


def _parse_kdenlive_clips(kdenlive_path: Path) -> list[AutoEditorClip]:
    """Parse a Kdenlive (MLT) project for the non-silent segments auto-editor kept.

    auto-editor's kdenlive export builds a producer per kept segment with
    in/out attributes in 'HH:MM:SS.mmm'. We harvest those and return
    source-relative seconds.
    """
    from lxml import etree

    try:
        tree = etree.parse(str(kdenlive_path))
    except etree.XMLSyntaxError:
        return []
    out: list[AutoEditorClip] = []
    for entry in tree.getroot().iter("entry"):
        in_s = _smpte_to_seconds(entry.get("in"))
        out_s = _smpte_to_seconds(entry.get("out"))
        if in_s is None or out_s is None or out_s <= in_s:
            continue
        out.append(AutoEditorClip(start=in_s, end=out_s))
    return out


def _smpte_to_seconds(smpte: Optional[str]) -> Optional[float]:
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
