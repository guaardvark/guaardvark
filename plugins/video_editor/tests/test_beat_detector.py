"""Beat detector — synthesized click track must yield the expected tempo."""

from __future__ import annotations

from pathlib import Path

import pytest

librosa = pytest.importorskip("librosa")
import numpy as np
import soundfile as sf

from plugins.video_editor.mlt.beat_detector import BeatFilterParams, detect_beats


def _synth_click_track(path: Path, bpm: float, duration_s: float, sr: int = 22050) -> None:
    """Write a click track at the given BPM as a WAV file."""
    n_samples = int(duration_s * sr)
    audio = np.zeros(n_samples, dtype=np.float32)
    interval_samples = int(round(60.0 / bpm * sr))
    # 5 ms click envelope
    click_len = int(0.005 * sr)
    click = np.linspace(1.0, 0.0, click_len, dtype=np.float32)
    for start in range(0, n_samples - click_len, interval_samples):
        audio[start : start + click_len] += click
    sf.write(str(path), audio, sr)


def test_120bpm_click_yields_120bpm(tmp_path: Path):
    wav = tmp_path / "click_120.wav"
    _synth_click_track(wav, bpm=120.0, duration_s=15.0)

    analysis = detect_beats(str(wav), BeatFilterParams(subdivision=1, min_clip_seconds=0.0))

    # librosa estimates tempo by autocorrelation — allow a wide tolerance because
    # half-time / double-time aliasing is well-known. Accept 60, 120, or 240.
    assert any(abs(analysis.tempo_bpm - candidate) < 5.0 for candidate in (60.0, 120.0, 240.0)), (
        f"tempo={analysis.tempo_bpm} not near 60/120/240"
    )

    assert len(analysis.beat_times) > 10, "expected many beats in a 15s 120bpm track"

    # Inter-beat intervals should cluster around 0.5s (120 BPM) or its multiples.
    diffs = np.diff(analysis.beat_times)
    median = float(np.median(diffs))
    assert any(abs(median - candidate) < 0.05 for candidate in (0.25, 0.5, 1.0)), (
        f"median inter-beat={median:.3f}s, expected near 0.25 / 0.5 / 1.0"
    )


def test_subdivision_filter_halves_beats(tmp_path: Path):
    wav = tmp_path / "click_120_sub.wav"
    _synth_click_track(wav, bpm=120.0, duration_s=10.0)

    full = detect_beats(str(wav), BeatFilterParams(subdivision=1, min_clip_seconds=0.0))
    half = detect_beats(str(wav), BeatFilterParams(subdivision=2, min_clip_seconds=0.0))

    # Subdivision=2 keeps every other beat — count must be ≈ half.
    assert len(half.beat_times) == (len(full.beat_times) + 1) // 2 or len(half.beat_times) == len(full.beat_times) // 2


def test_min_clip_seconds_enforces_spacing(tmp_path: Path):
    wav = tmp_path / "click_120_min.wav"
    _synth_click_track(wav, bpm=120.0, duration_s=10.0)

    filtered = detect_beats(str(wav), BeatFilterParams(subdivision=1, min_clip_seconds=1.5))
    diffs = np.diff(filtered.beat_times)
    assert all(d >= 1.5 - 1e-6 for d in diffs), (
        f"min_clip_seconds violated: smallest gap={min(diffs) if len(diffs) else 'n/a'}"
    )
