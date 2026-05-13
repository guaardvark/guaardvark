"""MLT filter preset catalog.

Each preset is a small function that takes a chain element + the chain's
duration + the FrameRate, and appends one or more `<filter>` children that
realize the preset's look. Stay close to Shotcut-native MLT services so the
generated .mlt opens with editable filter objects in the Shotcut UI.

Catalog is data: adding a preset = adding one function and one entry to
`PRESETS`. Recipe palettes (`grunge.json` etc.) reference preset slugs.
"""

from __future__ import annotations

import logging
from typing import Callable

from .frame_math import FrameRate, frames_to_smpte

logger = logging.getLogger(__name__)


PresetFn = Callable[[object, int, FrameRate], None]
"""Signature: apply(chain_element, duration_frames, fps) -> None."""


# ---------- helpers ---------------------------------------------------------


def _prop(parent, name: str, value: str) -> None:
    """Append a <property name="X">VALUE</property> to parent."""
    from lxml import etree
    p = etree.SubElement(parent, "property", attrib={"name": name})
    p.text = value


def _add_filter(chain, service: str, props: dict[str, str], duration_frames: int, fps: FrameRate) -> None:
    """Append a <filter mlt_service=...> element spanning the full chain."""
    from lxml import etree
    out_smpte = frames_to_smpte(max(0, duration_frames - 1), fps)
    f = etree.SubElement(chain, "filter", attrib={"in": "00:00:00.000", "out": out_smpte})
    _prop(f, "mlt_service", service)
    for k, v in props.items():
        _prop(f, k, v)


# ---------- Color presets ---------------------------------------------------


def _none(chain, duration_frames: int, fps: FrameRate) -> None:
    """The no-op. Useful when an arrangement clip has filter_preset='none'."""
    return None


def _warm_tint(chain, duration_frames: int, fps: FrameRate) -> None:
    # brightness lift toward warm. Shotcut's "Color Grading" uses lift_gamma_gain.
    _add_filter(chain, "lift_gamma_gain", {
        "lift_r": "0.05", "lift_g": "0.02", "lift_b": "-0.02",
        "gamma_r": "1.05", "gamma_g": "1.00", "gamma_b": "0.97",
        "gain_r": "1.05", "gain_g": "1.00", "gain_b": "0.95",
    }, duration_frames, fps)


def _cool_tint(chain, duration_frames: int, fps: FrameRate) -> None:
    _add_filter(chain, "lift_gamma_gain", {
        "lift_r": "-0.03", "lift_g": "0.00", "lift_b": "0.05",
        "gamma_r": "0.97", "gamma_g": "1.00", "gamma_b": "1.05",
        "gain_r": "0.93", "gain_g": "1.00", "gain_b": "1.07",
    }, duration_frames, fps)


def _high_contrast_bw(chain, duration_frames: int, fps: FrameRate) -> None:
    _add_filter(chain, "grayscale", {}, duration_frames, fps)
    _add_filter(chain, "lift_gamma_gain", {
        "lift_r": "-0.05", "lift_g": "-0.05", "lift_b": "-0.05",
        "gamma_r": "0.85", "gamma_g": "0.85", "gamma_b": "0.85",
        "gain_r": "1.20", "gain_g": "1.20", "gain_b": "1.20",
    }, duration_frames, fps)


def _sepia(chain, duration_frames: int, fps: FrameRate) -> None:
    # Shotcut's sepia is a built-in single filter with u/v offsets.
    _add_filter(chain, "sepia", {"u": "75", "v": "150"}, duration_frames, fps)


def _desaturate(chain, duration_frames: int, fps: FrameRate) -> None:
    _add_filter(chain, "saturation", {"level": "0.4"}, duration_frames, fps)


# ---------- Motion presets --------------------------------------------------


def _slow_zoom_in(chain, duration_frames: int, fps: FrameRate) -> None:
    # affine zoom from 100% → 115% over the chain. Shotcut writes affine
    # `transition.geometry` as keyframes "frame=x/y:wxh".
    out_smpte = frames_to_smpte(max(0, duration_frames - 1), fps)
    from lxml import etree
    f = etree.SubElement(chain, "filter", attrib={"in": "00:00:00.000", "out": out_smpte})
    _prop(f, "mlt_service", "affine")
    _prop(f, "transition.geometry", "0=0/0:100%x100%;-1=-7.5%/-7.5%:115%x115%")
    _prop(f, "transition.fill", "1")
    _prop(f, "transition.distort", "0")


def _vertigo(chain, duration_frames: int, fps: FrameRate) -> None:
    # Classic Hitchcock dolly zoom feel: zoom in then snap back out.
    half_smpte = frames_to_smpte(max(0, duration_frames // 2), fps)
    out_smpte = frames_to_smpte(max(0, duration_frames - 1), fps)
    from lxml import etree
    f = etree.SubElement(chain, "filter", attrib={"in": "00:00:00.000", "out": out_smpte})
    _prop(f, "mlt_service", "affine")
    _prop(f, "transition.geometry", f"0=0/0:100%x100%;{half_smpte}=-10%/-10%:120%x120%;-1=0/0:100%x100%")
    _prop(f, "transition.fill", "1")


def _pan_left(chain, duration_frames: int, fps: FrameRate) -> None:
    out_smpte = frames_to_smpte(max(0, duration_frames - 1), fps)
    from lxml import etree
    f = etree.SubElement(chain, "filter", attrib={"in": "00:00:00.000", "out": out_smpte})
    _prop(f, "mlt_service", "affine")
    _prop(f, "transition.geometry", "0=0/0:100%x100%;-1=-10%/0:100%x100%")
    _prop(f, "transition.fill", "1")


# ---------- Stylize presets -------------------------------------------------


def _oldfilm(chain, duration_frames: int, fps: FrameRate) -> None:
    _add_filter(chain, "oldfilm", {
        "delta": "14", "every": "20",
        "brightness_up": "20", "brightness_down": "30",
        "brightness_low": "0", "brightness_high": "255",
    }, duration_frames, fps)


def _vignette(chain, duration_frames: int, fps: FrameRate) -> None:
    _add_filter(chain, "vignette", {
        "smooth": "0.6", "radius": "0.95",
        "x": "0.5", "y": "0.5",
    }, duration_frames, fps)


def _glow(chain, duration_frames: int, fps: FrameRate) -> None:
    _add_filter(chain, "frei0r.glow", {"0": "0.5"}, duration_frames, fps)


# ---------- Glitch presets --------------------------------------------------


def _pixelate(chain, duration_frames: int, fps: FrameRate) -> None:
    _add_filter(chain, "pixelize", {"block_size": "0.02"}, duration_frames, fps)


def _wave_distort(chain, duration_frames: int, fps: FrameRate) -> None:
    _add_filter(chain, "wave", {"deformX": "10", "deformY": "10"}, duration_frames, fps)


# ---------- Registry --------------------------------------------------------


PRESETS: dict[str, PresetFn] = {
    "none": _none,
    # Color
    "warm-tint": _warm_tint,
    "cool-tint": _cool_tint,
    "high-contrast-bw": _high_contrast_bw,
    "sepia": _sepia,
    "desaturate": _desaturate,
    # Motion
    "slow-zoom-in": _slow_zoom_in,
    "vertigo": _vertigo,
    "pan-left": _pan_left,
    # Stylize
    "oldfilm": _oldfilm,
    "vignette": _vignette,
    "glow": _glow,
    # Glitch
    "pixelate": _pixelate,
    "wave-distort": _wave_distort,
}


PRESET_CATEGORIES: dict[str, list[str]] = {
    "Color":    ["warm-tint", "cool-tint", "high-contrast-bw", "sepia", "desaturate"],
    "Motion":   ["slow-zoom-in", "vertigo", "pan-left"],
    "Stylize":  ["oldfilm", "vignette", "glow"],
    "Glitch":   ["pixelate", "wave-distort"],
}


def apply_filter(chain, preset_name: str, duration_frames: int, fps: FrameRate) -> None:
    """Apply a named filter preset to a chain element. Unknown names log and skip."""
    if not preset_name or preset_name == "none":
        return
    fn = PRESETS.get(preset_name)
    if fn is None:
        logger.warning("unknown filter preset %r — skipping", preset_name)
        return
    fn(chain, duration_frames, fps)


def list_presets() -> dict[str, list[str]]:
    """Return the catalog grouped by category."""
    return {cat: list(slugs) for cat, slugs in PRESET_CATEGORIES.items()}
