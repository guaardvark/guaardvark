"""HunyuanVideo: registry entries, derived loader map, preflight, and ComfyUI graphs."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services import video_model_registry as vmr  # noqa: E402
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator  # noqa: E402


@pytest.fixture
def gen(monkeypatch):
    """Builder-only instance: skip __init__ (ComfyUI probe) and pin the TE device."""
    monkeypatch.setattr(
        ComfyUIVideoGenerator, "_wan_clip_device",
        classmethod(lambda cls, total_vram_mb=None: "cpu"),
    )
    return ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)


def test_registry_is_clean_with_hunyuan():
    assert vmr.verify_registry() == []
    for mid in ("hunyuan-t2v", "hunyuan-i2v"):
        entry = vmr.VIDEO_MODEL_REGISTRY[mid]
        assert entry["type"] == "hunyuan"
        assert entry["check_files"] == [f["dst"] for f in entry["files"]]
        for dep in entry["requires"]:
            assert dep in vmr.VIDEO_MODEL_REGISTRY


def test_hunyuan_map_derives_from_registry():
    m = vmr.hunyuan_comfyui_map()
    assert m["hunyuan-t2v"] == {
        "type": "t2v",
        "unet": "hunyuan-video-t2v-720p-Q5_K_M.gguf",
        "clip_l": "clip_l.safetensors",
        "clip_llava": "llava_llama3_fp8_scaled.safetensors",
        "vae": "hunyuan_video_vae_bf16.safetensors",
        "clip_vision": None,
    }
    assert m["hunyuan-i2v"]["type"] == "i2v"
    assert m["hunyuan-i2v"]["unet"] == "hunyuan-video-i2v-720p-Q5_K_M.gguf"
    assert m["hunyuan-i2v"]["clip_vision"] == "llava_llama3_vision.safetensors"


def test_preflight_requires_install(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda mid: False)
    ok, msg = vmr.preflight_video_model("hunyuan-t2v")
    assert not ok and "not installed" in msg


def test_preflight_requires_companions(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda mid: mid == "hunyuan-i2v")
    ok, msg = vmr.preflight_video_model("hunyuan-i2v")
    assert not ok and "companion" in msg


def test_family_alignment_and_vram_floor():
    assert ComfyUIVideoGenerator._model_family("hunyuan-t2v") == "hunyuan"
    assert ComfyUIVideoGenerator._model_family("hunyuan-i2v") == "hunyuan"
    assert ComfyUIVideoGenerator._align_dimensions(850, 482, "hunyuan-t2v") == (848, 480)
    assert ComfyUIVideoGenerator._min_vram_gb_for("hunyuan-i2v") == 16
    assert ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "hunyuan-t2v") != (1920, 1920)


@pytest.mark.parametrize("requested,expected", [(73, 73), (72, 73), (49, 49), (50, 49), (1, 1), (3, 5), (129, 129)])
def test_frame_count_snaps_to_4n_plus_1(requested, expected):
    assert ComfyUIVideoGenerator._hunyuan_frame_count(requested) == expected


def test_t2v_graph(gen):
    wf = gen._create_hunyuan_t2v_workflow(
        prompt="a fox running through snow", num_frames=73, num_inference_steps=20,
        guidance_scale=6.0, width=848, height=480, seed=7, fps=24,
    )
    types = {n["class_type"] for n in wf.values()}
    assert {
        "UnetLoaderGGUF", "DualCLIPLoader", "VAELoader", "CLIPTextEncode", "FluxGuidance",
        "EmptyHunyuanLatentVideo", "ModelSamplingSD3", "BasicGuider", "BasicScheduler",
        "KSamplerSelect", "RandomNoise", "SamplerCustomAdvanced", "VAEDecodeTiled", "VHS_VideoCombine",
    } <= types
    assert wf["1"]["inputs"]["unet_name"] == "hunyuan-video-t2v-720p-Q5_K_M.gguf"
    assert wf["2"]["inputs"] == {
        "clip_name1": "clip_l.safetensors",
        "clip_name2": "llava_llama3_fp8_scaled.safetensors",
        "type": "hunyuan_video",
        "device": "cpu",
    }
    assert wf["5"]["inputs"]["guidance"] == 6.0
    assert wf["6"]["inputs"]["length"] == 73
    assert wf["20"]["inputs"]["shift"] == 7.0
    assert wf["22"]["inputs"]["steps"] == 20
    assert wf["24"]["inputs"]["noise_seed"] == 7
    assert wf["25"]["inputs"]["latent_image"] == ["6", 0]
    assert wf["26"]["inputs"]["tile_size"] == 256 and wf["26"]["inputs"]["temporal_size"] == 64
    assert wf["27"]["inputs"]["frame_rate"] == 24
    assert "RIFE VFI" not in types


def test_t2v_graph_interpolation_adds_rife(gen):
    wf = gen._create_hunyuan_t2v_workflow(prompt="x", seed=1, interpolation_multiplier=2)
    assert any(n["class_type"] == "RIFE VFI" for n in wf.values())


def test_i2v_graph(gen):
    wf = gen._create_hunyuan_i2v_workflow(
        image_filename="start.png", prompt="she turns and smiles", num_frames=49, seed=3,
    )
    assert wf["1"]["inputs"]["unet_name"] == "hunyuan-video-i2v-720p-Q5_K_M.gguf"
    assert wf["4"]["inputs"]["clip_name"] == "llava_llama3_vision.safetensors"
    assert wf["5"] == {"class_type": "LoadImage", "inputs": {"image": "start.png"}}
    assert wf["6"]["inputs"]["crop"] == "center"
    assert wf["7"]["class_type"] == "TextEncodeHunyuanVideo_ImageToVideo"
    assert wf["7"]["inputs"]["clip_vision_output"] == ["6", 0]
    i2v = wf["8"]["inputs"]
    assert wf["8"]["class_type"] == "HunyuanImageToVideo"
    assert i2v["guidance_type"] == "v2 (replace)"
    assert i2v["start_image"] == ["5", 0] and i2v["length"] == 49
    assert wf["9"]["inputs"]["conditioning"] == ["8", 0]
    assert wf["21"]["inputs"]["conditioning"] == ["9", 0]
    assert wf["25"]["inputs"]["latent_image"] == ["8", 1]
