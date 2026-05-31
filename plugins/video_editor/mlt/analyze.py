"""Auto-editor wrapper exposing multi-modal (audio + motion) analysis.

auto-editor 29.x supports a Lisp-ish expression for `--edit`:

  --edit audio:threshold=0.04
  --edit motion:threshold=0.02
  --edit "(or audio:0.04 motion:0.02)"     # keep if EITHER passes
  --edit "(and audio:0.04 motion:0.02)"    # keep only if BOTH pass

We export to `--export kdenlive` (MLT XML, parsable with lxml) because the
JSON export this README implied was dropped in 29.x.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from .kdenlive import parse_kept_ranges
from .proc import run_logged

logger = logging.getLogger(__name__)


# Mode literal values must match what the frontend sends.
SCAN_MODE_AUDIO = "audio"
SCAN_MODE_MOTION = "motion"
SCAN_MODE_BOTH_OR = "both-or"     # (or audio motion)
SCAN_MODE_BOTH_AND = "both-and"   # (and audio motion)


@dataclass
class KeptRange:
    """One non-cut segment in source-relative seconds."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class AnalysisResult:
    source_path: Path
    mode: str
    audio_threshold: float
    motion_threshold: float
    kept_ranges: list[KeptRange]
    kdenlive_path: Path


def analyze_clip(
    input_path: str | Path,
    *,
    output_dir: str | Path,
    mode: str = SCAN_MODE_BOTH_AND,
    audio_threshold: float = 0.04,
    motion_threshold: float = 0.02,
    margin: str = "0.2sec",
    auto_editor_path: str = "auto-editor",
    timeout_s: float = 600.0,
) -> AnalysisResult:
    """Run auto-editor over `input_path` and parse the kept segments."""
    source = Path(input_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"input not found: {source}")

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    kdenlive_path = out_dir / f"{source.stem}.{mode}.kdenlive"

    edit_expr = _build_edit_expr(mode, audio_threshold, motion_threshold)
    bin_path = shutil.which(auto_editor_path) or auto_editor_path

    cmd = [
        bin_path,
        str(source),
        "--edit",
        edit_expr,
        "--margin",
        margin,
        "--export",
        "kdenlive",
        "--output",
        str(kdenlive_path),
    ]

    logger.info("analyze: %s", " ".join(cmd))
    proc = run_logged(cmd, timeout_s=timeout_s)

    if proc.returncode != 0 or not kdenlive_path.exists():
        tail = proc.output[-1500:]
        # Common case: clip has no audio stream and mode includes audio. Caller
        # can retry in motion-only mode if they want, but we surface the error.
        raise RuntimeError(f"auto-editor failed (rc={proc.returncode}): {tail}")

    kept_ranges = _parse_kept_ranges(kdenlive_path)
    return AnalysisResult(
        source_path=source,
        mode=mode,
        audio_threshold=audio_threshold,
        motion_threshold=motion_threshold,
        kept_ranges=kept_ranges,
        kdenlive_path=kdenlive_path,
    )


def _build_edit_expr(mode: str, audio_t: float, motion_t: float) -> str:
    if mode == SCAN_MODE_AUDIO:
        return f"audio:threshold={audio_t}"
    if mode == SCAN_MODE_MOTION:
        return f"motion:threshold={motion_t}"
    if mode == SCAN_MODE_BOTH_OR:
        return f"(or audio:{audio_t} motion:{motion_t})"
    if mode == SCAN_MODE_BOTH_AND:
        return f"(and audio:{audio_t} motion:{motion_t})"
    raise ValueError(
        f"unknown mode {mode!r}; expected one of "
        f"{SCAN_MODE_AUDIO!r}/{SCAN_MODE_MOTION!r}/"
        f"{SCAN_MODE_BOTH_OR!r}/{SCAN_MODE_BOTH_AND!r}"
    )


def _parse_kept_ranges(kdenlive_path: Path) -> list[KeptRange]:
    """Deduped, chronological kept segments (delegates to mlt.kdenlive)."""
    return [KeptRange(start=s, end=e) for s, e in parse_kept_ranges(kdenlive_path)]
