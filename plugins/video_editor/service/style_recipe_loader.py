"""Style Recipe loader — reads data/agent/style_recipes/*.json.

Recipes are the creative brief that biases the Art Director ('Grunge', 'Dark',
'Goth', etc.). v1 ships with only `default.json` (no bias, full catalog).
Hand-authored recipes come in a separate curation pass; eventually
Captain Recipe McRecipieface generates them.

Same conceptual layer as data/agent/recipes.json (servo recipes), different
domain. Loader is intentionally tolerant: missing fields fall back to safe
defaults, unknown filter slugs are passed through to the arranger which logs
and ignores them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_RECIPES_DIRNAME = Path("data/agent/style_recipes")


@dataclass
class StyleRecipe:
    name: str
    description: str = ""
    prefer_subjects: list[str] = field(default_factory=list)
    prefer_energy: list[str] = field(default_factory=list)
    prefer_motion: list[str] = field(default_factory=list)
    filter_palette: list[str] = field(default_factory=list)        # empty = use full catalog
    transition_palette: list[str] = field(default_factory=list)    # empty = use full catalog
    global_filter: Optional[str] = None
    audio_treatment: str = "as-is"

    @property
    def has_bias(self) -> bool:
        return bool(
            self.prefer_subjects or self.prefer_energy or self.prefer_motion
            or self.filter_palette or self.transition_palette or self.global_filter
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "prefer_subjects": self.prefer_subjects,
            "prefer_energy": self.prefer_energy,
            "prefer_motion": self.prefer_motion,
            "filter_palette": self.filter_palette,
            "transition_palette": self.transition_palette,
            "global_filter": self.global_filter,
            "audio_treatment": self.audio_treatment,
        }


def list_recipes(recipes_dir: Optional[str | Path] = None) -> list[StyleRecipe]:
    """Read every .json under data/agent/style_recipes/ as a StyleRecipe."""
    d = _resolve_dir(recipes_dir)
    if not d.is_dir():
        return []
    out: list[StyleRecipe] = []
    for path in sorted(d.glob("*.json")):
        try:
            out.append(_recipe_from_dict(json.loads(path.read_text())))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("skipping malformed recipe %s: %s", path.name, e)
    return out


def load_recipe(
    name: str, recipes_dir: Optional[str | Path] = None
) -> Optional[StyleRecipe]:
    """Look up a recipe by `name` (case-insensitive on filename stem)."""
    target = name.strip().lower()
    for r in list_recipes(recipes_dir):
        if r.name.lower() == target:
            return r
    # Filename-stem fallback (Grunge.json → "Grunge")
    d = _resolve_dir(recipes_dir)
    candidate = d / f"{target}.json"
    if candidate.is_file():
        try:
            return _recipe_from_dict(json.loads(candidate.read_text()))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("recipe %s exists but failed to parse: %s", candidate, e)
    return None


def _resolve_dir(recipes_dir: Optional[str | Path]) -> Path:
    if recipes_dir is not None:
        return Path(recipes_dir)
    import os
    root = Path(os.environ.get("GUAARDVARK_ROOT", ".")).resolve()
    return root / _DEFAULT_RECIPES_DIRNAME


def _recipe_from_dict(d: dict[str, Any]) -> StyleRecipe:
    if "name" not in d:
        raise KeyError("recipe missing required 'name' field")
    bias = d.get("art_director_bias") or {}
    return StyleRecipe(
        name=str(d["name"]),
        description=str(d.get("description", "")),
        prefer_subjects=list(bias.get("prefer_subjects", [])),
        prefer_energy=list(bias.get("prefer_energy", [])),
        prefer_motion=list(bias.get("prefer_motion", [])),
        filter_palette=list(d.get("filter_palette", [])),
        transition_palette=list(d.get("transition_palette", [])),
        global_filter=d.get("global_filter") or None,
        audio_treatment=str(d.get("audio_treatment", "as-is")),
    )
