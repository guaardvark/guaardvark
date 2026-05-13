"""Generic timeline JSON → Shotcut-compatible MLT XML.

Accepts the same shape the existing VideoEditorPage already produces and the
ffmpeg backend already consumes — see `backend/api/video_overlay_api.py`
docstring on /render-timeline. We translate it to MLT and emit a `.mlt`.

Currently supports:
  - One video clip with optional in/out trim (video_trim_start, video_trim_end)
  - N text overlay filters (dynamictext) with per-text timing, font, color, position
  - One audio replacement track with volume control

Multi-clip support is M4-ish — the JSON shape doesn't carry it yet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .frame_math import FrameRate, frames_to_smpte, seconds_to_absolute_frame
from .mlt_parser import ProjectProfile


@dataclass
class TextElement:
    """One text overlay on the video — UI coordinates are pixel-space."""

    text: str
    font_size: int = 48
    font_color: str = "#ffffff"
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    start_seconds: float = 0.0
    end_seconds: float = 0.0


@dataclass
class Timeline:
    video_path: str
    audio_path: Optional[str] = None
    video_trim_start: float = 0.0
    video_trim_end: Optional[float] = None  # None = full duration
    audio_volume: float = 1.0
    text_elements: list[TextElement] = field(default_factory=list)


def timeline_from_payload(payload: dict[str, Any]) -> Timeline:
    """Translate the Flask request body into a Timeline."""
    text_els = []
    for t in payload.get("text_elements") or []:
        text_els.append(
            TextElement(
                text=str(t.get("text", "")),
                font_size=int(t.get("fontSize", t.get("font_size", 48))),
                font_color=str(t.get("fontColor", t.get("font_color", "#ffffff"))),
                x=float(t.get("x", 0.0)),
                y=float(t.get("y", 0.0)),
                rotation=float(t.get("rotation", 0.0)),
                start_seconds=float(t.get("startSeconds", t.get("start_seconds", 0.0))),
                end_seconds=float(t.get("endSeconds", t.get("end_seconds", 0.0))),
            )
        )
    return Timeline(
        video_path=str(payload["video_path"]),
        audio_path=payload.get("audio_path") or None,
        video_trim_start=float(payload.get("video_trim_start") or 0.0),
        video_trim_end=(float(payload["video_trim_end"]) if payload.get("video_trim_end") not in (None, "") else None),
        audio_volume=float(payload.get("audio_volume", 1.0)),
        text_elements=text_els,
    )


def compose_timeline(
    timeline: Timeline,
    output_path: str | Path,
    profile: ProjectProfile,
    *,
    video_source_duration_seconds: Optional[float] = None,
) -> Path:
    """Emit a Shotcut .mlt for `timeline`. Returns the written path."""
    from lxml import etree

    fps = profile.frame_rate
    out_path = Path(output_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve the video out-point. If the caller didn't trim and we don't know
    # the source duration, fall back to a permissive 10-minute ceiling — Shotcut
    # will clamp at the actual end-of-file at playback time.
    if timeline.video_trim_end is not None:
        clip_duration_s = max(0.0, timeline.video_trim_end - timeline.video_trim_start)
    elif video_source_duration_seconds is not None:
        clip_duration_s = max(0.0, video_source_duration_seconds - timeline.video_trim_start)
    else:
        clip_duration_s = 600.0  # 10 minutes
    clip_duration_frames = seconds_to_absolute_frame(clip_duration_s, fps)

    video_in_frame = seconds_to_absolute_frame(timeline.video_trim_start, fps)
    video_out_frame = video_in_frame + clip_duration_frames

    mlt = etree.Element(
        "mlt",
        attrib={
            "LC_NUMERIC": "C",
            "version": "7.24.0",
            "title": "Shotcut version 24.04",
            "producer": "main_bin",
            "root": str(out_path.parent),
        },
    )
    _append_profile(mlt, profile)
    _append_main_bin(mlt)

    # Video chain — one chain for the single source clip.
    video_chain_id = "chain_video"
    _append_chain(
        mlt,
        video_chain_id,
        timeline.video_path,
        length_frames=video_out_frame,
        fps=fps,
        audio=False,
    )
    _append_text_filters(mlt, video_chain_id, timeline.text_elements, fps, profile)

    # Audio replacement chain (optional).
    audio_chain_id = None
    if timeline.audio_path:
        audio_chain_id = "chain_audio"
        _append_chain(
            mlt,
            audio_chain_id,
            timeline.audio_path,
            length_frames=clip_duration_frames,
            fps=fps,
            audio=True,
            volume=timeline.audio_volume,
        )

    _append_video_playlist(mlt, video_chain_id, video_in_frame, video_out_frame, fps)
    if audio_chain_id:
        _append_audio_playlist(mlt, audio_chain_id, clip_duration_frames, fps)
    _append_tractor(mlt, clip_duration_frames, fps, has_audio_track=bool(audio_chain_id))

    tree = etree.ElementTree(mlt)
    tree.write(
        str(out_path),
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=False,
    )
    return out_path


# ---------- XML helpers (shared shape with mlt_writer, kept local for clarity) ---


def _append_profile(mlt, profile: ProjectProfile) -> None:
    from lxml import etree

    fps = profile.frame_rate
    etree.SubElement(
        mlt,
        "profile",
        attrib={
            "description": f"automatic for {profile.width}x{profile.height}",
            "width": str(profile.width),
            "height": str(profile.height),
            "progressive": "1",
            "sample_aspect_num": str(profile.sample_aspect_num),
            "sample_aspect_den": str(profile.sample_aspect_den),
            "display_aspect_num": str(profile.width),
            "display_aspect_den": str(profile.height),
            "frame_rate_num": str(fps.num),
            "frame_rate_den": str(fps.den),
            "colorspace": "709",
        },
    )


def _append_main_bin(mlt) -> None:
    from lxml import etree

    pl = etree.SubElement(mlt, "playlist", attrib={"id": "main_bin", "title": "Shotcut version 24.04"})
    _prop(pl, "shotcut:projectAudioChannels", "2")
    _prop(pl, "shotcut:projectFolder", "0")
    _prop(pl, "xml_retain", "1")


def _append_chain(
    mlt,
    chain_id: str,
    resource: str,
    length_frames: int,
    fps: FrameRate,
    *,
    audio: bool,
    volume: Optional[float] = None,
) -> None:
    from lxml import etree

    smpte = frames_to_smpte(max(length_frames, 1), fps)
    chain = etree.SubElement(mlt, "chain", attrib={"id": chain_id, "out": smpte})
    _prop(chain, "length", smpte)
    _prop(chain, "resource", str(Path(resource).resolve()))
    _prop(chain, "mlt_service", "avformat-novalidate")
    if audio:
        _prop(chain, "audio_index", "0")
        _prop(chain, "video_index", "-1")
        if volume is not None and volume != 1.0:
            f = etree.SubElement(chain, "filter", attrib={"id": f"{chain_id}_vol"})
            _prop(f, "mlt_service", "volume")
            _prop(f, "gain", f"{volume:.3f}")


def _append_text_filters(
    mlt_root,
    video_chain_id: str,
    elements: list[TextElement],
    fps: FrameRate,
    profile: ProjectProfile,
) -> None:
    """Attach <filter mlt_service='dynamictext'> children to the video chain.

    MLT's dynamictext filter uses `geometry` in the form "X/Y:WxH" (pixels)
    or with trailing '%' for percentages. Shotcut writes pixel-space.
    """
    from lxml import etree

    if not elements:
        return
    chain = next((c for c in mlt_root.iter("chain") if c.get("id") == video_chain_id), None)
    if chain is None:
        return

    for i, el in enumerate(elements):
        in_frame = seconds_to_absolute_frame(max(0.0, el.start_seconds), fps)
        out_frame = seconds_to_absolute_frame(max(el.start_seconds, el.end_seconds), fps)
        if out_frame <= in_frame:
            continue

        filt = etree.SubElement(
            chain,
            "filter",
            attrib={
                "id": f"{video_chain_id}_text{i}",
                "in": frames_to_smpte(in_frame, fps),
                "out": frames_to_smpte(max(out_frame - 1, in_frame), fps),
            },
        )
        _prop(filt, "mlt_service", "dynamictext")
        _prop(filt, "argument", el.text)
        _prop(filt, "family", "Sans")
        _prop(filt, "size", str(el.font_size))
        _prop(filt, "fgcolour", _normalize_color(el.font_color))
        _prop(filt, "bgcolour", "0x00000000")
        _prop(filt, "olcolour", "0x000000ff")
        _prop(filt, "outline", "1")
        _prop(filt, "weight", "500")
        # geometry: top-left origin, in pixels. Width 0 lets MLT auto-size.
        _prop(filt, "geometry", f"{int(el.x)}/{int(el.y)}:0x0")
        _prop(filt, "halign", "left")
        _prop(filt, "valign", "top")
        if el.rotation:
            _prop(filt, "rotation", f"{el.rotation:.3f}")


def _normalize_color(c: str) -> str:
    """Convert '#RRGGBB' / '#RRGGBBAA' / 'name' → MLT's '0xRRGGBBAA' form."""
    if not c:
        return "0xffffffff"
    s = c.strip().lower()
    if s.startswith("#"):
        hexpart = s[1:]
        if len(hexpart) == 6:
            return f"0x{hexpart}ff"
        if len(hexpart) == 8:
            return f"0x{hexpart}"
    if s.startswith("0x"):
        return s
    named = {
        "white": "0xffffffff",
        "black": "0x000000ff",
        "red": "0xff0000ff",
        "green": "0x00ff00ff",
        "blue": "0x0000ffff",
        "yellow": "0xffff00ff",
    }
    return named.get(s, "0xffffffff")


def _append_video_playlist(
    mlt,
    video_chain_id: str,
    in_frame: int,
    out_frame: int,
    fps: FrameRate,
) -> None:
    from lxml import etree

    pl = etree.SubElement(mlt, "playlist", attrib={"id": "playlist0"})
    _prop(pl, "shotcut:video", "1")
    _prop(pl, "shotcut:name", "V1")
    etree.SubElement(
        pl,
        "entry",
        attrib={
            "producer": video_chain_id,
            "in": frames_to_smpte(in_frame, fps),
            "out": frames_to_smpte(max(out_frame - 1, in_frame), fps),
        },
    )


def _append_audio_playlist(
    mlt,
    audio_chain_id: str,
    duration_frames: int,
    fps: FrameRate,
) -> None:
    from lxml import etree

    pl = etree.SubElement(mlt, "playlist", attrib={"id": "playlist1"})
    _prop(pl, "shotcut:audio", "1")
    _prop(pl, "shotcut:name", "A1")
    etree.SubElement(
        pl,
        "entry",
        attrib={
            "producer": audio_chain_id,
            "in": "00:00:00.000",
            "out": frames_to_smpte(max(duration_frames - 1, 0), fps),
        },
    )


def _append_tractor(
    mlt,
    duration_frames: int,
    fps: FrameRate,
    *,
    has_audio_track: bool,
) -> None:
    from lxml import etree

    out_smpte = frames_to_smpte(max(0, duration_frames - 1), fps)
    tractor = etree.SubElement(
        mlt,
        "tractor",
        attrib={
            "id": "tractor0",
            "title": "Shotcut version 24.04",
            "global_feed": "1",
            "in": "00:00:00.000",
            "out": out_smpte,
        },
    )
    _prop(tractor, "shotcut", "1")
    _prop(tractor, "shotcut:projectAudioChannels", "2")
    _prop(tractor, "shotcut:projectFolder", "0")
    _prop(tractor, "shotcut:scaleFactor", "1")
    _prop(tractor, "shotcut:trackHeight", "50")

    multi = etree.SubElement(tractor, "multitrack")
    etree.SubElement(multi, "track", attrib={"producer": "playlist0"})
    if has_audio_track:
        etree.SubElement(multi, "track", attrib={"producer": "playlist1", "hide": "video"})


def _prop(parent, name: str, value: str) -> None:
    from lxml import etree

    p = etree.SubElement(parent, "property", attrib={"name": name})
    p.text = value


# Suppress unused-import warning — uuid is used by callers in service/app.py
_ = uuid
