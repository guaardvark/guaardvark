"""Stage 4 — emit a Shotcut-compatible MLT project from a cut list.

Required Shotcut annotations (without these the file may render via `melt`
but corrupts the Shotcut GUI on load — see research doc §"Shotcut-Specific
XML Annotations"):
  shotcut:project, shotcut:scaleFactor, shotcut:trackHeight,
  shotcut:name, shotcut:audio, shotcut:video.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .frame_math import FrameRate, frames_to_smpte, seconds_to_absolute_frame
from .mlt_parser import MediaAsset, ProjectProfile


@dataclass
class CutPlan:
    """One visual cut in the final timeline."""

    source_path: str
    in_frame: int
    out_frame: int

    @property
    def duration_frames(self) -> int:
        return self.out_frame - self.in_frame


def plan_cuts_from_beats(
    beat_times_seconds: list[float],
    video_assets: list[MediaAsset],
    profile: ProjectProfile,
    *,
    source_durations_seconds: Optional[dict[str, float]] = None,
    seed: Optional[int] = None,
) -> list[CutPlan]:
    """Map a beat-times array to a sequence of CutPlans, one per inter-beat interval.

    For each interval, pick a video at random (deterministic if `seed` is set),
    pick a starting offset that leaves enough footage for the required duration,
    and emit a CutPlan with absolute in/out frame indices.
    """
    if len(beat_times_seconds) < 2:
        return []
    if not video_assets:
        raise ValueError("video_assets is empty — nothing to cut from")

    rng = random.Random(seed)
    fps = profile.frame_rate
    abs_frames = [seconds_to_absolute_frame(t, fps) for t in beat_times_seconds]

    plans: list[CutPlan] = []
    for i in range(len(abs_frames) - 1):
        clip_duration = abs_frames[i + 1] - abs_frames[i]
        if clip_duration <= 0:
            continue
        asset = rng.choice(video_assets)
        max_in_frame = _max_in_frame_for(asset, clip_duration, fps, source_durations_seconds)
        in_frame = rng.randint(0, max_in_frame) if max_in_frame > 0 else 0
        plans.append(
            CutPlan(
                source_path=asset.resource_path,
                in_frame=in_frame,
                out_frame=in_frame + clip_duration,
            )
        )
    return plans


def _max_in_frame_for(
    asset: MediaAsset,
    clip_duration: int,
    fps: FrameRate,
    source_durations_seconds: Optional[dict[str, float]],
) -> int:
    if source_durations_seconds and asset.resource_path in source_durations_seconds:
        total = seconds_to_absolute_frame(source_durations_seconds[asset.resource_path], fps)
        return max(0, total - clip_duration)
    # Unknown source length — fall back to a permissive ceiling (10 minutes).
    # Shotcut will clamp at playback if we overshoot.
    return max(0, seconds_to_absolute_frame(600.0, fps) - clip_duration)


def write_project(
    output_path: str | Path,
    cuts: list[CutPlan],
    audio_path: str,
    profile: ProjectProfile,
    *,
    audio_in_seconds: float = 0.0,
    audio_out_seconds: Optional[float] = None,
) -> Path:
    """Serialize a CutPlan list + audio track to a Shotcut .mlt file."""
    from lxml import etree

    fps = profile.frame_rate
    out_path = Path(output_path).resolve()

    timeline_end = cuts[-1].in_frame + cuts[-1].duration_frames if cuts else 0
    video_track_length = sum(c.duration_frames for c in cuts)

    if audio_out_seconds is None:
        audio_out_frame = max(video_track_length, timeline_end)
    else:
        audio_out_frame = seconds_to_absolute_frame(audio_out_seconds, fps)
    audio_in_frame = seconds_to_absolute_frame(audio_in_seconds, fps)

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
    _append_playlist_main_bin(mlt, cuts, audio_path)

    video_chain_ids: list[str] = []
    for i, cut in enumerate(cuts):
        chain_id = f"chain{i}"
        video_chain_ids.append(chain_id)
        _append_chain(mlt, chain_id, cut.source_path, cut.duration_frames, fps)

    _append_chain(mlt, "audio_chain", audio_path, audio_out_frame - audio_in_frame, fps, audio=True)

    _append_video_playlist(mlt, cuts, video_chain_ids, fps)
    _append_audio_playlist(mlt, audio_in_frame, audio_out_frame, fps)
    _append_tractor(mlt, video_track_length, fps)

    tree = etree.ElementTree(mlt)
    tree.write(
        str(out_path),
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=False,
    )
    return out_path


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


def _append_playlist_main_bin(mlt, cuts: list[CutPlan], audio_path: str) -> None:
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
    audio: bool = False,
) -> None:
    from lxml import etree

    out_smpte = frames_to_smpte(length_frames, fps)
    chain = etree.SubElement(
        mlt,
        "chain",
        attrib={"id": chain_id, "out": out_smpte},
    )
    _prop(chain, "length", out_smpte)
    _prop(chain, "resource", str(Path(resource).resolve()))
    if audio:
        _prop(chain, "audio_index", "0")
        _prop(chain, "video_index", "-1")
        _prop(chain, "mlt_service", "avformat-novalidate")
    else:
        _prop(chain, "mlt_service", "avformat-novalidate")


def _append_video_playlist(
    mlt,
    cuts: list[CutPlan],
    chain_ids: list[str],
    fps: FrameRate,
) -> None:
    from lxml import etree

    pl = etree.SubElement(mlt, "playlist", attrib={"id": "playlist0"})
    _prop(pl, "shotcut:video", "1")
    _prop(pl, "shotcut:name", "V1")
    for cid, cut in zip(chain_ids, cuts):
        etree.SubElement(
            pl,
            "entry",
            attrib={
                "producer": cid,
                "in": frames_to_smpte(cut.in_frame, fps),
                "out": frames_to_smpte(cut.out_frame - 1, fps),
            },
        )


def _append_audio_playlist(
    mlt,
    in_frame: int,
    out_frame: int,
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
            "producer": "audio_chain",
            "in": frames_to_smpte(in_frame, fps),
            "out": frames_to_smpte(max(in_frame, out_frame - 1), fps),
        },
    )


def _append_tractor(mlt, total_frames: int, fps: FrameRate) -> None:
    from lxml import etree

    out_smpte = frames_to_smpte(max(0, total_frames - 1), fps)
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
    etree.SubElement(multi, "track", attrib={"producer": "playlist1", "hide": "video"})


def _prop(parent, name: str, value: str) -> None:
    from lxml import etree

    p = etree.SubElement(parent, "property", attrib={"name": name})
    p.text = value
