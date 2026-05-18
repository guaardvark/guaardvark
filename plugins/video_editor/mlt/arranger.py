"""Arranger — combines clip analysis + song structure + kept-ranges into an
ordered Arrangement that the MLT writer can render.

A1 implementation: section-by-section selection, biased by recipe filter
palette (so transitions/filters stay within the chosen aesthetic even before
A3 wires up real vision-model recommendations). Reproducible with a seed.

A3 will replace the random-from-eligible selection with a scoring function
over `ClipAnalysis.best_section_fit` and StyleRecipe biases.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Optional

from service.crew_interface import (
    ArrangedClip,
    Arrangement,
    ClipAnalysis,
    SongAnalysis,
)

logger = logging.getLogger(__name__)


def arrange_from_analysis(
    clip_analyses: list[ClipAnalysis],
    song: SongAnalysis,
    kept_ranges_by_clip: dict[str, list[tuple[float, float]]],
    recipe: Optional[dict[str, Any]] = None,
    seed: int = 0,
) -> Arrangement:
    """Section-by-section: pick a clip + kept range for each song section."""
    rng = random.Random(seed)

    eligible = _eligible_clip_ids(clip_analyses, kept_ranges_by_clip)
    if not eligible:
        return Arrangement(clips=[], style_recipe_name=_recipe_name(recipe), seed=seed)

    analysis_by_id = {a.clip_id: a for a in clip_analyses}

    sections = song.sections or _fallback_single_section(song)
    arranged: list[ArrangedClip] = []

    for i, section in enumerate(sections):
        section_label = _section_label(section)
        section_start = float(section["start"]) if isinstance(section, dict) else section.start
        section_end = float(section["end"]) if isinstance(section, dict) else section.end
        section_duration = section_end - section_start
        if section_duration <= 0:
            continue

        clip_id = _pick_clip_for_section(
            eligible_ids=eligible,
            analysis_by_id=analysis_by_id,
            kept_ranges=kept_ranges_by_clip,
            section_label=section_label,
            section_duration=section_duration,
            recipe=recipe,
            rng=rng,
        )
        if clip_id is None:
            continue

        analysis = analysis_by_id[clip_id]
        source_in, source_out = _pick_kept_range(
            kept_ranges_by_clip[clip_id], section_duration, rng
        )

        arranged.append(
            ArrangedClip(
                clip_id=clip_id,
                source_path=analysis.source_path,
                section_label=section_label,
                timeline_start=section_start,
                timeline_end=section_end,
                source_in=source_in,
                source_out=source_out,
                filter_preset=_resolve_filter(analysis, recipe),
                transition_to_next=_resolve_transition(i, sections, recipe, rng),
            )
        )

    return Arrangement(
        clips=arranged,
        style_recipe_name=_recipe_name(recipe),
        seed=seed,
    )


# ---------- helpers ---------------------------------------------------------


def _eligible_clip_ids(
    analyses: list[ClipAnalysis],
    kept: dict[str, list[tuple[float, float]]],
) -> list[str]:
    """Clips need (a) an analysis entry and (b) at least one kept range."""
    return [a.clip_id for a in analyses if kept.get(a.clip_id)]


def _pick_clip_for_section(
    *,
    eligible_ids: list[str],
    analysis_by_id: dict[str, ClipAnalysis],
    kept_ranges: dict[str, list[tuple[float, float]]],
    section_label: str,
    section_duration: float,
    recipe: Optional[dict[str, Any]],
    rng: random.Random,
) -> Optional[str]:
    """Score-then-pick. A1 score = 'has a kept range long enough' + recipe-bias bonus.
    A3 will add ClipAnalysis-based scoring (best_section_fit, energy match)."""
    long_enough = [
        cid for cid in eligible_ids
        if any((end - start) >= min(section_duration, 0.5) for start, end in kept_ranges[cid])
    ]
    pool = long_enough or eligible_ids[:]
    if not pool:
        return None

    # Recipe bias: in A1 we don't yet have rich ClipAnalysis fields, so this is
    # a no-op for now. In A3 we'll prefer clips whose analysis.best_section_fit
    # contains `section_label` and whose subject/energy match recipe.prefer_*.
    scored = [(_score_clip_for_section(analysis_by_id[cid], section_label, recipe), cid) for cid in pool]
    rng.shuffle(scored)  # randomize among ties
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def _score_clip_for_section(
    analysis: ClipAnalysis,
    section_label: str,
    recipe: Optional[dict[str, Any]],
) -> float:
    score = 0.0
    if section_label in analysis.best_section_fit or "any" in analysis.best_section_fit:
        score += 1.0
    if recipe:
        if analysis.subject in (recipe.get("prefer_subjects") or []):
            score += 0.5
        if analysis.energy in (recipe.get("prefer_energy") or []):
            score += 0.5
        if analysis.motion in (recipe.get("prefer_motion") or []):
            score += 0.5
    return score


def _pick_kept_range(
    ranges: list[tuple[float, float]],
    section_duration: float,
    rng: random.Random,
) -> tuple[float, float]:
    """Pick a kept range, then a start within it that leaves enough footage."""
    candidates = [(s, e) for s, e in ranges if (e - s) >= min(section_duration, 0.5)]
    pool = candidates or ranges
    start, end = rng.choice(pool)
    duration_available = end - start
    if duration_available <= section_duration:
        return (start, start + duration_available)
    # Slide the section_duration window randomly inside this kept range.
    max_offset = duration_available - section_duration
    offset = rng.uniform(0.0, max_offset)
    return (start + offset, start + offset + section_duration)


def _resolve_filter(
    analysis: ClipAnalysis,
    recipe: Optional[dict[str, Any]],
) -> str:
    """A1: respect recipe.filter_palette if present; otherwise use clip's recommended."""
    candidate = analysis.recommended_filter or "none"
    if recipe:
        palette = recipe.get("filter_palette") or []
        if palette and candidate not in palette and candidate != "none":
            # Out-of-palette recommendation — fall back to first palette entry.
            return palette[0]
    return candidate


def _resolve_transition(
    section_index: int,
    sections: list[Any],
    recipe: Optional[dict[str, Any]],
    rng: random.Random,
) -> str:
    """A1: hard-cut. A3 may pick based on adjacent-section energy delta."""
    if section_index >= len(sections) - 1:
        return "hard-cut"  # last clip has no following transition
    if recipe and recipe.get("transition_palette"):
        return rng.choice(recipe["transition_palette"])
    return "hard-cut"


def _section_label(section: Any) -> str:
    if isinstance(section, dict):
        return str(section.get("label", "unlabeled"))
    return getattr(section, "label", "unlabeled")


def _fallback_single_section(song: SongAnalysis) -> list[dict[str, Any]]:
    """If the song analysis didn't produce sections, treat the whole song as one."""
    return [{"label": "drop", "start": 0.0, "end": song.duration_seconds}]


def _recipe_name(recipe: Optional[dict[str, Any]]) -> str:
    if recipe and "name" in recipe:
        return str(recipe["name"])
    return "default"
