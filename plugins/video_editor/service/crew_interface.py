"""CrewInterface — the contract between the editor's pipeline and the creative AI.

v1 implementation (LocalArtDirector) lives in this plugin and talks directly to
Ollama for qwen3-vl. v2 will live in a separate `plugins/film_crew/` service;
the editor swaps `LocalArtDirector` for `FilmCrewClient` and nothing else changes.

This indirection is the user's explicit roadmap. Don't fold it into the call
sites — keep all qwen3-vl / Gemma4 specifics behind this Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol


# ---------- shared data shapes (input + output of the Crew) -----------------


@dataclass
class ClipAnalysis:
    """What the Art Director said about one clip after looking at sampled frames."""

    clip_id: str
    subject: str = "abstract"
    energy: str = "medium"
    dominant_palette: str = "neutral"
    motion: str = "medium"
    mood: str = "uplifting"
    recommended_filter: str = "none"
    best_section_fit: list[str] = field(default_factory=lambda: ["any"])
    source_path: str = ""
    cached: bool = False  # True when read from cache rather than freshly inferred

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "subject": self.subject,
            "energy": self.energy,
            "dominant_palette": self.dominant_palette,
            "motion": self.motion,
            "mood": self.mood,
            "recommended_filter": self.recommended_filter,
            "best_section_fit": self.best_section_fit,
            "source_path": self.source_path,
            "cached": self.cached,
        }


@dataclass
class SongAnalysis:
    """What the audio analyzer said about the master soundtrack."""

    tempo_bpm: float
    duration_seconds: float
    beat_times: list[float] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)  # [{label, start, end}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tempo_bpm": self.tempo_bpm,
            "duration_seconds": self.duration_seconds,
            "beat_times": self.beat_times,
            "sections": self.sections,
        }


@dataclass
class ArrangedClip:
    """One slot in the final arrangement: which clip plays from when to when."""

    clip_id: str
    source_path: str
    section_label: str
    timeline_start: float       # seconds in the final video
    timeline_end: float
    source_in: float            # seconds within the source clip
    source_out: float
    filter_preset: str = "none"
    transition_to_next: str = "hard-cut"


@dataclass
class Arrangement:
    clips: list[ArrangedClip]
    style_recipe_name: str = "default"
    seed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "style_recipe_name": self.style_recipe_name,
            "seed": self.seed,
            "clips": [
                {
                    "clip_id": c.clip_id,
                    "source_path": c.source_path,
                    "section_label": c.section_label,
                    "timeline_start": c.timeline_start,
                    "timeline_end": c.timeline_end,
                    "source_in": c.source_in,
                    "source_out": c.source_out,
                    "filter_preset": c.filter_preset,
                    "transition_to_next": c.transition_to_next,
                }
                for c in self.clips
            ],
        }


# ---------- the Protocol every Crew implementation must satisfy ------------


class CrewInterface(Protocol):
    """The contract. v1 = LocalArtDirector. v2 = FilmCrewClient. Same shape."""

    def analyze_clip(
        self,
        frames: list[Path],
        clip_id: str,
        source_path: str,
        recipe: Optional[dict[str, Any]] = None,
    ) -> ClipAnalysis:
        """Return per-clip creative analysis. May be cached upstream."""
        ...

    def arrange(
        self,
        clip_analyses: list[ClipAnalysis],
        song: SongAnalysis,
        kept_ranges_by_clip: dict[str, list[tuple[float, float]]],
        recipe: Optional[dict[str, Any]] = None,
        seed: int = 0,
    ) -> Arrangement:
        """Combine vision + audio + kept-ranges (+ optional style recipe) → arrangement."""
        ...


# ---------- v1 LocalArtDirector — minimal scaffold (real impl lands in A3) -


class LocalArtDirector:
    """v1 — runs in-process. Vision call wires up in A3; A1 returns neutral defaults.

    The scaffold exists now so call sites (jobs_pipeline.py, app.py) can be
    written against the final shape. Replacing the analyze_clip body with the
    real qwen3-vl call in A3 won't touch anything upstream.
    """

    def __init__(self, ollama_url: str = "http://localhost:11434") -> None:
        self.ollama_url = ollama_url

    def analyze_clip(
        self,
        frames: list[Path],
        clip_id: str,
        source_path: str,
        recipe: Optional[dict[str, Any]] = None,
    ) -> ClipAnalysis:
        # A1: return neutral. A3: real qwen3-vl call.
        return ClipAnalysis(
            clip_id=clip_id,
            source_path=source_path,
            subject="abstract",
            energy="medium",
            dominant_palette="neutral",
            motion="medium",
            mood="uplifting",
            recommended_filter="none",
            best_section_fit=["any"],
        )

    def arrange(
        self,
        clip_analyses: list[ClipAnalysis],
        song: SongAnalysis,
        kept_ranges_by_clip: dict[str, list[tuple[float, float]]],
        recipe: Optional[dict[str, Any]] = None,
        seed: int = 0,
    ) -> Arrangement:
        # Concrete arrangement logic lives in mlt/arranger.py so it can be
        # tested in isolation; this just delegates.
        from mlt.arranger import arrange_from_analysis

        return arrange_from_analysis(
            clip_analyses=clip_analyses,
            song=song,
            kept_ranges_by_clip=kept_ranges_by_clip,
            recipe=recipe,
            seed=seed,
        )


# ---------- v2 placeholder — exists so the swap point is visible -----------


class FilmCrewClient:
    """Stub for the future plugins/film_crew/ HTTP client.

    Don't import this from anywhere yet — it's here so the swap point is
    visible in code review when film_crew lands.
    """

    def __init__(self, base_url: str = "http://localhost:8211") -> None:
        self.base_url = base_url

    def analyze_clip(self, *args: Any, **kwargs: Any) -> ClipAnalysis:  # noqa: D401
        raise NotImplementedError("FilmCrewClient lands when plugins/film_crew/ exists")

    def arrange(self, *args: Any, **kwargs: Any) -> Arrangement:  # noqa: D401
        raise NotImplementedError("FilmCrewClient lands when plugins/film_crew/ exists")
