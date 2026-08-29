"""MiniMax H3 registry contract: download plan, loader map, preflight gate."""
from backend.services import video_model_registry as vmr

MODEL = "minimax-h3-int8"


def test_verify_registry_is_clean_with_minimax():
    assert vmr.verify_registry() == []


def test_minimax_is_the_local_h3_release_not_the_api():
    entry = vmr.VIDEO_MODEL_REGISTRY[MODEL]
    assert entry["type"] == "minimax"
    assert entry["hf_repo"] == "Comfy-Org/MiniMax-H3"
    assert entry["check_files"] == ["minimax_h3_fl2va_pruned_int8_convrot.safetensors"]
    assert entry["local_subdir"] == "diffusion_models"


def test_minimax_requires_encoder_and_both_vaes():
    req = vmr.VIDEO_MODEL_REGISTRY[MODEL]["requires"]
    assert req == ["minimax-h3-qwen3vl-nvfp4", "minimax-h3-vae", "minimax-h3-audio-vae"]
    for dep in req:
        assert vmr.VIDEO_MODEL_REGISTRY[dep]["hf_repo"] == "Comfy-Org/MiniMax-H3"
    # Every companion path matches the layout the official template loads from.
    assert vmr.VIDEO_MODEL_REGISTRY["minimax-h3-qwen3vl-nvfp4"]["local_subdir"] == "text_encoders"
    assert vmr.VIDEO_MODEL_REGISTRY["minimax-h3-vae"]["local_subdir"] == "vae"
    assert vmr.VIDEO_MODEL_REGISTRY["minimax-h3-audio-vae"]["local_subdir"] == "vae"


def test_minimax_map_separates_video_and_audio_vae():
    m = vmr.minimax_comfyui_map()[MODEL]
    assert m["unet"] == "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    assert m["clip"] == "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    assert m["vae"] == "minimax_h3_video_vae_fp16.safetensors"
    assert m["audio_vae"] == "minimax_h3_audio_vae_fp32.safetensors"


def test_minimax_download_size_is_the_full_set():
    plan = [MODEL] + vmr.VIDEO_MODEL_REGISTRY[MODEL]["requires"]
    total = sum(vmr.VIDEO_MODEL_REGISTRY[e]["size_gb"] for e in plan)
    assert 42 < total < 43


def test_preflight_minimax_requires_install(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: False)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: True)
    ok, err = vmr.preflight_video_model(MODEL)
    assert ok is False
    assert "not installed" in err.lower()


def test_preflight_minimax_requires_comfy(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: False)
    ok, err = vmr.preflight_video_model(MODEL)
    assert ok is False
    assert "comfyui" in err.lower() and "0.30" in err


def test_preflight_minimax_ok(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: True)
    assert vmr.preflight_video_model(MODEL) == (True, "")
