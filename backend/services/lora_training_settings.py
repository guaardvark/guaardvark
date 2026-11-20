"""Normalize per-subject LoRA training hyperparameters."""
from __future__ import annotations

from typing import Any

# Used for a base that declares no train_recipe in media_model_registry.
DEFAULTS: dict[str, Any] = {
    "resolution": 512,
    "rank": 16,
    "alpha": 16,
    "learning_rate": 1.0e-4,
    "steps": None,  # computed from image count when None
    # base_model_id filled from media_model_registry default when missing
    "base_model_id": None,
    "lora_targets": "attention",
    "base_storage": "bf16",
}

LORA_TARGETS = ("attention", "attention_ffn")
BASE_STORAGE = ("bf16", "float8")

# What the Cast page sent for every subject before bases declared a recipe: its
# own form defaults, not values a person chose.
_PRE_RECIPE_FORM_DEFAULTS = {"resolution": (512, 768), "rank": (16,), "alpha": (16,)}


def _resolve_base(base_model_id: Any) -> str:
    try:
        from backend.services.media_model_registry import (
            get_cast_train_base_setting,
            get_profile,
        )
        if base_model_id:
            p = get_profile(str(base_model_id))
            return p["id"] if p else get_cast_train_base_setting()
        return get_cast_train_base_setting()
    except Exception:
        return base_model_id or "zimage-turbo"


def defaults_for_base(base_model_id: str | None) -> dict[str, Any]:
    """DEFAULTS with the base's declared recipe on top."""
    out = dict(DEFAULTS)
    try:
        from backend.services.media_model_registry import train_recipe
        recipe = train_recipe(base_model_id)
    except Exception:
        recipe = {}
    out.update({k: recipe[k] for k in out if k in recipe})
    return out


def normalize_training_settings(raw: dict | None) -> dict[str, Any]:
    given = {k: v for k, v in raw.items() if v is not None} if isinstance(raw, dict) else {}
    base_model_id = _resolve_base(given.get("base_model_id") or given.get("base_model"))
    src = defaults_for_base(base_model_id)
    src.update(given)
    resolution = int(src.get("resolution") or 512)
    resolution = max(512, (resolution // 64) * 64)
    rank = max(4, min(64, int(src.get("rank") or 16)))
    alpha = max(4, min(128, int(src.get("alpha") or rank)))
    lr = float(src.get("learning_rate") or 1.0e-4)
    steps = src.get("steps")
    steps = int(steps) if steps not in (None, "") else None
    defaults = defaults_for_base(base_model_id)
    lora_targets = str(src.get("lora_targets") or "").lower()
    if lora_targets not in LORA_TARGETS:
        lora_targets = defaults["lora_targets"]
    base_storage = str(src.get("base_storage") or "").lower()
    if base_storage not in BASE_STORAGE:
        base_storage = defaults["base_storage"]

    return {
        "resolution": resolution,
        "rank": rank,
        "alpha": alpha,
        "learning_rate": lr,
        "steps": steps,
        "base_model_id": base_model_id,
        "lora_targets": lora_targets,
        "base_storage": base_storage,
    }


def merge_training_settings(existing: dict | None, raw: dict | None) -> dict[str, Any]:
    """Return a subject's stored settings with the hyperparameters from ``raw``.

    ``Subject.training_settings_json`` holds more than hyperparameters: the
    identity state (bible_vision_grounded, bible_manual_override,
    bible_vision_tags, bible_identity_marks, class_token) and the post-train
    smoke_identity score live in the same object. A hyperparameter write
    replaces the keys normalize_training_settings returns, exactly as
    before, and leaves every other key as it was.
    """
    merged = dict(existing) if isinstance(existing, dict) else {}
    # The Cast form does not send these two; a stored choice survives its saves.
    kept = {k: merged[k] for k in ("lora_targets", "base_storage") if merged.get(k)}
    merged.update(normalize_training_settings({**kept, **(raw if isinstance(raw, dict) else {})}))
    return merged


def settings_for_subject(subject) -> dict[str, Any]:
    raw = getattr(subject, "training_settings_json", None) or {}
    raw = dict(raw) if isinstance(raw, dict) else {}
    if "lora_targets" not in raw and all(
        raw.get(k) in (None, *old) for k, old in _PRE_RECIPE_FORM_DEFAULTS.items()
    ):
        # Stored before recipes existed and never changed from the form's
        # defaults: the base's recipe applies, as it does for a new subject.
        for k in _PRE_RECIPE_FORM_DEFAULTS:
            raw.pop(k, None)
    return normalize_training_settings(raw)
