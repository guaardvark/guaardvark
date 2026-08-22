"""Wan 2.2 5B defaults to the reference sampler config.

"adaptive" scales shift linearly with pixel area, so by 736x416 it floors at 3.0
against the 8.0 the model is tuned for. Every clip rendered with it warped or
colour-bled; "official" (the ComfyUI template's own uni_pc + fixed shift 8)
rendered cleanly at both 1280x736 and 736x416.
"""
from __future__ import annotations

import pytest

from backend.services.comfyui_video_generator import ComfyUIVideoGenerator as G


def test_default_is_the_reference_config():
    assert G._wan5b_sampler_profile() == "official"
    assert G.WAN5B_SAMPLER_PROFILES["official"] == {"sampler": "uni_pc", "shift": 8.0}


def test_official_pins_shift_regardless_of_resolution():
    """The whole point: small frames stop being under-shifted."""
    assert G.WAN5B_SAMPLER_PROFILES["official"]["shift"] == 8.0


def test_adaptive_is_still_selectable():
    assert G._wan5b_sampler_profile("adaptive") == "adaptive"
    assert G.WAN5B_SAMPLER_PROFILES["adaptive"]["shift"] is None


def test_env_override_still_wins_over_the_default(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_WAN5B_SAMPLER", "adaptive")
    assert G._wan5b_sampler_profile() == "adaptive"


def test_an_explicit_request_beats_the_env(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_WAN5B_SAMPLER", "adaptive")
    assert G._wan5b_sampler_profile("official") == "official"


def test_an_unknown_profile_falls_back_to_the_default():
    assert G._wan5b_sampler_profile("nonsense") == "official"


@pytest.mark.parametrize("width,height", [(736, 416), (960, 544)])
def test_adaptive_still_under_shifts_small_frames(width, height):
    """Documents why the default moved; delete when the curve is retuned."""
    assert G._wan_dynamic_shift(width, height) < 8.0
