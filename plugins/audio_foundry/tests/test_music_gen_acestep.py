"""Opt-in test that actually loads ACE-Step and generates a song.

Skipped by default because:
  - First run downloads ~10 GB of weights
  - Subsequent runs still take 1-2 min for a 30 s song on a 4070 Ti SUPER
  - Requires acestep package + a CUDA GPU with ~10 GB free

Enable:
    AUDIO_FOUNDRY_RUN_SLOW_TESTS=1 pytest plugins/audio_foundry/tests/test_music_gen_acestep.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SLOW = os.environ.get("AUDIO_FOUNDRY_RUN_SLOW_TESTS") == "1"

pytestmark = pytest.mark.skipif(not SLOW, reason="Set AUDIO_FOUNDRY_RUN_SLOW_TESTS=1 to enable")

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def test_acestep_generates_valid_wav(tmp_path):
    """Load ACE-Step once, generate a 15-second song, verify file is valid WAV."""
    import soundfile as sf
    from backends.music_gen_acestep import ACEStepBackend

    backend = ACEStepBackend(
        output_root=tmp_path,
        steps=15,           # below production (60) — correctness test, not quality
        max_duration_s=240.0,
    )
    backend.load()
    try:
        result = backend.generate(
            style_prompt="lo-fi hip hop, mellow vibes",
            lyrics="",
            duration_s=15.0,
            instrumental_only=True,
            seed=7,
        )
        assert result.path.exists(), "output file missing"
        assert result.path.stat().st_size > 100_000, "output too small to be a song"
        data, sr = sf.read(str(result.path))
        assert sr == 44100
        assert len(data) / sr == pytest.approx(result.duration_s, rel=0.05)
        assert result.meta["model"] == ACEStepBackend.MODEL_ID
        assert result.meta["seed"] == 7
        assert result.meta["instrumental_only"] is True
    finally:
        backend.unload()
