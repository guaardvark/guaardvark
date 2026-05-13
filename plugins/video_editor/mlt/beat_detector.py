"""Librosa-based beat and onset extraction.

Stage 2 of the pipeline. Owns everything between "raw audio file" and "list of
absolute beat timestamps suitable for cutting video against".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BeatAnalysis:
    tempo_bpm: float
    beat_times: list[float]
    onset_envelope: Optional[list[float]] = field(default=None)
    onset_times: Optional[list[float]] = field(default=None)
    duration_seconds: float = 0.0


@dataclass
class BeatFilterParams:
    """Aesthetic / pacing filters on top of raw beat detection."""

    subdivision: int = 1
    """Keep every Nth beat (2 = every other, 4 = downbeat of each measure)."""

    min_clip_seconds: float = 1.2
    """If two consecutive kept beats are closer than this, drop the second."""

    tightness: int = 100
    """librosa beat_track tightness — higher = more rigid grid."""

    use_onset_envelope: bool = False
    """Replace beat list with raw onset detection (for chaotic / drop-heavy edits)."""


def detect_beats(
    audio_path: str,
    params: Optional[BeatFilterParams] = None,
) -> BeatAnalysis:
    """Analyze an audio file and return a filtered list of beat timestamps."""
    import librosa
    import numpy as np

    params = params or BeatFilterParams()

    y, sr = librosa.load(audio_path, sr=None, mono=True)
    duration = float(len(y)) / float(sr)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)

    if params.use_onset_envelope:
        onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)
        raw_times = librosa.frames_to_time(onset_frames, sr=sr).tolist()
        tempo = 0.0
    else:
        tempo, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset_env,
            sr=sr,
            tightness=params.tightness,
            trim=True,
            units="frames",
        )
        raw_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        tempo = float(np.asarray(tempo).reshape(-1)[0]) if hasattr(tempo, "__len__") else float(tempo)

    filtered = _apply_filters(raw_times, params)

    onset_times = librosa.frames_to_time(
        np.arange(len(onset_env)), sr=sr
    ).tolist()

    return BeatAnalysis(
        tempo_bpm=tempo,
        beat_times=filtered,
        onset_envelope=onset_env.tolist(),
        onset_times=onset_times,
        duration_seconds=duration,
    )


def _apply_filters(times: list[float], params: BeatFilterParams) -> list[float]:
    if not times:
        return []

    if params.subdivision > 1:
        times = times[:: params.subdivision]

    out: list[float] = [times[0]]
    for t in times[1:]:
        if t - out[-1] >= params.min_clip_seconds:
            out.append(t)
    return out
