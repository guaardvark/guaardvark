"""Opt-in test that actually loads Stable Audio Open and generates audio.

Skipped by default because:
  - First run downloads ~1.5 GB of weights
  - Subsequent runs still take ~20-30 s on a 4070 Ti SUPER
  - Requires HF_TOKEN + accepted terms at hf.co/stabilityai/stable-audio-open-1.0

Enable:
    AUDIO_FOUNDRY_RUN_SLOW_TESTS=1 pytest plugins/audio_foundry/tests/test_fx_sao.py
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


def test_sao_generates_valid_wav(tmp_path):
    """Load SAO once, generate 3 seconds of audio, verify file is a valid WAV."""
    import soundfile as sf
    from backends.audio_fx_sao import StableAudioOpenBackend

    backend = StableAudioOpenBackend(
        output_root=tmp_path,
        steps=20,  # fewer steps than production (100) — correctness test, not quality test
        max_duration_s=47.0,
    )
    backend.load()
    try:
        result = backend.generate(
            prompt="soft rain on a tin roof",
            duration_s=3.0,
            seed=42,
        )
        assert result.path.exists(), "output file missing"
        assert result.path.stat().st_size > 1000, "output file too small to be audio"
        # Re-read to confirm WAV is well-formed
        data, sr = sf.read(str(result.path))
        assert sr == 44100
        assert len(data) / sr == pytest.approx(result.duration_s, rel=0.01)
        assert result.meta["model"] == StableAudioOpenBackend.MODEL_ID
        assert result.meta["seed"] == 42
    finally:
        backend.unload()
