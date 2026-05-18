"""Tests for the multi-modal auto-editor wrapper."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mlt import analyze


def _have_auto_editor() -> bool:
    if shutil.which("auto-editor"):
        return True
    venv_bin = Path(__file__).resolve().parent.parent / "venv" / "bin" / "auto-editor"
    return venv_bin.is_file()


def _have_voice_sim() -> bool:
    return Path("/tmp/beat_sync_smoke/voice_sim.mp4").exists()


pytestmark = pytest.mark.skipif(
    not (_have_auto_editor() and _have_voice_sim()),
    reason="needs auto-editor binary and the /tmp/beat_sync_smoke/voice_sim.mp4 fixture",
)


@pytest.fixture
def ae_path() -> str:
    venv_bin = Path(__file__).resolve().parent.parent / "venv" / "bin" / "auto-editor"
    return str(venv_bin) if venv_bin.is_file() else "auto-editor"


def test_build_edit_expr_modes():
    assert analyze._build_edit_expr("audio", 0.04, 0.02) == "audio:threshold=0.04"
    assert analyze._build_edit_expr("motion", 0.04, 0.02) == "motion:threshold=0.02"
    assert analyze._build_edit_expr("both-or", 0.04, 0.02) == "(or audio:0.04 motion:0.02)"
    assert analyze._build_edit_expr("both-and", 0.04, 0.02) == "(and audio:0.04 motion:0.02)"


def test_build_edit_expr_unknown_mode_raises():
    with pytest.raises(ValueError):
        analyze._build_edit_expr("nonsense", 0.04, 0.02)


def test_analyze_voice_sim_audio_mode(tmp_path: Path, ae_path: str):
    result = analyze.analyze_clip(
        "/tmp/beat_sync_smoke/voice_sim.mp4",
        output_dir=tmp_path,
        mode="audio",
        auto_editor_path=ae_path,
    )
    # The voice_sim.mp4 has tone 0-4s, silence 4-5s, tone 5-8s.
    assert len(result.kept_ranges) >= 2, f"expected ≥2 kept ranges, got {result.kept_ranges}"
    # First range should start near 0
    assert result.kept_ranges[0].start < 1.0
    # Last range should end before 8.5s
    assert result.kept_ranges[-1].end < 8.5


def test_analyze_voice_sim_both_or_mode(tmp_path: Path, ae_path: str):
    """OR mode should produce at least as many kept ranges as audio-only."""
    audio_only = analyze.analyze_clip(
        "/tmp/beat_sync_smoke/voice_sim.mp4", output_dir=tmp_path,
        mode="audio", auto_editor_path=ae_path,
    )
    both_or = analyze.analyze_clip(
        "/tmp/beat_sync_smoke/voice_sim.mp4", output_dir=tmp_path,
        mode="both-or", auto_editor_path=ae_path,
    )
    assert len(both_or.kept_ranges) >= len(audio_only.kept_ranges) - 1  # tolerate 1-frame edge effects
