"""Tests for the shared kdenlive cut-list parser (mlt.kdenlive)."""

from __future__ import annotations

from mlt.kdenlive import parse_kept_ranges, smpte_to_seconds


# auto-editor's kdenlive export mirrors every kept segment onto a video AND an
# audio track. This fixture reproduces that (4 entries, out of order) for the
# tone(0-2.1) / silence / tone(3.9-6.0) case — it must collapse to 2 clips.
_MIRRORED_TRACKS = """<?xml version="1.0"?>
<mlt>
  <playlist id="playlist0">
    <entry in="00:00:03.900" out="00:00:06.000"/>
    <entry in="00:00:00.000" out="00:00:02.100"/>
  </playlist>
  <playlist id="playlist1_audio">
    <entry in="00:00:00.000" out="00:00:02.100"/>
    <entry in="00:00:03.900" out="00:00:06.000"/>
  </playlist>
</mlt>
"""


def test_mirrored_entries_dedupe_to_two_clips(tmp_path):
    p = tmp_path / "clip.kdenlive"
    p.write_text(_MIRRORED_TRACKS)
    ranges = parse_kept_ranges(p)
    # Was 4 (one per <entry>) before the dedup fix; must now be 2.
    assert ranges == [(0.0, 2.1), (3.9, 6.0)]


def test_sorted_chronologically(tmp_path):
    p = tmp_path / "clip.kdenlive"
    p.write_text(_MIRRORED_TRACKS)  # entries appear out of order on track 0
    ranges = parse_kept_ranges(p)
    assert ranges == sorted(ranges, key=lambda r: r[0])


def test_zero_and_negative_length_entries_dropped(tmp_path):
    p = tmp_path / "clip.kdenlive"
    p.write_text(
        """<?xml version="1.0"?>
<mlt><playlist id="p0">
  <entry in="00:00:01.000" out="00:00:01.000"/>
  <entry in="00:00:05.000" out="00:00:02.000"/>
  <entry in="00:00:00.000" out="00:00:01.500"/>
</playlist></mlt>
"""
    )
    assert parse_kept_ranges(p) == [(0.0, 1.5)]


def test_missing_or_bad_file_returns_empty(tmp_path):
    assert parse_kept_ranges(tmp_path / "does_not_exist.kdenlive") == []
    bad = tmp_path / "bad.kdenlive"
    bad.write_text("<mlt><not-closed>")
    assert parse_kept_ranges(bad) == []


def test_smpte_to_seconds_variants():
    assert smpte_to_seconds("00:00:02.100") == 2.1
    assert smpte_to_seconds("01:02:03.000") == 3723.0
    assert smpte_to_seconds("4.5") == 4.5
    assert smpte_to_seconds(None) is None
    assert smpte_to_seconds("garbage") is None
