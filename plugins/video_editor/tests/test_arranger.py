"""Tests for the arranger — section-by-section clip selection."""

from __future__ import annotations

import pytest

from mlt.arranger import arrange_from_analysis
from service.crew_interface import ClipAnalysis, SongAnalysis


def _song(duration: float = 12.0) -> SongAnalysis:
    return SongAnalysis(
        tempo_bpm=120.0,
        duration_seconds=duration,
        beat_times=[i * 0.5 for i in range(int(duration / 0.5))],
        sections=[
            {"label": "intro", "start": 0.0, "end": 3.0},
            {"label": "build", "start": 3.0, "end": 6.0},
            {"label": "drop", "start": 6.0, "end": 9.0},
            {"label": "outro", "start": 9.0, "end": 12.0},
        ],
    )


def _analysis(clip_id: str, **fields) -> ClipAnalysis:
    return ClipAnalysis(clip_id=clip_id, source_path=f"/x/{clip_id}.mp4", **fields)


def test_arrange_produces_one_clip_per_section():
    analyses = [_analysis(f"c{i}") for i in range(4)]
    kept = {f"c{i}": [(0.0, 60.0)] for i in range(4)}
    arr = arrange_from_analysis(analyses, _song(), kept, seed=1)
    assert len(arr.clips) == 4
    section_labels = [c.section_label for c in arr.clips]
    assert section_labels == ["intro", "build", "drop", "outro"]


def test_arrange_skips_clips_with_no_kept_ranges():
    analyses = [_analysis("c0"), _analysis("c1_empty")]
    kept = {"c0": [(0.0, 60.0)]}  # c1_empty has no entry
    arr = arrange_from_analysis(analyses, _song(), kept, seed=1)
    assert all(c.clip_id == "c0" for c in arr.clips)


def test_arrange_returns_empty_when_no_eligible_clips():
    analyses = [_analysis("c0")]
    kept = {}  # No kept ranges at all
    arr = arrange_from_analysis(analyses, _song(), kept, seed=1)
    assert arr.clips == []


def test_arrange_is_reproducible_with_seed():
    analyses = [_analysis(f"c{i}") for i in range(4)]
    kept = {f"c{i}": [(0.0, 60.0)] for i in range(4)}
    a1 = arrange_from_analysis(analyses, _song(), kept, seed=42)
    a2 = arrange_from_analysis(analyses, _song(), kept, seed=42)
    assert [c.clip_id for c in a1.clips] == [c.clip_id for c in a2.clips]


def test_arrange_respects_kept_range_bounds():
    """source_in / source_out must fall within the picked kept range."""
    analyses = [_analysis("c0")]
    kept = {"c0": [(5.0, 10.0)]}  # only 5-10s of source available
    arr = arrange_from_analysis(analyses, _song(duration=2.0), kept, seed=1)
    assert len(arr.clips) > 0
    for c in arr.clips:
        assert c.source_in >= 5.0
        assert c.source_out <= 10.001  # tiny float slop


def test_recipe_filter_palette_constrains_clip_filter():
    """A clip whose recommended_filter isn't in the palette falls back to palette[0]."""
    analyses = [_analysis("c0", recommended_filter="sepia")]
    kept = {"c0": [(0.0, 60.0)]}
    recipe = {"name": "Grunge", "filter_palette": ["oldfilm", "high-contrast-bw"]}
    arr = arrange_from_analysis(analyses, _song(), kept, recipe=recipe, seed=1)
    assert all(c.filter_preset == "oldfilm" for c in arr.clips)


def test_recipe_filter_palette_keeps_in_palette_recommendation():
    analyses = [_analysis("c0", recommended_filter="oldfilm")]
    kept = {"c0": [(0.0, 60.0)]}
    recipe = {"name": "Grunge", "filter_palette": ["oldfilm", "high-contrast-bw"]}
    arr = arrange_from_analysis(analyses, _song(), kept, recipe=recipe, seed=1)
    assert all(c.filter_preset == "oldfilm" for c in arr.clips)


def test_no_recipe_means_no_filter_constraint():
    analyses = [_analysis("c0", recommended_filter="sepia")]
    kept = {"c0": [(0.0, 60.0)]}
    arr = arrange_from_analysis(analyses, _song(), kept, recipe=None, seed=1)
    assert all(c.filter_preset == "sepia" for c in arr.clips)
