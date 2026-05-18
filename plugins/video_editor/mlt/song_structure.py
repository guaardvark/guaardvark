"""Song structure analysis: beats + section segmentation.

We pair librosa.beat.beat_track with a simple energy-curve segmentation:
divide the song into N sections, label by energy ('intro', 'build', 'drop',
'outro'), expose to the arranger.

Section detection uses the onset envelope's median energy by segment. We don't
attempt full music-structure analysis (MSAF / Foote novelty etc.) for v1 —
4 sections is a useful enough scaffold for clip placement bias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SECTION_COUNT = 4


@dataclass
class SongSection:
    label: str               # intro | build | drop | outro | unlabeled
    start: float             # seconds
    end: float
    mean_energy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "mean_energy": self.mean_energy,
        }


@dataclass
class SongStructure:
    tempo_bpm: float
    duration_seconds: float
    beat_times: list[float] = field(default_factory=list)
    sections: list[SongSection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tempo_bpm": self.tempo_bpm,
            "duration_seconds": self.duration_seconds,
            "beat_times": self.beat_times,
            "sections": [s.to_dict() for s in self.sections],
        }


def analyze_song(
    audio_path: str | Path,
    *,
    section_count: int = DEFAULT_SECTION_COUNT,
    tightness: int = 100,
) -> SongStructure:
    """Extract tempo, beats, and labeled sections from an audio file."""
    import librosa
    import numpy as np

    path = Path(audio_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"audio not found: {path}")

    y, sr = librosa.load(str(path), sr=None, mono=True)
    duration = float(len(y)) / float(sr)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=sr,
        tightness=tightness,
        trim=True,
        units="frames",
    )
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    if hasattr(tempo, "__len__"):
        tempo = float(np.asarray(tempo).reshape(-1)[0])
    else:
        tempo = float(tempo)

    sections = _segment_by_energy(onset_env, sr, duration, section_count)

    return SongStructure(
        tempo_bpm=tempo,
        duration_seconds=duration,
        beat_times=beat_times,
        sections=sections,
    )


def _segment_by_energy(
    onset_env, sr: int, duration: float, n: int
) -> list[SongSection]:
    """Split the song into N equal-time slices, label by their mean energy.

    Labels are assigned based on the rank-position of each section's energy:
    lowest-energy slices become intro/outro depending on position, highest
    become drop, middling become build. v1 heuristic — good enough as a bias
    signal for the arranger, not a music-theory claim.
    """
    import librosa
    import numpy as np

    if n < 2:
        sections = [SongSection(label="unlabeled", start=0.0, end=duration, mean_energy=float(np.mean(onset_env)))]
        _label_sections(sections)
        return sections

    env_times = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr)
    slice_dur = duration / n
    sections: list[SongSection] = []
    for i in range(n):
        start = i * slice_dur
        end = duration if i == n - 1 else (i + 1) * slice_dur
        mask = (env_times >= start) & (env_times < end)
        mean_energy = float(np.mean(onset_env[mask])) if mask.any() else 0.0
        sections.append(SongSection(label="unlabeled", start=start, end=end, mean_energy=mean_energy))

    _label_sections(sections)
    return sections


def _label_sections(sections: list[SongSection]) -> None:
    """Assign intro/build/drop/outro based on position + energy ranking."""
    if not sections:
        return
    if len(sections) == 1:
        sections[0].label = "drop"
        return

    n = len(sections)
    # Position-first heuristic: the first is intro, the last is outro.
    sections[0].label = "intro"
    sections[-1].label = "outro"

    # Middle sections: the highest-energy is the drop, others are build.
    middles = sections[1:-1]
    if not middles:
        return
    max_energy = max(s.mean_energy for s in middles)
    for s in middles:
        s.label = "drop" if s.mean_energy >= max_energy else "build"
