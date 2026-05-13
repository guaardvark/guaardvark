"""Director's Notes overrides — applied after vision, before arranging."""

from __future__ import annotations

from service.crew_interface import ClipAnalysis
from service.jobs_pipeline import _apply_overrides


def _a(clip_id: str, **fields) -> ClipAnalysis:
    return ClipAnalysis(clip_id=clip_id, source_path=f"/x/{clip_id}.mp4", **fields)


def test_no_overrides_leaves_analyses_unchanged():
    analyses = [_a("c0", subject="abstract", energy="medium")]
    _apply_overrides(analyses, {})
    assert analyses[0].subject == "abstract"
    assert analyses[0].energy == "medium"


def test_single_field_override_applied():
    analyses = [_a("c0", subject="abstract")]
    _apply_overrides(analyses, {"c0": {"subject": "wide-landscape"}})
    assert analyses[0].subject == "wide-landscape"


def test_multiple_fields_override_applied():
    analyses = [_a("c0", energy="medium", mood="uplifting")]
    _apply_overrides(analyses, {"c0": {"energy": "frenetic", "mood": "aggressive"}})
    assert analyses[0].energy == "frenetic"
    assert analyses[0].mood == "aggressive"


def test_override_unknown_field_ignored():
    """Patch with arbitrary keys shouldn't add attributes to ClipAnalysis."""
    analyses = [_a("c0", subject="abstract")]
    _apply_overrides(analyses, {"c0": {"subject": "crowd", "rogue_field": "x"}})
    assert analyses[0].subject == "crowd"
    assert not hasattr(analyses[0], "rogue_field") or getattr(analyses[0], "rogue_field", None) != "x"


def test_override_for_missing_clip_id_ignored():
    analyses = [_a("c0", subject="abstract")]
    _apply_overrides(analyses, {"c99": {"subject": "crowd"}})
    assert analyses[0].subject == "abstract"


def test_override_non_dict_patch_ignored():
    analyses = [_a("c0", subject="abstract")]
    _apply_overrides(analyses, {"c0": "not-a-dict"})
    assert analyses[0].subject == "abstract"


def test_override_best_section_fit():
    analyses = [_a("c0", best_section_fit=["any"])]
    _apply_overrides(analyses, {"c0": {"best_section_fit": ["intro", "outro"]}})
    assert analyses[0].best_section_fit == ["intro", "outro"]


def test_override_recommended_filter():
    analyses = [_a("c0", recommended_filter="none")]
    _apply_overrides(analyses, {"c0": {"recommended_filter": "sepia"}})
    assert analyses[0].recommended_filter == "sepia"
