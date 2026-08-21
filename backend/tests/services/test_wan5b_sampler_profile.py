"""Wan 2.2 5B sampler profiles: adaptive (euler + scaled shift) vs official (uni_pc + shift 8)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.comfyui_video_generator import ComfyUIVideoGenerator  # noqa: E402


@pytest.fixture
def gen(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_WAN5B_SAMPLER", raising=False)
    monkeypatch.setattr(
        ComfyUIVideoGenerator, "_wan_clip_device",
        classmethod(lambda cls, total_vram_mb=None: "cpu"),
    )
    return ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)


def _sampler(wf):
    return wf["10"]["inputs"]["sampler_name"], wf["8"]["inputs"]["shift"]


def test_default_is_adaptive(gen):
    wf = gen._create_wan22_5b_workflow(prompt="x", width=832, height=480, seed=1)
    sampler, shift = _sampler(wf)
    assert sampler == "euler"
    assert shift == ComfyUIVideoGenerator._wan_dynamic_shift(832, 480)
    assert shift < 8.0


def test_official_profile_uses_uni_pc_and_fixed_shift(gen):
    wf = gen._create_wan22_5b_workflow(prompt="x", width=832, height=480, seed=1, sampler_profile="official")
    assert _sampler(wf) == ("uni_pc", 8.0)


def test_official_profile_keeps_shift_8_at_native_res(gen):
    wf = gen._create_wan22_5b_workflow(prompt="x", width=1280, height=704, seed=1, sampler_profile="official")
    assert _sampler(wf) == ("uni_pc", 8.0)


def test_env_sets_default_and_request_wins(gen, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_WAN5B_SAMPLER", "official")
    assert _sampler(gen._create_wan22_5b_workflow(prompt="x", seed=1))[0] == "uni_pc"
    assert _sampler(gen._create_wan22_5b_workflow(prompt="x", seed=1, sampler_profile="adaptive"))[0] == "euler"


@pytest.mark.parametrize("bad", ["", "  ", "dpm", None])
def test_unknown_profile_falls_back_to_adaptive(gen, bad, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_WAN5B_SAMPLER", "nonsense")
    assert ComfyUIVideoGenerator._wan5b_sampler_profile(bad) == "adaptive"


def test_profile_resolution_is_case_insensitive():
    assert ComfyUIVideoGenerator._wan5b_sampler_profile(" Official ") == "official"


def test_request_field_exists_with_neutral_default():
    from backend.services.comfyui_video_generator import VideoGenerationRequest
    from backend.services.batch_video_generator import BatchVideoRequest
    assert VideoGenerationRequest(prompt="x").wan_sampler_profile is None
    assert BatchVideoRequest(batch_id="b", items=[], output_dir="/tmp").wan_sampler_profile is None
