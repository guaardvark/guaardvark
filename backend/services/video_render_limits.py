"""Hold a render request to the limits its video model declares.

Every per-model constraint the render enforces lives in the registry
(``video_model_registry.model_capabilities``: an entry's own keys over its
family's ``FAMILY_SPECS`` row) and is applied here, so a model is added by data
plus a graph builder, and the builders only decide graph shape.

Two families of rules differ from what the registry declares, and are kept as
they are until ``GUAARDVARK_VIDEO_STRICT_LIMITS=1`` asks for the declared ones
(docs/video-pipeline.md F-11, F-12, F-13, F-14):

- the size is snapped to the family grid, not the entry's (Wan entries declare
  32 px, the render snaps to 16);
- a length is moved by ``frame_snap`` (None for Wan and CogVideoX) and is not
  held to ``max_frames``; the step floor applies only where
  ``enforce_min_steps`` says so.

With the switch on, sizes land on the entry's grid, lengths are snapped down
onto ``frame_rule`` (up where the rule's template rounds up) inside
``[min_frames, max_frames]``, and every model raises a preset step count to its
floor.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Optional

from backend.services.video_model_registry import (
    VIDEO_MODEL_REGISTRY,
    family_spec,
    model_capabilities,
)

logger = logging.getLogger(__name__)

STRICT_LIMITS_ENV = "GUAARDVARK_VIDEO_STRICT_LIMITS"
# The speed profile a graph builder was not told; only a backend verified for
# every profile ("*") covers it.
UNKNOWN_PROFILE = "?"
TEXT_ENCODER_DEVICE_ENV = "GUAARDVARK_WAN_CLIP_DEVICE"

# Ratio presets the UI offers, as width/height. A clamp names the ratio it
# snapped to rather than an arbitrary decimal.
ASPECT_RATIOS = {
    "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:3": 4 / 3, "3:2": 3 / 2,
    "21:9": 21 / 9, "3:4": 3 / 4,
}
# Within 6% of a declared ratio is that ratio: a 32 px snap moves 1280x704
# (native, 1.82) 2.3% off exact 16:9, while the nearest wrong preset (3:2) sits
# 15.6% away.
ASPECT_TOLERANCE = 0.06


def strict_limits_enabled() -> bool:
    return (os.environ.get(STRICT_LIMITS_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def limits_for(model_id: str, family: Optional[str] = None) -> dict:
    """The capability record for a model, or its family's row for an id the
    registry does not know (aliases such as ``wan22``)."""
    caps = model_capabilities(model_id)
    if caps:
        return caps
    spec = family_spec(family or "")
    spec.setdefault("attention_verified", {"pytorch": ["*"]})
    return spec


def _family_of(model_id: str, family: Optional[str]) -> Optional[str]:
    return (VIDEO_MODEL_REGISTRY.get(model_id or "") or {}).get("type") or family


# ── Canvas ───────────────────────────────────────────────────────────────────

def supported_aspect_ratios(model_id: str) -> list:
    """Ratio keys a model declares, or [] when it takes any."""
    entry = VIDEO_MODEL_REGISTRY.get(model_id) or {}
    return [r for r in (entry.get("aspect_ratios") or []) if r in ASPECT_RATIOS]


def clamp_aspect_ratio(model_id: str, width: int, height: int) -> tuple:
    """Reshape to the nearest ratio the model declares, keeping pixel area.

    Within ASPECT_TOLERANCE of a declared ratio the size is left alone.
    Orientation wins over log-distance: 4:3 sits as far from 16:9 as from 1:1,
    and a landscape request must not come back square."""
    supported = supported_aspect_ratios(model_id)
    if not supported or width <= 0 or height <= 0:
        return width, height
    requested = width / height
    if any(abs(requested / ASPECT_RATIOS[k] - 1.0) <= ASPECT_TOLERANCE for k in supported):
        return width, height

    def _orient(r: float) -> int:
        return (r > 1.0) - (r < 1.0)

    candidates = [k for k in supported if _orient(ASPECT_RATIOS[k]) == _orient(requested)] or supported
    key = min(candidates, key=lambda k: abs(math.log(requested / ASPECT_RATIOS[k])))
    target = ASPECT_RATIOS[key]
    area = width * height
    new_w = int(round(math.sqrt(area * target)))
    new_h = int(round(new_w / target)) or 1
    logger.warning(
        "Reshaped %s video dims %dx%d (%.2f:1) → %dx%d (%s) — the model declares "
        "%s; off-native frames warp rather than crop",
        model_id, width, height, requested, new_w, new_h, key, "/".join(supported),
    )
    return new_w, new_h


def pixel_area_cap(model_id: str, frames: int = 0, family: Optional[str] = None) -> Optional[int]:
    """The entry's max_pixel_area (else its family's), lowered by the
    duration tier a longer clip falls in (MiniMax H3: 480p beyond ~7 s on 16 GB)."""
    entry = VIDEO_MODEL_REGISTRY.get(model_id) or {}
    cap = entry.get("max_pixel_area")
    tiers = sorted((t for t in entry.get("duration_tiers") or [] if t.get("frames")), key=lambda t: t["frames"])
    if tiers and frames:
        tier = next((t for t in tiers if int(frames) <= int(t["frames"])), tiers[-1])
        if tier.get("max_pixel_area"):
            cap = min(cap or tier["max_pixel_area"], tier["max_pixel_area"])
    return cap or family_spec(_family_of(model_id, family) or "").get("max_pixel_area")


def clamp_pixel_area(model_id: str, width: int, height: int, frames: int = 0,
                     family: Optional[str] = None) -> tuple:
    """Scale down, keeping aspect, to the pixel budget. ~1.0 MPx (1280x736) is
    proven on 16 GB cards; 3.7 MPx (1920x1920) never finished on either Wan."""
    cap = pixel_area_cap(model_id, frames, family)
    area = int(width) * int(height)
    if not cap or area <= cap:
        return width, height
    scale = (cap / area) ** 0.5
    new_w, new_h = int(width * scale), int(height * scale)
    logger.warning(
        "Clamped %s video dims %dx%d (%.1f MPx) → %dx%d to stay within the "
        "%.1f MPx budget — larger frames time out on this hardware",
        model_id, width, height, area / 1e6, new_w, new_h, cap / 1e6,
    )
    return new_w, new_h


def dimension_alignment(model_id: str, family: Optional[str] = None, *, strict: Optional[bool] = None) -> int:
    """The grid the render snaps to: the family's, or under strict limits the
    entry's own declaration."""
    strict = strict_limits_enabled() if strict is None else strict
    fam = _family_of(model_id, family)
    if strict:
        declared = limits_for(model_id, fam).get("dimension_alignment")
        if declared:
            return int(declared)
    return int(family_spec(fam or "").get("dimension_alignment") or 16)


def align_dimensions(model_id: str, width: int, height: int, family: Optional[str] = None,
                     *, strict: Optional[bool] = None) -> tuple:
    """Round each side to the alignment grid (off by one is the "tensor a (51)
    must match tensor b (50)" crash). Under strict limits the result is also
    kept inside the pixel budget the rounding could push it over."""
    strict = strict_limits_enabled() if strict is None else strict
    align = dimension_alignment(model_id, family, strict=strict)
    new_w = max(align, round(width / align) * align)
    new_h = max(align, round(height / align) * align)
    if strict:
        cap = pixel_area_cap(model_id, 0, family)
        while cap and new_w * new_h > cap and max(new_w, new_h) > align:
            if new_w >= new_h:
                new_w -= align
            else:
                new_h -= align
    if (new_w, new_h) != (width, height):
        logger.warning(
            "Aligned video dims for %s: %dx%d → %dx%d (must be multiple of %d)",
            model_id, width, height, new_w, new_h, align,
        )
    return new_w, new_h


def resolve_canvas(model_id: str, width: int, height: int, frames: int = 0,
                   family: Optional[str] = None, *, strict: Optional[bool] = None) -> tuple:
    """(width, height) the render uses: declared ratio, pixel budget, grid."""
    width, height = clamp_aspect_ratio(model_id, width, height)
    width, height = clamp_pixel_area(model_id, width, height, frames, family)
    return align_dimensions(model_id, width, height, family, strict=strict)


# ── Length ───────────────────────────────────────────────────────────────────

def _rule(frame_rule: Optional[str]) -> Optional[tuple]:
    import re

    m = re.match(r"^(\d+)[a-z]\+(\d+)$", str(frame_rule or "").replace(" ", ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def snap_to_rule(frames, frame_rule: Optional[str], snap: Optional[str], *,
                 min_frames: Optional[int] = None, when_unset: Optional[int] = None) -> int:
    """Move a length onto ``step·k + offset``: "down", "up" or "nearest";
    None leaves it as asked. ``when_unset`` stands in for 0/None."""
    n = int(frames or 0) or int(when_unset or 0)
    rule = _rule(frame_rule)
    if not snap or not rule:
        return n
    step, offset = rule
    n = max(n, int(min_frames or 1))
    if snap == "nearest":
        k = int((n - offset) / step + 0.5)
    elif snap == "up":
        k = -((offset - n) // step)
    else:
        k = (n - offset) // step
    out = offset + max(k, 0) * step
    if min_frames and out < min_frames:
        out += step * -((out - int(min_frames)) // step)
    return out


def resolve_frames(model_id: str, frames, family: Optional[str] = None, *, strict: Optional[bool] = None) -> int:
    """The length the render asks the model for."""
    strict = strict_limits_enabled() if strict is None else strict
    caps = limits_for(model_id, _family_of(model_id, family))
    rule = caps.get("frame_rule")
    if not strict:
        return snap_to_rule(frames, rule, caps.get("frame_snap"), min_frames=caps.get("min_frames"),
                            when_unset=caps.get("frames_when_unset"))
    snap = "up" if caps.get("frame_snap") == "up" else "down"
    n = int(frames or 0) or int(caps.get("frames_when_unset") or 0)
    max_frames = caps.get("max_frames")
    if max_frames:
        n = min(n, int(max_frames))
    out = snap_to_rule(n, rule, snap, min_frames=caps.get("min_frames"))
    if max_frames and out > int(max_frames):
        out = snap_to_rule(int(max_frames), rule, "down", min_frames=caps.get("min_frames"))
    if out != int(frames or 0):
        logger.info("%s: %s frames → %d (frame rule %s, max %s)", model_id, frames, out, rule, max_frames)
    return out


def family_frame_count(family: str, frames) -> int:
    """resolve_frames for a caller that knows only the family."""
    return resolve_frames("", frames, family)


# ── Steps and guidance ───────────────────────────────────────────────────────

def resolve_steps(model_id: str, requested, *, explicit: bool = False, profile: Optional[dict] = None,
                  family: Optional[str] = None, strict: Optional[bool] = None) -> int:
    """Sampling steps: a count the person typed stands; a speed profile brings
    its own; otherwise the request or the model's default, raised to the floor
    where the model enforces one (every model under strict limits)."""
    strict = strict_limits_enabled() if strict is None else strict
    caps = limits_for(model_id, _family_of(model_id, family))
    profile = profile or {}
    requested = int(requested or 0)
    floor = int(profile.get("min_steps") or caps.get("min_steps") or 0)
    if explicit and requested > 0:
        if floor and requested < floor:
            logger.info("%s: keeping the %d steps typed by the person, below the %d-step floor",
                        model_id, requested, floor)
        return requested
    if profile.get("steps"):
        return int(profile["steps"])
    steps = requested or int(caps.get("default_steps") or 0)
    if floor and steps < floor and (caps.get("enforce_min_steps") or strict):
        logger.info("%s: raised %d preset steps to the %d-step floor", model_id, steps, floor)
        steps = floor
    return steps


def resolve_cfg(model_id: str, requested, family: Optional[str] = None):
    """Guidance: the request, else the model's value for none given. Outside
    the declared range the value is kept and logged."""
    caps = limits_for(model_id, _family_of(model_id, family))
    cfg = requested if requested is not None else caps.get("cfg_when_unset")
    lo_hi = caps.get("cfg_range")
    if cfg is not None and lo_hi and not (lo_hi[0] <= float(cfg) <= lo_hi[1]):
        logger.info("%s prefers CFG %s–%s (got %.2f); keeping caller value but quality may degrade.",
                    model_id, lo_hi[0], lo_hi[1], float(cfg))
    return cfg


def resolve_fps(model_id: str, requested, family: Optional[str] = None):
    return requested or limits_for(model_id, _family_of(model_id, family)).get("native_fps") or 24


# ── Hardware ─────────────────────────────────────────────────────────────────

def min_vram_gb(model_id: str, family: Optional[str] = None) -> int:
    """Total VRAM (GB) a model needs to run at all; 0 when nothing is declared."""
    return int(limits_for(model_id, _family_of(model_id, family)).get("min_vram_gb") or 0)


def text_encoder_device(model_id: str, total_vram_mb: Optional[int] = None,
                        family: Optional[str] = None) -> str:
    """"cpu" when the text encoder should stay off the card, else "default".

    ``GUAARDVARK_WAN_CLIP_DEVICE`` (cpu|default) overrides. A family that
    declares no threshold keeps the default; an unreadable probe counts as a
    small card."""
    override = (os.environ.get(TEXT_ENCODER_DEVICE_ENV) or "").strip().lower()
    if override in ("cpu", "default"):
        return override
    threshold = limits_for(model_id, _family_of(model_id, family)).get("text_encoder_cpu_max_vram_mb")
    if not threshold:
        return "default"
    total = total_vram_mb
    if total is None:
        try:
            from backend.services.gpu_resource_coordinator import get_available_vram
            info = get_available_vram()
            if info.get("success"):
                total = int(info.get("total_mb") or 0) or None
        except Exception:  # noqa: BLE001
            total = None
    if not total or total <= 0 or total <= int(threshold):
        return "cpu"
    return "default"


def attention_pin(model_id: str, speed_profile: Optional[str], launch_backend: str,
                  family: Optional[str] = None) -> Optional[str]:
    """The backend to pin in the graph, or None to leave ComfyUI's choice.

    The launch default (PyTorch) is never pinned. Another launch backend is
    pinned away from unless the entry lists it as verified for the profile
    being rendered; no profile is the model's standard one, and
    UNKNOWN_PROFILE is covered only by ``"*"``."""
    from backend.services.comfyui_launch_flags import ATTENTION_DEFAULT

    caps = limits_for(model_id, _family_of(model_id, family))
    target = caps.get("attention")
    launch = (launch_backend or ATTENTION_DEFAULT).strip().lower()
    if not target or launch == target:
        return None
    verified = (caps.get("attention_verified") or {}).get(launch) or []
    profile = speed_profile or "standard"
    if "*" in verified or profile in verified:
        return None
    return target


# ── Modes ────────────────────────────────────────────────────────────────────

def start_image(model_id: str, image_path: Optional[str], family: Optional[str] = None) -> tuple:
    """(start image to animate or None, error or None), from the declared modes.

    An image on a text-only model is refused with its image-to-video sibling
    named. A model that also renders from text alone renders text-to-video
    when the named file is missing (docs/video-pipeline.md F-28); one that
    needs the image refuses."""
    from pathlib import Path

    from backend.services.video_model_registry import i2v_model_for

    caps = limits_for(model_id, _family_of(model_id, family))
    modes = caps.get("modes") or []
    if image_path and not ("i2v" in modes or "flf2v" in modes):
        sibling = i2v_model_for(model_id, default="")
        hint = f" Use {sibling} for image-to-video." if sibling and sibling != model_id else ""
        return None, f"{model_id} is text-to-video only.{hint}"
    if image_path and Path(image_path).exists():
        return image_path, None
    if "t2v" in modes:
        if image_path:
            logger.warning("%s: start image %s not found; rendering text-to-video", model_id, image_path)
        return None, None
    name = (VIDEO_MODEL_REGISTRY.get(model_id) or {}).get("name") or model_id
    return None, f"{name} requires an input image."
