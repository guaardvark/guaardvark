"""Art Director tests — JSON parsing + normalization, no live LLM calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service import art_director
from mlt.filters import PRESETS as FILTER_PRESETS

ALL_FILTERS = list(FILTER_PRESETS.keys())


def test_build_prompt_includes_n_frames_and_all_enums():
    p = art_director.build_prompt(3, ALL_FILTERS)
    assert "3 frames" in p
    assert "sepia" in p          # filter slug appears
    assert "intro" in p          # section enum
    assert "warm" in p           # palette enum
    assert "calm" in p           # energy enum


def test_parse_response_clean_json():
    raw = '{"subject": "wide-landscape", "energy": "high"}'
    parsed = art_director._parse_response_json(raw)
    assert parsed == {"subject": "wide-landscape", "energy": "high"}


def test_parse_response_strips_markdown_fence():
    raw = '```json\n{"subject": "crowd"}\n```'
    parsed = art_director._parse_response_json(raw)
    assert parsed == {"subject": "crowd"}


def test_parse_response_extracts_first_json_block():
    raw = 'Sure! Here is the JSON: {"subject": "abstract", "energy": "calm"} Thanks!'
    parsed = art_director._parse_response_json(raw)
    assert parsed == {"subject": "abstract", "energy": "calm"}


def test_parse_response_empty():
    assert art_director._parse_response_json("") is None
    assert art_director._parse_response_json("no json here") is None


def test_normalize_clamps_unknown_subject_to_abstract():
    out = art_director._normalize({"subject": "potatoes"}, ALL_FILTERS)
    assert out["subject"] == "abstract"


def test_normalize_clamps_unknown_recommended_filter_to_none():
    out = art_director._normalize({"recommended_filter": "nonsense"}, ALL_FILTERS)
    assert out["recommended_filter"] == "none"


def test_normalize_respects_palette_constraint():
    palette = ["sepia", "oldfilm"]
    out = art_director._normalize({"recommended_filter": "vertigo"}, palette)
    # vertigo isn't in the palette → fall back to none
    assert out["recommended_filter"] == "none"
    out_in_palette = art_director._normalize({"recommended_filter": "sepia"}, palette)
    assert out_in_palette["recommended_filter"] == "sepia"


def test_normalize_section_fit_filters_unknown_values():
    out = art_director._normalize({"best_section_fit": ["intro", "lunchbreak", "drop"]}, ALL_FILTERS)
    assert out["best_section_fit"] == ["intro", "drop"]


def test_normalize_section_fit_default_when_empty():
    out = art_director._normalize({"best_section_fit": []}, ALL_FILTERS)
    assert out["best_section_fit"] == ["any"]


def test_normalize_full_well_formed_response():
    parsed = {
        "subject": "character-closeup",
        "energy": "high",
        "dominant_palette": "warm",
        "motion": "fast",
        "mood": "aggressive",
        "recommended_filter": "high-contrast-bw",
        "best_section_fit": ["drop", "build"],
    }
    out = art_director._normalize(parsed, ALL_FILTERS)
    assert out == parsed


def test_analyze_frames_empty_returns_defaults():
    out = art_director.analyze_frames([], available_filter_slugs=ALL_FILTERS)
    assert out["subject"] == "abstract"
    assert out["best_section_fit"] == ["any"]


def test_analyze_frames_unreadable_file_returns_defaults(tmp_path: Path):
    bad = tmp_path / "missing.jpg"
    out = art_director.analyze_frames([bad], available_filter_slugs=ALL_FILTERS)
    assert out["subject"] == "abstract"


def test_neutral_defaults_shape():
    d = art_director._neutral_defaults()
    for key in ("subject", "energy", "dominant_palette", "motion", "mood", "recommended_filter", "best_section_fit"):
        assert key in d
