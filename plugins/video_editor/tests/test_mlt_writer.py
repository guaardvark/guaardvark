"""Writer must emit a Shotcut-readable .mlt with the required annotations."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from plugins.video_editor.mlt.frame_math import FrameRate
from plugins.video_editor.mlt.mlt_parser import MediaAsset, ProjectProfile
from plugins.video_editor.mlt.mlt_writer import (
    CutPlan,
    plan_cuts_from_beats,
    write_project,
)


def _profile(fps: int = 30) -> ProjectProfile:
    return ProjectProfile(frame_rate=FrameRate(fps), width=1920, height=1080)


def test_plan_cuts_count_matches_intervals():
    profile = _profile(60)
    assets = [MediaAsset(producer_id="src0", resource_path="/x/a.mp4")]
    beats = [0.0, 1.0, 2.0, 3.5, 5.0]
    cuts = plan_cuts_from_beats(beats, assets, profile, seed=1)
    assert len(cuts) == len(beats) - 1


def test_plan_cuts_durations_match_drift_free_math():
    profile = _profile(60)
    assets = [MediaAsset(producer_id="src0", resource_path="/x/a.mp4")]
    beats = [0.0, 1.333, 2.666, 4.0]
    cuts = plan_cuts_from_beats(beats, assets, profile, seed=42)
    # 60fps, beats at exact 1.333 multiples — frame indices are 0, 80, 160, 240
    expected_durations = [80, 80, 80]
    assert [c.duration_frames for c in cuts] == expected_durations


def test_write_project_emits_required_annotations(tmp_path: Path):
    profile = _profile(30)
    out = tmp_path / "p.mlt"
    cuts = [
        CutPlan(source_path=str(tmp_path / "a.mp4"), in_frame=0, out_frame=30),
        CutPlan(source_path=str(tmp_path / "b.mp4"), in_frame=60, out_frame=90),
    ]
    write_project(out, cuts, str(tmp_path / "audio.wav"), profile, audio_out_seconds=10.0)

    tree = etree.parse(str(out))
    root = tree.getroot()

    # Required Shotcut annotations per research doc §"Shotcut-Specific XML Annotations".
    all_prop_names = {p.get("name") for p in root.iter("property")}
    for required in (
        "shotcut:scaleFactor",
        "shotcut:trackHeight",
        "shotcut:video",
        "shotcut:audio",
        "shotcut:name",
    ):
        assert required in all_prop_names, f"missing required Shotcut annotation: {required}"

    # main_bin playlist must exist.
    playlists = {pl.get("id"): pl for pl in root.iter("playlist")}
    assert "main_bin" in playlists
    assert "playlist0" in playlists
    assert "playlist1" in playlists

    # V1 video, A1 audio.
    v1 = playlists["playlist0"]
    a1 = playlists["playlist1"]
    assert _prop(v1, "shotcut:name") == "V1"
    assert _prop(v1, "shotcut:video") == "1"
    assert _prop(a1, "shotcut:name") == "A1"
    assert _prop(a1, "shotcut:audio") == "1"


def test_write_project_video_entries_match_cuts(tmp_path: Path):
    profile = _profile(60)
    out = tmp_path / "p.mlt"
    cuts = [
        CutPlan(source_path="/x/a.mp4", in_frame=0, out_frame=60),
        CutPlan(source_path="/x/b.mp4", in_frame=120, out_frame=240),
        CutPlan(source_path="/x/c.mp4", in_frame=300, out_frame=360),
    ]
    write_project(out, cuts, "/x/song.wav", profile, audio_out_seconds=60.0)
    tree = etree.parse(str(out))
    playlists = {pl.get("id"): pl for pl in tree.getroot().iter("playlist")}
    v1 = playlists["playlist0"]
    entries = v1.findall("entry")
    assert len(entries) == len(cuts)


def _prop(elem, name: str) -> str | None:
    for p in elem.findall("property"):
        if p.get("name") == name:
            return (p.text or "").strip()
    return None
