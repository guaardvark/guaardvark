"""Family-aware stills resolution limits (Z-Image / Krea / Flux / SDXL / SD).

Used by offline_image_generator, settings_validator and the batch Flux path, so
UI 2K presets are not silently truncated at the old 1536 clamp. The ceilings
are declared in media_model_registry.IMAGE_FAMILY_SPECS and applied by
image_render_limits.resolve_canvas; this module keeps the names its callers use.

Limits are model-family ceilings, not GPU guarantees — 2K on 16GB may OOM.
"""
from __future__ import annotations

from typing import List, Tuple

from backend.services.image_render_limits import family_of, limits_for, resolve_canvas


def resolve_family(model_or_family: str | None) -> str:
    """Normalize catalog key / family string to a limit family."""
    return family_of(model_or_family)


def family_limits(family: str) -> Tuple[int, int]:
    """Return (max_side, max_pixels) for a family."""
    lim = limits_for(family_of(family))
    return int(lim["max_side"]), int(lim["max_pixel_area"])


def clamp_image_dimensions(
    width: int,
    height: int,
    family: str | None,
) -> Tuple[int, int, List[str]]:
    """Clamp W×H to family max side + max area. Returns (w, h, warnings).

    Preserves aspect ratio when shrinking for max_pixels.
    """
    return resolve_canvas(width, height, family_of(family))
