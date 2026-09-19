"""Shared family defaults for still image generation (chat / CLI / batch).

Callers pass model id (or \"auto\"); when width/height/steps/guidance are None
or left at legacy SD placeholders, resolve from this table so all surfaces
start with the same canvas and sampling policy.

Keep in sync with OfflineImageGenerator._apply_family_sampling — that method
still corrects mid-flight; this module is the *caller-side* source of truth
so UI/API defaults are not SD-era 20/7.5/512 for modern models.
"""
from __future__ import annotations

from typing import Any


# Family sampling + canvas (PoA image gen unification §4).
# Z-Image Turbo: official HF recipe is num_inference_steps=9 (→ 8 DiT forwards),
# guidance_scale=0.0 (CFG distilled out).
#
# prompt_style says what kind of text the family's encoder was trained on, and
# therefore what the product may add to a person's prompt:
#   "tags"    CLIP-era models (SD 1.5 / SDXL). Comma-separated quality and
#             anatomy tags are how these were captioned, so appending them helps.
#   "natural" LLM text encoders (Z-Image: Qwen3). Trained on prose captions; the
#             model card asks for long natural-language descriptions and states
#             that negative prompts have no effect (CFG-distilled). Tag suffixes
#             are read as scene content: measured 2026-09-07 on this box, "full
#             body shot, realistic stance, correct anatomy, ..." appended to
#             "A man and woman watching a movie on a couch" produced posed,
#             camera-facing figures with tangled legs on 4/4 seeds, while the
#             same seeds with the bare sentence or a prose rewrite were clean.
#             Natural families get an LLM rewrite (media_director) or nothing.
# Krea 2 also uses an LLM encoder (Qwen3-VL per its model_index) but has not
# been A/B'd here yet, so it keeps "tags" until it is.
_FAMILY_DEFAULTS: dict[str, dict[str, Any]] = {
    # min_steps: the no-bad-knob floor for steps a default or an agent chose. None
    # until measured; record the comparison beside the number when it is set.
    # Z-Image Turbo 2: measured 2026-09-13 on this box at 1024x1024, seed 1984,
    # verbatim prompts, three scenes (two people on a bench, a paper boat in neon
    # rain, a bicycle under a street sign) at 1-9 steps. 1 step is visibly broken
    # (grain over everything, blank faces, smeared newsprint, noisy wheels); 2 is
    # clean and only slightly softer than 3-9; 9 adds fine texture and legible
    # signage.
    "zimage": {"min_steps": 2, "width": 1024, "height": 1024, "steps": 9, "guidance": 0.0, "prompt_style": "natural"},
    "comfyui": {"width": 1024, "height": 1024, "steps": 9, "guidance": 0.0, "prompt_style": "natural"},
    "krea2-turbo": {"width": 1024, "height": 1024, "steps": 8, "guidance": 0.0, "prompt_style": "tags"},
    "krea2-raw": {"width": 1024, "height": 1024, "steps": 52, "guidance": 3.5, "prompt_style": "tags"},
    "sdxl": {"width": 1024, "height": 1024, "steps": 25, "guidance": 7.0, "prompt_style": "tags"},
    "sd": {"width": 512, "height": 512, "steps": 20, "guidance": 7.5, "prompt_style": "tags"},
    "flux": {"width": 1024, "height": 1024, "steps": 28, "guidance": 3.5, "prompt_style": "tags"},
}

# Generator-side family names (OfflineImageGenerator._model_family) that do not
# carry the turbo/raw split used above.
_GENERATOR_FAMILY_ALIASES = {"krea2": "krea2-turbo"}


def prompt_style_for_family(family: str | None) -> str:
    """'natural' or 'tags' for a stills family name from either naming scheme."""
    fam = (family or "").strip().lower()
    fam = _GENERATOR_FAMILY_ALIASES.get(fam, fam)
    entry = _FAMILY_DEFAULTS.get(fam)
    if not entry:
        return "tags"
    return str(entry.get("prompt_style", "tags"))


def prompt_style(model: str | None = "auto") -> str:
    """'natural' or 'tags' for a catalog key / HF id / auto."""
    return prompt_style_for_family(model_family(model))

# When callers still ship classic SD-era "unset" markers, treat as None so
# family defaults win for modern models.
_LEGACY_SIZE = 512
_LEGACY_STEPS = 20
_LEGACY_GUIDANCE = 7.5


def model_family(model: str | None) -> str:
    """Map catalog key / HF id / auto to a sampling family key."""
    mid = (model or "").strip().lower()
    if mid == "comfyui":
        return "comfyui"
    if not mid or mid == "auto":
        # Product daily driver family for unresolved auto.
        try:
            from backend.services.media_model_registry import resolve_stills_model
            mid = (resolve_stills_model("auto") or "zimage-turbo").strip().lower()
        except Exception:
            mid = "zimage-turbo"
    if "flux" in mid:
        return "flux"
    if "krea" in mid:
        if "raw" in mid:
            return "krea2-raw"
        return "krea2-turbo"
    if "z-image" in mid or "zimage" in mid:
        return "zimage"
    if "xl" in mid or "sdxl" in mid or mid in ("sd-xl", "juggernaut-xl", "realvisxl"):
        return "sdxl"
    return "sd"


def resolve_stills_defaults(
    model: str | None = "auto",
    *,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    steps_explicit: bool = False,
    guidance: float | None = None,
    replace_legacy_sd_markers: bool = True,
) -> dict[str, Any]:
    """Return family defaults and sampling provenance, applying any measured floor.

    Explicit non-None values are kept unless ``replace_legacy_sd_markers`` is
    True and the value is the classic 512/20/7.5 placeholder (then family wins
    for modern families). ``steps_explicit`` preserves typed steps, including
    legacy markers, and bypasses the floor.
    """
    family = model_family(model)
    base = dict(_FAMILY_DEFAULTS.get(family) or _FAMILY_DEFAULTS["sd"])

    # Classic "unset" form: all three SD-era placeholders together. Intentional
    # draft sizes (e.g. 512² with Turbo steps/CFG) must NOT be rewritten.
    full_legacy_unset = (
        replace_legacy_sd_markers
        and family != "sd"
        and width is not None
        and height is not None
        and int(width) == _LEGACY_SIZE
        and int(height) == _LEGACY_SIZE
        and steps is not None
        and int(steps) == _LEGACY_STEPS
        and guidance is not None
        and abs(float(guidance) - _LEGACY_GUIDANCE) < 1e-6
    )

    def _pick_size(val: int | None, key: str) -> int:
        if val is None:
            return int(base[key])
        if full_legacy_unset and int(val) == _LEGACY_SIZE:
            return int(base[key])
        return int(val)

    # Steps and guidance follow the same rule as size: only the complete
    # 512/20/7.5 triple is an "unset" marker. A lone 20 steps (SDXL Fast preset)
    # or 7.5 guidance (SDXL High) is a value somebody chose and must survive.
    def _pick_steps(val: int | None) -> int:
        if val is None or int(val) <= 0:
            return int(base["steps"])
        if full_legacy_unset and not steps_explicit and int(val) == _LEGACY_STEPS:
            return int(base["steps"])
        return int(val)

    def _pick_guidance(val: float | None) -> float:
        if val is None:
            return float(base["guidance"])
        if full_legacy_unset and abs(float(val) - _LEGACY_GUIDANCE) < 1e-6:
            return float(base["guidance"])
        return float(val)

    resolved_steps = _pick_steps(steps)
    floor = base.get("min_steps")
    floor = int(floor) if floor is not None else None
    notice = None
    if floor is not None and resolved_steps < floor and not steps_explicit:
        label = {
            "zimage": "Z-Image Turbo", "krea2-turbo": "Krea 2 Turbo",
            "krea2-raw": "Krea 2 Raw", "sdxl": "SDXL",
            "sd": "Stable Diffusion", "flux": "FLUX",
        }[family]
        notice = f"{label} needs at least {floor} steps; raised {resolved_steps} to {floor}."
        resolved_steps = floor

    resolved_model = (model or "auto").strip() or "auto"
    return {
        "model": resolved_model,
        "family": family,
        "width": _pick_size(width, "width"),
        "height": _pick_size(height, "height"),
        "steps": resolved_steps,
        "steps_requested": steps,
        "steps_floor": floor,
        "steps_notice": notice,
        "guidance": _pick_guidance(guidance),
    }


def family_quality_presets(model: str | None = "auto") -> list[dict[str, Any]]:
    """UI quality presets keyed by family (not universal SD 15/20/30)."""
    family = model_family(model)
    if family == "zimage":
        return [
            {"value": "fast", "label": "Fast", "steps": 6, "guidance": 0.0},
            {"value": "standard", "label": "Standard", "steps": 9, "guidance": 0.0},
            {"value": "high", "label": "High Quality", "steps": 9, "guidance": 0.0},
        ]
    if family == "krea2-turbo":
        return [
            {"value": "fast", "label": "Fast", "steps": 6, "guidance": 0.0},
            {"value": "standard", "label": "Standard", "steps": 8, "guidance": 0.0},
            {"value": "high", "label": "High Quality", "steps": 12, "guidance": 0.0},
        ]
    if family == "krea2-raw":
        return [
            {"value": "standard", "label": "Standard", "steps": 40, "guidance": 3.5},
            {"value": "high", "label": "High Quality", "steps": 52, "guidance": 3.5},
            {"value": "ultra", "label": "Ultra", "steps": 60, "guidance": 3.5},
        ]
    if family == "flux":
        return [
            {"value": "flux-fast", "label": "FLUX Fast", "steps": 16, "guidance": 3.0},
            {"value": "flux-quality", "label": "FLUX Quality", "steps": 28, "guidance": 3.5},
            {"value": "flux-ultra", "label": "FLUX Ultra", "steps": 40, "guidance": 4.0},
        ]
    if family == "sdxl":
        return [
            {"value": "fast", "label": "Fast", "steps": 20, "guidance": 6.0},
            {"value": "standard", "label": "Standard", "steps": 25, "guidance": 7.0},
            {"value": "high", "label": "High Quality", "steps": 35, "guidance": 7.5},
        ]
    return [
        {"value": "fast", "label": "Fast", "steps": 15, "guidance": 7.0},
        {"value": "standard", "label": "Standard", "steps": 20, "guidance": 7.5},
        {"value": "high", "label": "High Quality", "steps": 30, "guidance": 8.0},
    ]
