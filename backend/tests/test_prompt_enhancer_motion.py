"""Motion preset → prompt wiring in prompt_enhancer.enhance_video_prompt."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.utils.prompt_enhancer import enhance_video_prompt, motion_strength_hint  # noqa: E402


@pytest.mark.parametrize("strength,fragment", [
    (0.5, "subtle"),
    (0.74, "subtle"),
    (1.5, "dynamic"),
    (2.0, "intense"),
    (3.0, "intense"),
])
def test_hint_bands(strength, fragment):
    assert fragment in motion_strength_hint(strength)


@pytest.mark.parametrize("strength", [None, 1.0, 0.75, 1.24, "not-a-number"])
def test_neutral_or_invalid_adds_nothing(strength):
    assert motion_strength_hint(strength) is None


def test_enhancer_appends_motion_phrase_for_wan():
    out = enhance_video_prompt("a fox runs through snow", style="cinematic",
                               model_family="wan", motion_strength=2.0)
    assert "fast, intense movement" in out
    assert out.startswith("a fox runs through snow.")


def test_enhancer_neutral_motion_is_unchanged_from_default():
    base = enhance_video_prompt("a fox runs through snow", style="cinematic", model_family="wan")
    neutral = enhance_video_prompt("a fox runs through snow", style="cinematic",
                                   model_family="wan", motion_strength=1.0)
    assert neutral == base
    assert "intense" not in neutral and "subtle" not in neutral


def test_enhancer_fidelity_path_still_carries_motion():
    out = enhance_video_prompt("neon sign reading OPEN", style="cinematic",
                               model_family="cogvideox", fidelity_mode=True, motion_strength=0.5)
    assert "slow, subtle movement" in out


def test_style_none_ignores_motion():
    assert enhance_video_prompt("exact text", style="none", motion_strength=2.0) == "exact text"
