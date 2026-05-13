"""Tests for the style recipe loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service.style_recipe_loader import (
    StyleRecipe,
    list_recipes,
    load_recipe,
)


def _write(d: Path, name: str, payload: dict) -> Path:
    p = d / name
    p.write_text(json.dumps(payload))
    return p


def test_list_recipes_reads_directory(tmp_path: Path):
    _write(tmp_path, "alpha.json", {"name": "Alpha", "description": "First"})
    _write(tmp_path, "beta.json", {"name": "Beta", "description": "Second"})
    recipes = list_recipes(tmp_path)
    names = sorted(r.name for r in recipes)
    assert names == ["Alpha", "Beta"]


def test_load_recipe_by_name_case_insensitive(tmp_path: Path):
    _write(tmp_path, "grunge.json", {
        "name": "Grunge",
        "description": "x",
        "filter_palette": ["oldfilm"],
    })
    r = load_recipe("GRUNGE", recipes_dir=tmp_path)
    assert r is not None
    assert r.name == "Grunge"
    assert r.filter_palette == ["oldfilm"]


def test_load_recipe_missing_returns_none(tmp_path: Path):
    assert load_recipe("nonexistent", recipes_dir=tmp_path) is None


def test_malformed_recipe_is_skipped_not_fatal(tmp_path: Path):
    _write(tmp_path, "good.json", {"name": "Good"})
    (tmp_path / "bad.json").write_text("{not valid json")
    recipes = list_recipes(tmp_path)
    assert [r.name for r in recipes] == ["Good"]


def test_recipe_missing_name_field_is_skipped(tmp_path: Path):
    _write(tmp_path, "good.json", {"name": "Good"})
    _write(tmp_path, "nameless.json", {"description": "no name"})
    recipes = list_recipes(tmp_path)
    assert [r.name for r in recipes] == ["Good"]


def test_recipe_has_bias_flag(tmp_path: Path):
    _write(tmp_path, "biased.json", {
        "name": "Biased",
        "filter_palette": ["sepia"],
    })
    _write(tmp_path, "neutral.json", {"name": "Neutral"})
    biased = load_recipe("biased", recipes_dir=tmp_path)
    neutral = load_recipe("neutral", recipes_dir=tmp_path)
    assert biased and biased.has_bias is True
    assert neutral and neutral.has_bias is False


def test_default_recipe_in_repo_loads(tmp_path: Path):
    """The repo's default.json must load — sanity check."""
    import os
    repo_root = Path(__file__).resolve().parents[3]
    recipes_dir = repo_root / "data" / "agent" / "style_recipes"
    if not recipes_dir.is_dir():
        pytest.skip("repo recipes dir not present")
    r = load_recipe("Default", recipes_dir=recipes_dir)
    assert r is not None
    assert r.name == "Default"
    assert r.has_bias is False  # default has no constraints
