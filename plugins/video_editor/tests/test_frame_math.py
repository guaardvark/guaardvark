"""Drift-prevention tests — the core invariant of the pipeline.

The doc warns that naive delta-accumulation will silently desync video and
audio after thousands of cuts. These tests prove the writer never accumulates.
"""

from __future__ import annotations

from plugins.video_editor.mlt.frame_math import (
    FrameRate,
    durations_from_absolute_beats,
    frames_to_smpte,
    seconds_to_absolute_frame,
    smpte_to_frames,
)


def test_60fps_round_to_nearest():
    fps = FrameRate(60)
    assert seconds_to_absolute_frame(1.333, fps) == 80
    assert seconds_to_absolute_frame(0.0, fps) == 0
    assert seconds_to_absolute_frame(1.0, fps) == 60


def test_ntsc_2997_fractional_rate():
    fps = FrameRate(30000, 1001)
    # 1.0s on NTSC ≈ 29.97 frames → rounds to 30
    assert seconds_to_absolute_frame(1.0, fps) == 30
    # 10s ≈ 299.7 → 300
    assert seconds_to_absolute_frame(10.0, fps) == 300


def test_no_drift_over_200_cuts_60fps():
    """200 cuts spaced 1.333s apart on 60fps must accumulate zero drift."""
    fps = FrameRate(60)
    spacing = 1.333
    beats = [i * spacing for i in range(201)]  # 200 intervals

    durations = durations_from_absolute_beats(beats, fps)
    assert len(durations) == 200

    # Verify cumulative sum lands on the absolute frame of the final beat.
    final_absolute = seconds_to_absolute_frame(beats[-1], fps)
    assert sum(durations) == final_absolute, (
        f"Drift detected: sum(durations)={sum(durations)} but final_absolute={final_absolute}"
    )


def test_no_drift_over_200_cuts_ntsc():
    fps = FrameRate(30000, 1001)
    spacing = 1.333
    beats = [i * spacing for i in range(201)]

    durations = durations_from_absolute_beats(beats, fps)
    final_absolute = seconds_to_absolute_frame(beats[-1], fps)
    assert sum(durations) == final_absolute


def test_irregular_beats_no_drift():
    """Real beat times aren't evenly spaced — drift must remain zero anyway."""
    fps = FrameRate(60)
    beats = [0.0, 0.51, 0.99, 1.47, 2.03, 2.49, 3.01, 3.55, 4.02]
    durations = durations_from_absolute_beats(beats, fps)
    assert sum(durations) == seconds_to_absolute_frame(beats[-1], fps)


def test_smpte_round_trip():
    fps = FrameRate(60)
    for frame in (0, 1, 59, 60, 3599, 3600, 216000):
        smpte = frames_to_smpte(frame, fps)
        assert smpte_to_frames(smpte, fps) == frame, f"round-trip failed for frame {frame}"


def test_smpte_format_three_decimals():
    fps = FrameRate(60)
    assert frames_to_smpte(0, fps) == "00:00:00.000"
    assert frames_to_smpte(60, fps) == "00:00:01.000"
    # 30 frames @ 60fps = 0.5s
    assert frames_to_smpte(30, fps) == "00:00:00.500"


def test_short_input_returns_empty():
    fps = FrameRate(60)
    assert durations_from_absolute_beats([], fps) == []
    assert durations_from_absolute_beats([0.0], fps) == []
