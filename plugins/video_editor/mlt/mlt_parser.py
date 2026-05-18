"""Stage 1 — parse a Shotcut template.mlt and extract the media bin.

The user opens Shotcut, drops media into the Playlist panel, saves the project.
We read that project to harvest the absolute paths and the project profile,
sidestepping any need to probe video metadata ourselves with FFmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .frame_math import FrameRate


@dataclass
class MediaAsset:
    producer_id: str
    resource_path: str
    length_smpte: Optional[str] = None
    is_audio: bool = False


@dataclass
class ProjectProfile:
    frame_rate: FrameRate
    width: int = 1920
    height: int = 1080
    sample_aspect_num: int = 1
    sample_aspect_den: int = 1


@dataclass
class ParsedTemplate:
    profile: ProjectProfile
    main_bin: list[MediaAsset]
    template_path: Path


_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".aac", ".m4a", ".opus"}


def parse_template(template_path: str | Path) -> ParsedTemplate:
    """Parse a Shotcut .mlt project file and return its profile + main_bin."""
    from lxml import etree

    path = Path(template_path)
    tree = etree.parse(str(path))
    root = tree.getroot()

    profile = _parse_profile(root)
    producers = _index_producers(root)
    main_bin = _harvest_main_bin(root, producers)

    return ParsedTemplate(profile=profile, main_bin=main_bin, template_path=path)


def _parse_profile(root) -> ProjectProfile:
    prof = root.find("profile")
    if prof is None:
        return ProjectProfile(frame_rate=FrameRate(30))

    fps_num = int(prof.get("frame_rate_num", "30"))
    fps_den = int(prof.get("frame_rate_den", "1"))
    width = int(prof.get("width", "1920"))
    height = int(prof.get("height", "1080"))
    sar_num = int(prof.get("sample_aspect_num", "1"))
    sar_den = int(prof.get("sample_aspect_den", "1"))
    return ProjectProfile(
        frame_rate=FrameRate(fps_num, fps_den),
        width=width,
        height=height,
        sample_aspect_num=sar_num,
        sample_aspect_den=sar_den,
    )


def _index_producers(root) -> dict[str, MediaAsset]:
    """Map producer/chain IDs to MediaAsset entries by walking the XML."""
    producers: dict[str, MediaAsset] = {}
    for elem in list(root.iter("producer")) + list(root.iter("chain")):
        pid = elem.get("id")
        if not pid:
            continue
        resource = _property(elem, "resource")
        length = _property(elem, "length")
        if not resource:
            continue
        ext = Path(resource).suffix.lower()
        producers[pid] = MediaAsset(
            producer_id=pid,
            resource_path=str(Path(resource).expanduser()),
            length_smpte=length,
            is_audio=ext in _AUDIO_EXTS,
        )
    return producers


def _harvest_main_bin(root, producers: dict[str, MediaAsset]) -> list[MediaAsset]:
    """Find <playlist id="main_bin"> and resolve its entries to MediaAssets."""
    bin_pl = None
    for pl in root.iter("playlist"):
        if pl.get("id") == "main_bin":
            bin_pl = pl
            break
    if bin_pl is None:
        return []

    assets: list[MediaAsset] = []
    for entry in bin_pl.iter("entry"):
        pid = entry.get("producer")
        if pid and pid in producers:
            assets.append(producers[pid])
    return assets


def _property(elem, name: str) -> Optional[str]:
    for prop in elem.findall("property"):
        if prop.get("name") == name:
            return (prop.text or "").strip()
    return None
