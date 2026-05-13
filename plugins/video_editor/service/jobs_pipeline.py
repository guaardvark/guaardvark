"""Plan pipeline — the orchestrator behind POST /plan.

Stages, in order:

  1. Auto-editor analyze each bin clip → keptRanges per clip (parallelized).
  2. Librosa beat + section analysis on the song → SongStructure.
  3. Vision-model analyze each clip (cached) → ClipAnalysis.
     v1: returns neutral defaults via LocalArtDirector (no GPU spend).
     A3:  real qwen3-vl call.
  4. Arrange via CrewInterface.arrange → Arrangement.

The output is the full Arrangement plus per-clip kept-ranges, ready for the
frontend to render as a preview and to feed into POST /shotcut/compose.

This module is intentionally NOT a celery task; it runs inside the plugin's
in-memory JobTable. The plugin restart wipes state — acceptable for a
single-user box. See plan §"Architectural commitments".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from mlt import analyze as analyze_mod
from mlt.clip_hash import cache_path_for, hash_clip
from mlt.frame_sampler import sample_frames
from mlt.song_structure import analyze_song

from service.crew_interface import (
    Arrangement,
    ClipAnalysis,
    CrewInterface,
    SongAnalysis,
)

logger = logging.getLogger(__name__)


@dataclass
class BinClip:
    clip_id: str
    source_path: str
    document_id: Optional[int] = None


@dataclass
class PlanRequest:
    bin_clips: list[BinClip]
    song_path: str
    scan_mode: str = analyze_mod.SCAN_MODE_BOTH_AND
    audio_threshold: float = 0.04
    motion_threshold: float = 0.02
    margin: str = "0.2sec"
    style_recipe: Optional[dict[str, Any]] = None
    seed: int = 0


@dataclass
class PlanResult:
    arrangement: Arrangement
    song: SongAnalysis
    kept_ranges_by_clip: dict[str, list[tuple[float, float]]]
    clip_analyses: list[ClipAnalysis] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arrangement": self.arrangement.to_dict(),
            "song": self.song.to_dict(),
            "kept_ranges_by_clip": {
                cid: [list(r) for r in rngs] for cid, rngs in self.kept_ranges_by_clip.items()
            },
            "clip_analyses": [c.to_dict() for c in self.clip_analyses],
            "warnings": self.warnings,
        }


def run_plan(
    req: PlanRequest,
    *,
    crew: CrewInterface,
    analyze_out_dir: Path,
    vision_cache_dir: Path,
    progress_cb=None,
) -> PlanResult:
    """Execute the full Plan pipeline; return everything the UI needs."""
    warnings: list[str] = []

    if progress_cb:
        progress_cb(0.05, "Analyzing clips")
    kept_ranges_by_clip = _analyze_all_clips(
        req=req,
        out_dir=analyze_out_dir,
        warnings=warnings,
        progress_cb=progress_cb,
    )

    if progress_cb:
        progress_cb(0.40, "Analyzing song")
    song_struct = analyze_song(req.song_path)
    song = SongAnalysis(
        tempo_bpm=song_struct.tempo_bpm,
        duration_seconds=song_struct.duration_seconds,
        beat_times=song_struct.beat_times,
        sections=[s.to_dict() for s in song_struct.sections],
    )

    if progress_cb:
        progress_cb(0.60, "Art Director scanning clips")
    clip_analyses = _vision_analyze_all_clips(
        req=req,
        crew=crew,
        cache_dir=vision_cache_dir,
        progress_cb=progress_cb,
    )

    if progress_cb:
        progress_cb(0.90, "Arranging")
    arrangement = crew.arrange(
        clip_analyses=clip_analyses,
        song=song,
        kept_ranges_by_clip=kept_ranges_by_clip,
        recipe=req.style_recipe,
        seed=req.seed,
    )

    if progress_cb:
        progress_cb(1.0, "Plan ready")

    return PlanResult(
        arrangement=arrangement,
        song=song,
        kept_ranges_by_clip=kept_ranges_by_clip,
        clip_analyses=clip_analyses,
        warnings=warnings,
    )


# ---------- stage 1: auto-editor analyze ------------------------------------


def _analyze_all_clips(
    *,
    req: PlanRequest,
    out_dir: Path,
    warnings: list[str],
    progress_cb,
) -> dict[str, list[tuple[float, float]]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    kept: dict[str, list[tuple[float, float]]] = {}
    total = max(1, len(req.bin_clips))

    for i, clip in enumerate(req.bin_clips):
        if progress_cb:
            progress_cb(0.05 + 0.30 * (i / total), f"Scanning {Path(clip.source_path).name}")
        try:
            result = analyze_mod.analyze_clip(
                clip.source_path,
                output_dir=out_dir,
                mode=req.scan_mode,
                audio_threshold=req.audio_threshold,
                motion_threshold=req.motion_threshold,
                margin=req.margin,
            )
            kept[clip.clip_id] = [(r.start, r.end) for r in result.kept_ranges]
            if not result.kept_ranges:
                # Auto-editor decided everything was cut. Fall back to "keep whole clip".
                kept[clip.clip_id] = [(0.0, _safe_duration_seconds(clip.source_path))]
                warnings.append(f"{Path(clip.source_path).name}: no kept ranges from {req.scan_mode}; using full clip")
        except RuntimeError as e:
            # Common: clip has no audio stream + audio-mode requested.
            msg = str(e)
            warnings.append(f"{Path(clip.source_path).name}: {msg[:200]}")
            kept[clip.clip_id] = [(0.0, _safe_duration_seconds(clip.source_path))]
    return kept


def _safe_duration_seconds(path: str) -> float:
    """Probe via ffprobe; fall back to 60s if it fails."""
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 60.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return 60.0


# ---------- stage 3: vision (cached) ----------------------------------------


def _vision_analyze_all_clips(
    *,
    req: PlanRequest,
    crew: CrewInterface,
    cache_dir: Path,
    progress_cb,
    n_frames: int = 3,
) -> list[ClipAnalysis]:
    """Sample frames + call Art Director, caching by content hash."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: list[ClipAnalysis] = []
    total = max(1, len(req.bin_clips))

    for i, clip in enumerate(req.bin_clips):
        if progress_cb:
            progress_cb(0.60 + 0.28 * (i / total), f"Art Director: {Path(clip.source_path).name}")

        cache_file = cache_path_for(clip.source_path, cache_dir)
        cached = _read_cached_analysis(cache_file, clip)
        if cached is not None:
            out.append(cached)
            continue

        # Sample frames into a per-clip subdir so they're reusable / inspectable.
        clip_hash = hash_clip(clip.source_path)
        frames_dir = cache_dir / "frames" / clip_hash
        frames = sample_frames(clip.source_path, frames_dir, n_frames=n_frames)
        if not frames:
            logger.warning("no frames sampled for %s — using neutral defaults", clip.source_path)

        analysis = crew.analyze_clip(
            frames=[f.path for f in frames],
            clip_id=clip.clip_id,
            source_path=clip.source_path,
            recipe=req.style_recipe,
        )
        _write_cached_analysis(cache_file, analysis)
        out.append(analysis)
    return out


def _read_cached_analysis(cache_file: Path, clip: BinClip) -> Optional[ClipAnalysis]:
    if not cache_file.exists():
        return None
    try:
        d = json.loads(cache_file.read_text())
    except json.JSONDecodeError:
        return None
    return ClipAnalysis(
        clip_id=clip.clip_id,                  # cache file is hashed per-content; reuse caller's clip_id
        source_path=clip.source_path,
        subject=d.get("subject", "abstract"),
        energy=d.get("energy", "medium"),
        dominant_palette=d.get("dominant_palette", "neutral"),
        motion=d.get("motion", "medium"),
        mood=d.get("mood", "uplifting"),
        recommended_filter=d.get("recommended_filter", "none"),
        best_section_fit=list(d.get("best_section_fit", ["any"])),
        cached=True,
    )


def _write_cached_analysis(cache_file: Path, analysis: ClipAnalysis) -> None:
    cache_file.write_text(json.dumps(analysis.to_dict(), indent=2))
