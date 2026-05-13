"""Drift-safe seconds → frame conversion for MLT timeline assembly.

Every conversion anchors to t=0 (absolute frame index) rather than accumulating
deltas. This is the only way to avoid sub-frame drift across thousands of cuts —
see the research doc, §"Mathematics of Frame Synchronization".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameRate:
    """Rational frame rate, e.g. (30000, 1001) for NTSC 29.97."""

    num: int
    den: int = 1

    @property
    def fps(self) -> float:
        return self.num / self.den

    @classmethod
    def from_string(cls, spec: str) -> "FrameRate":
        if "/" in spec:
            n, d = spec.split("/", 1)
            return cls(int(n), int(d))
        if "." in spec:
            f = float(spec)
            if abs(f - 29.97) < 0.005:
                return cls(30000, 1001)
            if abs(f - 23.976) < 0.005:
                return cls(24000, 1001)
            if abs(f - 59.94) < 0.005:
                return cls(60000, 1001)
            return cls(int(round(f)))
        return cls(int(spec))


def seconds_to_absolute_frame(t_seconds: float, fps: FrameRate) -> int:
    """Convert an absolute timestamp to the nearest absolute frame index.

    Uses banker's-style nearest-integer rounding to emulate MLT's internal
    lrint behavior. Anchored to t=0, so calling this for any timestamp in a
    sequence never compounds error from prior calls.
    """
    return int(round(t_seconds * fps.num / fps.den))


def durations_from_absolute_beats(
    beat_times_seconds: list[float], fps: FrameRate
) -> list[int]:
    """Translate a list of absolute beat timestamps to per-clip durations in frames.

    Each duration is the difference between two ABSOLUTE frame indices — never
    the difference between two seconds-deltas. This guarantees that the Nth
    cut lands on the Nth beat regardless of how many cuts came before it.
    """
    if len(beat_times_seconds) < 2:
        return []
    absolute_frames = [seconds_to_absolute_frame(t, fps) for t in beat_times_seconds]
    return [absolute_frames[i + 1] - absolute_frames[i] for i in range(len(absolute_frames) - 1)]


def frames_to_smpte(frame_idx: int, fps: FrameRate) -> str:
    """Format an absolute frame index as 'HH:MM:SS.mmm'.

    Shotcut accepts both HH:MM:SS:FF and HH:MM:SS.mmm; the latter sidesteps
    drop-frame ambiguity on fractional rates like 29.97 and 23.976.
    """
    if frame_idx < 0:
        raise ValueError(f"frame_idx must be >= 0, got {frame_idx}")
    total_seconds = frame_idx * fps.den / fps.num
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def smpte_to_frames(smpte: str, fps: FrameRate) -> int:
    """Inverse of frames_to_smpte, for round-trip use."""
    h, m, s = smpte.split(":")
    total_seconds = int(h) * 3600 + int(m) * 60 + float(s)
    return seconds_to_absolute_frame(total_seconds, fps)
