"""Parser must extract main_bin entries and the project profile from a Shotcut .mlt."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.video_editor.mlt.mlt_parser import parse_template


@pytest.fixture
def sample_mlt(tmp_path: Path) -> Path:
    content = """<?xml version="1.0" standalone="no"?>
<mlt LC_NUMERIC="C" version="7.24.0" producer="main_bin">
  <profile description="HD 1080p 60 fps" width="1920" height="1080"
           progressive="1" sample_aspect_num="1" sample_aspect_den="1"
           display_aspect_num="16" display_aspect_den="9"
           frame_rate_num="60" frame_rate_den="1" colorspace="709"/>
  <chain id="chain0" out="00:00:10.000">
    <property name="length">00:00:10.000</property>
    <property name="resource">/abs/path/clip_a.mp4</property>
  </chain>
  <chain id="chain1" out="00:00:08.500">
    <property name="length">00:00:08.500</property>
    <property name="resource">/abs/path/clip_b.mp4</property>
  </chain>
  <producer id="prod_audio" out="00:03:14.000">
    <property name="length">00:03:14.000</property>
    <property name="resource">/abs/path/song.wav</property>
  </producer>
  <playlist id="main_bin">
    <entry producer="chain0"/>
    <entry producer="chain1"/>
    <entry producer="prod_audio"/>
  </playlist>
</mlt>
"""
    path = tmp_path / "sample.mlt"
    path.write_text(content)
    return path


def test_parses_profile(sample_mlt: Path):
    parsed = parse_template(sample_mlt)
    assert parsed.profile.frame_rate.num == 60
    assert parsed.profile.frame_rate.den == 1
    assert parsed.profile.width == 1920
    assert parsed.profile.height == 1080


def test_resolves_main_bin_paths(sample_mlt: Path):
    parsed = parse_template(sample_mlt)
    paths = [a.resource_path for a in parsed.main_bin]
    assert "/abs/path/clip_a.mp4" in paths
    assert "/abs/path/clip_b.mp4" in paths
    assert "/abs/path/song.wav" in paths


def test_flags_audio_assets(sample_mlt: Path):
    parsed = parse_template(sample_mlt)
    by_path = {a.resource_path: a for a in parsed.main_bin}
    assert by_path["/abs/path/song.wav"].is_audio is True
    assert by_path["/abs/path/clip_a.mp4"].is_audio is False


def test_missing_main_bin_returns_empty(tmp_path: Path):
    minimal = tmp_path / "minimal.mlt"
    minimal.write_text(
        '<?xml version="1.0"?><mlt version="7.24.0">'
        '<profile frame_rate_num="30" frame_rate_den="1"/>'
        "</mlt>"
    )
    parsed = parse_template(minimal)
    assert parsed.main_bin == []
