"""timeline_compose tests — JSON contract from VideoEditorPage → .mlt."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from plugins.video_editor.mlt.frame_math import FrameRate
from plugins.video_editor.mlt.mlt_parser import ProjectProfile
from plugins.video_editor.mlt.timeline_compose import (
    TextElement,
    Timeline,
    compose_timeline,
    timeline_from_payload,
)


def _profile() -> ProjectProfile:
    return ProjectProfile(frame_rate=FrameRate(30), width=1920, height=1080)


def test_payload_round_trip_matches_frontend_keys():
    """The frontend sends camelCase keys for text_elements; compose accepts them."""
    payload = {
        "video_path": "/x/v.mp4",
        "audio_path": "/x/a.wav",
        "video_trim_start": 1.0,
        "video_trim_end": 5.0,
        "audio_volume": 0.8,
        "text_elements": [
            {
                "text": "Hello",
                "fontSize": 64,
                "fontColor": "#ff0000",
                "x": 100,
                "y": 200,
                "rotation": 0,
                "startSeconds": 0.5,
                "endSeconds": 2.5,
            }
        ],
    }
    t = timeline_from_payload(payload)
    assert t.video_path == "/x/v.mp4"
    assert t.audio_path == "/x/a.wav"
    assert t.video_trim_start == 1.0
    assert t.video_trim_end == 5.0
    assert t.audio_volume == 0.8
    assert len(t.text_elements) == 1
    e = t.text_elements[0]
    assert e.text == "Hello" and e.font_size == 64 and e.font_color == "#ff0000"
    assert e.start_seconds == 0.5 and e.end_seconds == 2.5


def test_compose_video_only(tmp_path: Path):
    out = tmp_path / "p.mlt"
    timeline = Timeline(video_path=str(tmp_path / "src.mp4"), video_trim_end=4.0)
    compose_timeline(timeline, out, _profile())
    root = etree.parse(str(out)).getroot()

    chains = [c.get("id") for c in root.iter("chain")]
    assert "chain_video" in chains
    assert "chain_audio" not in chains
    playlists = {p.get("id") for p in root.iter("playlist")}
    assert {"main_bin", "playlist0"} <= playlists
    assert "playlist1" not in playlists


def test_compose_with_audio_and_volume(tmp_path: Path):
    out = tmp_path / "p.mlt"
    timeline = Timeline(
        video_path=str(tmp_path / "src.mp4"),
        audio_path=str(tmp_path / "song.wav"),
        video_trim_end=4.0,
        audio_volume=0.5,
    )
    compose_timeline(timeline, out, _profile())
    root = etree.parse(str(out)).getroot()

    chain_audio = next(c for c in root.iter("chain") if c.get("id") == "chain_audio")
    # volume filter must be attached when volume != 1.0
    filters = list(chain_audio.iter("filter"))
    assert any(_prop(f, "mlt_service") == "volume" for f in filters)
    vol_filter = next(f for f in filters if _prop(f, "mlt_service") == "volume")
    assert _prop(vol_filter, "gain") == "0.500"


def test_compose_text_overlay_filter_attached(tmp_path: Path):
    out = tmp_path / "p.mlt"
    timeline = Timeline(
        video_path=str(tmp_path / "src.mp4"),
        video_trim_end=10.0,
        text_elements=[
            TextElement(text="Hi", font_size=72, font_color="#00ff00",
                        x=50, y=80, start_seconds=1.0, end_seconds=5.0)
        ],
    )
    compose_timeline(timeline, out, _profile())
    root = etree.parse(str(out)).getroot()

    chain = next(c for c in root.iter("chain") if c.get("id") == "chain_video")
    text_filters = [f for f in chain.iter("filter") if _prop(f, "mlt_service") == "dynamictext"]
    assert len(text_filters) == 1
    f = text_filters[0]
    assert _prop(f, "argument") == "Hi"
    assert _prop(f, "size") == "72"
    assert _prop(f, "fgcolour") == "0x00ff00ff"
    # Time range: 1.0 → 5.0 at 30fps = frames 30 → 150
    assert f.get("in") == "00:00:01.000"
    assert f.get("out") == "00:00:04.967"  # 149 frames at 30fps


def test_color_normalization():
    from plugins.video_editor.mlt.timeline_compose import _normalize_color

    assert _normalize_color("#ffffff") == "0xffffffff"
    assert _normalize_color("#FF0000") == "0xff0000ff"
    assert _normalize_color("white") == "0xffffffff"
    assert _normalize_color("black") == "0x000000ff"
    assert _normalize_color("#aabbccdd") == "0xaabbccdd"
    assert _normalize_color("") == "0xffffffff"
    assert _normalize_color("unknown_name") == "0xffffffff"  # fallback


def test_compose_drops_zero_duration_text(tmp_path: Path):
    """end <= start → no filter emitted."""
    out = tmp_path / "p.mlt"
    timeline = Timeline(
        video_path=str(tmp_path / "src.mp4"),
        video_trim_end=10.0,
        text_elements=[
            TextElement(text="Visible", start_seconds=0.0, end_seconds=2.0),
            TextElement(text="Invisible", start_seconds=5.0, end_seconds=5.0),
            TextElement(text="Reversed", start_seconds=8.0, end_seconds=3.0),
        ],
    )
    compose_timeline(timeline, out, _profile())
    root = etree.parse(str(out)).getroot()
    chain = next(c for c in root.iter("chain") if c.get("id") == "chain_video")
    text_filters = [f for f in chain.iter("filter") if _prop(f, "mlt_service") == "dynamictext"]
    assert len(text_filters) == 1
    assert _prop(text_filters[0], "argument") == "Visible"


def test_compose_preserves_required_shotcut_annotations(tmp_path: Path):
    out = tmp_path / "p.mlt"
    timeline = Timeline(video_path=str(tmp_path / "src.mp4"), video_trim_end=2.0)
    compose_timeline(timeline, out, _profile())
    root = etree.parse(str(out)).getroot()

    all_prop_names = {p.get("name") for p in root.iter("property")}
    for required in (
        "shotcut:scaleFactor",
        "shotcut:trackHeight",
        "shotcut:video",
        "shotcut:name",
    ):
        assert required in all_prop_names, f"missing {required}"


def _prop(elem, name: str) -> str | None:
    for p in elem.findall("property"):
        if p.get("name") == name:
            return (p.text or "").strip()
    return None
