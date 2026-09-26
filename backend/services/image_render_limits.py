"""The one place a still-image request is held to its model's limits.

The numbers live in media_model_registry (IMAGE_FAMILY_SPECS, IMAGE_MODEL_LIMITS);
this module only reads them. A model id is a stills catalog key
("zimage-turbo", "krea2-raw", "sd-xl", ...), an HF repo id, a Comfy tag
("flux-dev", "flux-schnell") or a family name; each resolves to one family row
and, when it has one, its own row on top.

Two paths render differently from what the rows declare, and keep doing so
until ``GUAARDVARK_IMAGE_STRICT_LIMITS=1`` asks for the declared values:

- FLUX stills sent to ComfyUI (stills_pipeline, Cast stills) go out at the size
  asked, with no canvas clamp; strict clamps them like every other family.
- A Cast still rendered in ComfyUI starts from Z-Image's defaults (a FLUX
  route's offline key falls back to "zimage-turbo": 9 steps, guidance 0) and is
  sent no guidance, so the graph uses its own 7.0 (FluxGuidance 7.0 on
  FLUX-dev, whose value is 3.5); strict starts from the ComfyUI model's own
  defaults (FLUX-dev 28 steps / 3.5) and sends that guidance.
- sdxl-turbo, realistic-vision and epic-realism start from their family's
  steps and guidance (SDXL 25/7.0, SD 20/7.5); strict starts from their own.
"""
from __future__ import annotations

import logging
import os
from typing import Any, List, Optional, Tuple

from backend.services.media_model_registry import IMAGE_FAMILY_SPECS, IMAGE_MODEL_LIMITS

logger = logging.getLogger(__name__)

STRICT_LIMITS_ENV = "GUAARDVARK_IMAGE_STRICT_LIMITS"


def strict_limits_enabled() -> bool:
    return (os.environ.get(STRICT_LIMITS_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


# ── Which row ────────────────────────────────────────────────────────────────

def family_of(model: Optional[str]) -> str:
    """The family row for a catalog key, HF id, Comfy tag or family name."""
    key = (model or "").strip().lower()
    if key in IMAGE_MODEL_LIMITS:
        return IMAGE_MODEL_LIMITS[key]["family"]
    if not key:
        return "sd"
    if key in ("zimage", "zimage-turbo", "z-image-turbo") or "zimage" in key or "z-image" in key:
        return "zimage"
    if key.startswith("krea") or "krea2" in key or "krea-2" in key:
        return "krea2"
    if key.startswith("flux") or "flux" in key:
        return "flux"
    if "xl" in key or "sdxl" in key or key in ("sd-xl", "sdxl-turbo", "sdxl-legacy"):
        return "sdxl"
    return "sd"


def model_key(model: Optional[str]) -> Optional[str]:
    """The IMAGE_MODEL_LIMITS row a model reads, or None for family-only ids."""
    key = (model or "").strip().lower()
    if key in IMAGE_MODEL_LIMITS:
        return key
    if "krea" in key:
        return "krea2-raw" if "raw" in key else "krea2-turbo"
    if family_of(key) == "zimage":
        # Every Z-Image build (HF id, user entry) samples like the Turbo catalog row.
        return "zimage-turbo"
    return None


def limits_for(model: Optional[str]) -> dict:
    """The family row with the model's own row on top, plus ``family``."""
    family = family_of(model)
    limits = dict(IMAGE_FAMILY_SPECS.get(family) or IMAGE_FAMILY_SPECS["sd"])
    row = IMAGE_MODEL_LIMITS.get(model_key(model) or "")
    if row:
        limits.update({k: v for k, v in row.items() if k != "family"})
    limits["family"] = family
    return limits


def family_values(field: str) -> dict:
    """``{family: value}`` for every family row that declares ``field``."""
    return {fam: spec[field] for fam, spec in IMAGE_FAMILY_SPECS.items() if field in spec}


# ── Canvas ───────────────────────────────────────────────────────────────────

def resolve_canvas(width: int, height: int, model: Optional[str]) -> Tuple[int, int, List[str]]:
    """Hold W x H to the model's max side and area, then snap each side to its
    grid. Returns (width, height, warnings). Aspect is kept when shrinking."""
    lim = limits_for(model)
    fam = lim["family"]
    max_side = int(lim["max_side"])
    max_pixels = int(lim["max_pixel_area"])
    min_side = int(lim.get("min_side") or 256)
    grid = int(lim.get("dimension_alignment") or 16)
    warnings: List[str] = []

    w = max(min_side, int(width or min_side))
    h = max(min_side, int(height or min_side))
    if w > max_side or h > max_side:
        warnings.append(f"{fam}: side {w}x{h} exceeds max side {max_side}; clamping sides")
        w, h = min(w, max_side), min(h, max_side)

    pixels = w * h
    if pixels > max_pixels:
        scale = (max_pixels / float(pixels)) ** 0.5
        nw = max(min_side, int(w * scale))
        nh = max(min_side, int(h * scale))
        warnings.append(
            f"{fam}: {w}x{h} ({pixels} px) exceeds max area {max_pixels}; scaling to {nw}x{nh}"
        )
        w, h = nw, nh

    w = max(min_side, (max(min_side, w) // grid) * grid)
    h = max(min_side, (max(min_side, h) // grid) * grid)
    while w * h > max_pixels and (w > min_side or h > min_side):
        if w >= h and w > min_side:
            w = max(min_side, w - grid)
        elif h > min_side:
            h = max(min_side, h - grid)
        else:
            break

    if w * h > 1024 * 1024 and fam in ("zimage", "krea2", "flux"):
        warnings.append(f"{fam}: {w}x{h} is >1MP — higher VRAM use; may OOM on 16GB cards")
    return w, h, warnings


# ── Sampling ─────────────────────────────────────────────────────────────────

def defaults_for(model: Optional[str], *, strict: Optional[bool] = None) -> dict:
    """Canvas, steps and guidance a request starts from. Outside strict limits a
    model starts from its family row (see the module docstring)."""
    strict = strict_limits_enabled() if strict is None else strict
    row = IMAGE_MODEL_LIMITS.get(model_key(model) or "") or {}
    lim = limits_for(family_of(model)) if row.get("starts_from_family") and not strict else limits_for(model)
    return {
        "width": int(lim["width"]),
        "height": int(lim["height"]),
        "steps": int(lim["default_steps"]),
        "guidance": float(lim["cfg_when_unset"]),
        "min_steps": lim.get("min_steps"),
        "prompt_style": lim.get("prompt_style", "tags"),
    }


def envelope_steps(model: Optional[str], steps, *, explicit: bool = False) -> int:
    """Steps inside the model's working envelope. A typed count stands; an unset
    or runaway count takes the default; a count under the envelope is raised to
    the measured floor where there is one, else it takes the default."""
    lim = limits_for(model)
    steps = int(steps or 0)
    if explicit:
        return steps
    lo, hi = lim.get("steps_range") or (1, 100)
    default = int(lim["default_steps"])
    if steps <= 0 or steps > hi:
        return default
    if steps < lo:
        floor = lim.get("min_steps")
        return max(steps, int(floor)) if floor else default
    return steps


def envelope_cfg(model: Optional[str], guidance) -> float:
    """Guidance inside the model's range, else its default."""
    lim = limits_for(model)
    lo, hi = lim.get("cfg_range") or (0.0, 30.0)
    try:
        g = float(guidance)
    except (TypeError, ValueError):
        return float(lim["cfg_when_unset"])
    return g if lo <= g <= hi else float(lim["cfg_when_unset"])


def resolve_cfg(model: Optional[str], requested) -> Optional[float]:
    """Guidance for the render: the request, else the model's value."""
    if requested is not None:
        return float(requested)
    return float(limits_for(model)["cfg_when_unset"])


def validator_settings() -> dict:
    """The numeric part of settings_validator.MODEL_SETTINGS, per catalog id."""
    out: dict[str, dict[str, Any]] = {}
    for mid, row in IMAGE_MODEL_LIMITS.items():
        lim = limits_for(mid)
        entry: dict[str, Any] = {
            "guidance_range": tuple(lim["cfg_range"]),
            "recommended_guidance": float(lim["cfg_when_unset"]),
            "min_dimensions": tuple(lim["min_dimensions"]),
            "recommended_dimensions": (int(lim["width"]), int(lim["height"])),
            "steps_range": tuple(lim["steps_range"]),
            "recommended_steps": int(lim["default_steps"]),
            "max_dimensions": (int(lim["max_side"]), int(lim["max_side"])),
        }
        if "hard_clamp" in row:
            entry["hard_clamp"] = bool(row["hard_clamp"])
            # Only a soft-clamped model reads max_pixels (its absolute ceiling).
            if not row["hard_clamp"]:
                entry["max_pixels"] = int(lim["max_pixel_area"])
        if lim.get("engine") == "comfy":
            entry["engine"] = "comfy"
        if row.get("force_max_workers"):
            entry["force_max_workers"] = int(row["force_max_workers"])
        out[mid] = entry
    return out
