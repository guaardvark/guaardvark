"""Multi-clip compose_arrangement tests."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from mlt.frame_math import FrameRate
from mlt.mlt_parser import ProjectProfile
from mlt.timeline_compose import compose_arrangement


def _profile(fps: int = 30) -> ProjectProfile:
    return ProjectProfile(frame_rate=FrameRate(fps), width=1280, height=720)


def _arrangement_three_clips(transition_to_next: str = "hard-cut", filter_preset: str = "none") -> list[dict]:
    """Three clips back-to-back, 3 seconds each, all on the same source for simplicity."""
    return [
        {"clip_id": "c0", "source_path": "/x/a.mp4", "section_label": "intro",
         "timeline_start": 0.0, "timeline_end": 3.0,
         "source_in": 0.0, "source_out": 3.0,
         "filter_preset": filter_preset, "transition_to_next": transition_to_next},
        {"clip_id": "c1", "source_path": "/x/b.mp4", "section_label": "drop",
         "timeline_start": 3.0, "timeline_end": 6.0,
         "source_in": 0.0, "source_out": 3.0,
         "filter_preset": "none", "transition_to_next": transition_to_next},
        {"clip_id": "c2", "source_path": "/x/c.mp4", "section_label": "outro",
         "timeline_start": 6.0, "timeline_end": 9.0,
         "source_in": 0.0, "source_out": 3.0,
         "filter_preset": "none", "transition_to_next": "hard-cut"},
    ]


def test_compose_hard_cuts_single_track(tmp_path: Path):
    out = tmp_path / "p.mlt"
    compose_arrangement(_arrangement_three_clips("hard-cut"), audio_path=None,
                        output_path=out, profile=_profile())
    root = etree.parse(str(out)).getroot()
    playlists = {pl.get("id") for pl in root.iter("playlist")}
    # Only V1 + main_bin (no V2 because hard-cuts only)
    assert "playlist0" in playlists
    assert "playlist1_v2" not in playlists
    # 3 entries on V1
    v1 = next(p for p in root.iter("playlist") if p.get("id") == "playlist0")
    assert len(v1.findall("entry")) == 3
    # No transition elements
    assert root.findall(".//transition") == []


def test_compose_cross_dissolve_alternates_v1_v2(tmp_path: Path):
    out = tmp_path / "p.mlt"
    compose_arrangement(_arrangement_three_clips("cross-dissolve"), audio_path=None,
                        output_path=out, profile=_profile())
    root = etree.parse(str(out)).getroot()
    playlists = {pl.get("id") for pl in root.iter("playlist")}
    assert "playlist0" in playlists
    assert "playlist1_v2" in playlists
    # Alternation: c0→V1, c1→V2, c2→V1
    v1 = next(p for p in root.iter("playlist") if p.get("id") == "playlist0")
    v2 = next(p for p in root.iter("playlist") if p.get("id") == "playlist1_v2")
    assert len(v1.findall("entry")) == 2  # c0 + c2
    assert len(v2.findall("entry")) == 1  # c1
    # Two cross-dissolve transitions emitted
    trans = root.findall(".//transition")
    assert len(trans) == 2
    for t in trans:
        services = [p.text for p in t.findall("property") if p.get("name") == "mlt_service"]
        assert "luma" in services


def test_compose_attaches_filter_to_chain(tmp_path: Path):
    out = tmp_path / "p.mlt"
    arr = _arrangement_three_clips("hard-cut", filter_preset="sepia")
    compose_arrangement(arr, audio_path=None, output_path=out, profile=_profile())
    root = etree.parse(str(out)).getroot()
    # c0's source is /x/a.mp4, so its chain should have a sepia filter
    chains_with_sepia = []
    for chain in root.iter("chain"):
        for f in chain.findall("filter"):
            for p in f.findall("property"):
                if p.get("name") == "mlt_service" and p.text == "sepia":
                    chains_with_sepia.append(chain.get("id"))
    assert len(chains_with_sepia) == 1


def test_compose_audio_chain_present_when_audio_path_given(tmp_path: Path):
    out = tmp_path / "p.mlt"
    compose_arrangement(
        _arrangement_three_clips("hard-cut"),
        audio_path=str(tmp_path / "song.wav"),
        output_path=out, profile=_profile(),
        song_duration_seconds=10.0,
    )
    root = etree.parse(str(out)).getroot()
    # Audio chain exists
    audio_chains = [c for c in root.iter("chain") if c.get("id") == "chain_audio"]
    assert len(audio_chains) == 1
    # Audio playlist exists
    audio_pls = [p for p in root.iter("playlist") if p.get("id") == "playlist_audio"]
    assert len(audio_pls) == 1


def test_compose_required_shotcut_annotations(tmp_path: Path):
    out = tmp_path / "p.mlt"
    compose_arrangement(_arrangement_three_clips("cross-dissolve"), audio_path=None,
                        output_path=out, profile=_profile())
    root = etree.parse(str(out)).getroot()
    prop_names = {p.get("name") for p in root.iter("property")}
    for required in ("shotcut:scaleFactor", "shotcut:trackHeight", "shotcut:video", "shotcut:name"):
        assert required in prop_names, f"missing {required}"


def test_compose_empty_arrangement_raises(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        compose_arrangement([], audio_path=None, output_path=tmp_path / "p.mlt", profile=_profile())


def test_compose_reuses_chain_for_repeated_source(tmp_path: Path):
    """Same source path → single chain, even if it appears twice in arrangement."""
    out = tmp_path / "p.mlt"
    arr = [
        {"clip_id": "c0", "source_path": "/x/dup.mp4", "section_label": "intro",
         "timeline_start": 0.0, "timeline_end": 3.0,
         "source_in": 0.0, "source_out": 3.0,
         "filter_preset": "none", "transition_to_next": "hard-cut"},
        {"clip_id": "c1", "source_path": "/x/dup.mp4", "section_label": "outro",
         "timeline_start": 3.0, "timeline_end": 6.0,
         "source_in": 5.0, "source_out": 8.0,
         "filter_preset": "none", "transition_to_next": "hard-cut"},
    ]
    compose_arrangement(arr, audio_path=None, output_path=out, profile=_profile())
    root = etree.parse(str(out)).getroot()
    chains = [c for c in root.iter("chain") if c.get("id") != "chain_audio"]
    assert len(chains) == 1  # deduped
