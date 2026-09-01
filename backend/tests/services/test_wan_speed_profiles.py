"""Wan 2.2 14B Lightning profile: registry entries, LoRA nodes per expert,
the template's 4-step settings, and a plain refusal when the LoRAs are absent."""
import pytest

from backend.services import video_model_registry as vmr
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator, VideoGenerationRequest


def _gen():
    return ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)


def test_lightning_loras_are_optional_pairs_per_expert():
    for mid, base in (("wan22-t2v-lightx2v-high", "wan22-14b"), ("wan22-i2v-lightx2v-low", "wan22-14b-i2v")):
        entry = vmr.VIDEO_MODEL_REGISTRY[mid]
        assert entry["type"] == "lora" and entry["local_subdir"] == "loras" and entry["size_gb"] == 1.14
        assert entry["applies_to"] == [base]
        assert mid not in vmr.VIDEO_MODEL_REGISTRY[base]["requires"]
    assert vmr.verify_registry() == []


def test_wan_floors_now_live_in_the_registry():
    assert vmr.model_capabilities("wan22-14b")["min_steps"] == 20
    assert vmr.model_capabilities("wan22-14b")["default_steps"] == 25
    assert vmr.model_capabilities("wan22-5b")["speed_profiles"] == {}
    prof = vmr.speed_profile_for("wan22-14b-i2v", "lightx2v-4")
    assert prof["steps"] == 4 and prof["cfg"] == 1.0 and prof["shift"] == 5.0 and prof["experimental"] is True
    assert prof["lora_files"] == {
        "unet_high": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        "unet_low": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
    }


def test_builder_patches_each_expert_and_takes_the_profile_shift():
    wf = _gen()._create_wan22_t2v_workflow(
        prompt="a river", num_inference_steps=4, guidance_scale=1.0, width=832, height=480,
        lora_high="high.safetensors", lora_low="low.safetensors", shift_override=5.0,
    )
    assert wf["17"] == {"class_type": "LoraLoaderModelOnly",
                        "inputs": {"model": ["1", 0], "lora_name": "high.safetensors", "strength_model": 1.0}}
    assert wf["18"]["inputs"]["model"] == ["2", 0] and wf["18"]["inputs"]["lora_name"] == "low.safetensors"
    assert wf["8"]["inputs"] == {"model": ["17", 0], "shift": 5.0}
    assert wf["9"]["inputs"] == {"model": ["18", 0], "shift": 5.0}
    assert wf["10"]["inputs"]["steps"] == 4 and wf["10"]["inputs"]["cfg"] == 1.0
    assert wf["10"]["inputs"]["end_at_step"] == 2 and wf["11"]["inputs"]["start_at_step"] == 2
    plain = _gen()._create_wan22_t2v_workflow(prompt="a river", width=832, height=480)
    assert "17" not in plain and plain["8"]["inputs"]["model"] == ["1", 0]


def test_i2v_builder_takes_the_same_pair():
    wf = _gen()._create_wan22_i2v_workflow(
        image_filename="s.png", prompt="a river", lora_high="h.safetensors", lora_low="l.safetensors",
        shift_override=5.0, num_inference_steps=4, guidance_scale=1.0,
    )
    assert wf["8"]["inputs"] == {"model": ["17", 0], "shift": 5.0}
    assert wf["9"]["inputs"] == {"model": ["18", 0], "shift": 5.0}


def test_profile_resolution_refuses_missing_loras_and_unknown_names(monkeypatch):
    gen = _gen()
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: False)
    req = VideoGenerationRequest(model="wan22-14b", speed_profile="lightx2v-4")
    prof, err = gen._resolve_wan_profile(req, "wan22-14b")
    assert prof is None and "Manage Video Models" in err and "high noise" in err
    req = VideoGenerationRequest(model="wan22-5b", speed_profile="lightx2v-4")
    prof, err = gen._resolve_wan_profile(req, "wan22-5b")
    assert prof is None and "declares no speed profile" in err
    assert gen._resolve_wan_profile(VideoGenerationRequest(model="wan22-14b"), "wan22-14b") == (None, None)
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: True)
    prof, err = gen._resolve_wan_profile(req.__class__(model="wan22-14b", speed_profile="lightx2v-4"), "wan22-14b")
    assert err is None and prof["lora_files"]["unet_high"].endswith("high_noise.safetensors")
