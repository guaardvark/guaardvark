"""Tests for librosa-based song structure analysis."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

soundfile = pytest.importorskip("soundfile")
import soundfile as sf

from mlt.song_structure import analyze_song


def _write_synthetic_song(path: Path, bpm: float = 120.0, duration: float = 12.0, sr: int = 22050) -> None:
    """Build a 4-section song: quiet, building, drop, fade."""
    n = int(sr * duration)
    audio = np.zeros(n, dtype=np.float32)

    section_len = n // 4
    # intro — sparse hits at 120 BPM, quiet
    interval = int(round(60 / bpm * sr))
    for i in range(0, section_len, interval):
        audio[i:i+200] = 0.1
    # build — more hits, louder
    for i in range(section_len, 2 * section_len, interval // 2):
        audio[i:i+400] = 0.3
    # drop — densest, loudest (uses noise burst)
    audio[2*section_len:3*section_len] = 0.5 * np.random.RandomState(0).randn(section_len).astype(np.float32)
    # outro — sparse + quiet again
    for i in range(3 * section_len, n, interval):
        audio[i:i+200] = 0.1

    sf.write(str(path), audio, sr)


def test_analyze_song_returns_expected_shape(tmp_path: Path):
    song_path = tmp_path / "song.wav"
    _write_synthetic_song(song_path)
    s = analyze_song(song_path, section_count=4)
    assert s.duration_seconds > 0
    assert len(s.sections) == 4
    assert s.sections[0].label == "intro"
    assert s.sections[-1].label == "outro"
    # exactly one drop
    drops = [sec for sec in s.sections if sec.label == "drop"]
    assert len(drops) == 1
    # drop should be the highest-energy section in the middle
    middle_energies = [sec.mean_energy for sec in s.sections[1:-1]]
    assert drops[0].mean_energy == max(middle_energies)


def test_analyze_song_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        analyze_song("/nonexistent/song.wav")


def test_section_count_one_yields_drop_label(tmp_path: Path):
    song_path = tmp_path / "song.wav"
    _write_synthetic_song(song_path, duration=4.0)
    s = analyze_song(song_path, section_count=1)
    assert len(s.sections) == 1
    assert s.sections[0].label == "drop"
