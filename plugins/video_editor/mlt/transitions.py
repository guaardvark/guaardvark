"""MLT transition preset catalog.

A transition merges two parallel tracks (V1, V2) over an overlap region.
The compose function lays out alternating clips on V1/V2 with the required
overlap, then attaches one `<transition>` per cut.

Special case: `hard-cut` is the no-op — clips stay on a single track,
sequentially, no transition element.

For a transition over [t0, t1]:
    - V1 clip ends at t1 (extended past the section boundary by overlap_frames)
    - V2 clip starts at t0 (entered ahead of its section boundary by overlap_frames)
    - <transition> element on the tractor with in=t0 out=t1, referencing
      a_track=V1 and b_track=V2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from .frame_math import FrameRate, frames_to_smpte

logger = logging.getLogger(__name__)


@dataclass
class TransitionSpec:
    """How a transition affects timing + what XML to emit."""

    slug: str
    overlap_seconds: float
    is_hard_cut: bool = False
    emit_xml: Callable | None = None  # (parent, in_smpte, out_smpte, a_track, b_track) -> None

    def overlap_frames(self, fps: FrameRate) -> int:
        return int(round(self.overlap_seconds * fps.num / fps.den))


# ---------- emitters --------------------------------------------------------


def _prop(parent, name: str, value: str) -> None:
    from lxml import etree
    p = etree.SubElement(parent, "property", attrib={"name": name})
    p.text = value


def _emit_luma(parent, in_smpte: str, out_smpte: str, a_track: int, b_track: int, *, resource: str = "", softness: str = "0.0") -> None:
    """Generic luma transition. Empty resource = linear cross-dissolve."""
    from lxml import etree
    t = etree.SubElement(parent, "transition", attrib={"in": in_smpte, "out": out_smpte})
    _prop(t, "a_track", str(a_track))
    _prop(t, "b_track", str(b_track))
    _prop(t, "mlt_service", "luma")
    _prop(t, "softness", softness)
    if resource:
        _prop(t, "resource", resource)


def _emit_cross_dissolve(parent, in_smpte: str, out_smpte: str, a_track: int, b_track: int) -> None:
    _emit_luma(parent, in_smpte, out_smpte, a_track, b_track, resource="", softness="0.0")


def _emit_dip_to_black(parent, in_smpte: str, out_smpte: str, a_track: int, b_track: int) -> None:
    """Dip-to-black: composite over a black field. Approximated by luma with high softness
    plus a `mlt_service=luma` and `progress_threshold` to a darker midpoint. Shotcut's
    "Dip To Black" is actually a paired brightness keyframe set — we emulate with luma
    + halfway-stop. For v1 we use the same luma but a heavier softness so it darkens."""
    _emit_luma(parent, in_smpte, out_smpte, a_track, b_track, resource="", softness="0.6")


def _emit_luma_circle(parent, in_smpte: str, out_smpte: str, a_track: int, b_track: int) -> None:
    # Shotcut ships luma maps under .../share/shotcut/lumas/ — using a relative
    # filename lets MLT look in the standard search paths.
    _emit_luma(parent, in_smpte, out_smpte, a_track, b_track, resource="luma13.pgm", softness="0.3")


def _emit_luma_wipe(parent, in_smpte: str, out_smpte: str, a_track: int, b_track: int) -> None:
    _emit_luma(parent, in_smpte, out_smpte, a_track, b_track, resource="luma01.pgm", softness="0.1")


# ---------- Registry --------------------------------------------------------


PRESETS: dict[str, TransitionSpec] = {
    "hard-cut":       TransitionSpec(slug="hard-cut", overlap_seconds=0.0, is_hard_cut=True, emit_xml=None),
    "cross-dissolve": TransitionSpec(slug="cross-dissolve", overlap_seconds=0.4, emit_xml=_emit_cross_dissolve),
    "dip-to-black":   TransitionSpec(slug="dip-to-black", overlap_seconds=0.6, emit_xml=_emit_dip_to_black),
    "luma-circle":    TransitionSpec(slug="luma-circle", overlap_seconds=0.5, emit_xml=_emit_luma_circle),
    "luma-wipe":      TransitionSpec(slug="luma-wipe", overlap_seconds=0.5, emit_xml=_emit_luma_wipe),
}


def get(slug: str) -> TransitionSpec:
    """Look up a preset; unknown slugs fall back to hard-cut."""
    if slug not in PRESETS:
        logger.warning("unknown transition preset %r — using hard-cut", slug)
        return PRESETS["hard-cut"]
    return PRESETS[slug]


def list_presets() -> list[str]:
    return list(PRESETS.keys())


def emit_transition(
    parent,
    slug: str,
    in_frame: int,
    out_frame: int,
    a_track: int,
    b_track: int,
    fps: FrameRate,
) -> None:
    """Emit a transition <transition> element on `parent` (typically the tractor)."""
    spec = get(slug)
    if spec.is_hard_cut or spec.emit_xml is None:
        return
    in_smpte = frames_to_smpte(max(0, in_frame), fps)
    out_smpte = frames_to_smpte(max(0, out_frame - 1), fps)
    spec.emit_xml(parent, in_smpte, out_smpte, a_track, b_track)
