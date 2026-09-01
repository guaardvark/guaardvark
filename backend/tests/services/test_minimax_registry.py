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


# ── capability contract ──────────────────────────────────────────────────────

REF = "minimax-h3-ref2va-int8"


def test_every_h3_build_declares_the_same_contract():
    for mid in (MODEL, REF, "minimax-h3-int8-full", "minimax-h3-bf16"):
        caps = vmr.model_capabilities(mid)
        assert caps["audio_out"] and caps["audio_in"] and caps["cfg"] is False
        assert caps["aspect_ratios"] == ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
        assert caps["native_fps"] == 24 and caps["frame_rule"] == "17k+5"
        assert caps["min_steps"] == 20 and caps["default_steps"] == 20
        assert caps["license"]["attribution"] == "MiniMax H3"
        assert [e["id"] for e in caps["style_embeddings"]][:2] == ["art_is_explosion", "blooming_flowers"]
        assert "minimax-h3-style-embeddings" in vmr.VIDEO_MODEL_REGISTRY[mid]["requires"]


def test_reference_build_is_never_a_keyframe_animator():
    assert vmr.model_capabilities(REF)["modes"] == ["ref2v"]
    assert vmr.supports_first_frame_i2v(REF) is False
    assert vmr.i2v_model_for(REF) != REF
    assert vmr.model_capabilities(MODEL)["modes"] == ["t2v", "i2v", "l2v", "flf2v"]
    assert vmr.supports_first_frame_i2v(MODEL) is True
    assert vmr.model_capabilities(REF)["ref_limits"] == {
        "images": 9, "videos": 3, "audios": 3, "files": 12, "video_seconds": [2, 15],
    }


def test_reference_build_shares_companions_and_maps_both_vaes():
    m = vmr.minimax_comfyui_map()[REF]
    assert m["unet"] == "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    assert m["clip"] == "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    assert m["vae"] == "minimax_h3_video_vae_fp16.safetensors"
    assert m["audio_vae"] == "minimax_h3_audio_vae_fp32.safetensors"


def test_speed_profiles_resolve_to_installed_lora_files(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: m == "minimax-h3-fl2v-turbo-8step")
    standard = vmr.speed_profile_for(MODEL, "standard")
    assert standard["steps"] == 20 and standard["lora_file"] is None
    turbo = vmr.speed_profile_for(MODEL, "turbo-8")
    assert turbo["lora_file"] == "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    assert turbo["steps"] == 8 and turbo["min_steps"] == 8 and turbo["lora_installed"] is True
    fast = vmr.speed_profile_for(MODEL, "turbo-4-768p")
    assert fast["min_short_edge"] == 768 and fast["lora_installed"] is False
    assert vmr.speed_profile_for(MODEL, "nope") is None
    assert vmr.speed_profile_for(REF, "turbo-4")["experimental"] is True
    # The ref2v LoRA is not offered on the fl2va build and vice versa.
    assert vmr.speed_profile_for(MODEL, "turbo-4") is None


def test_turbo_loras_are_optional_two_gb_companions():
    for lid in ("minimax-h3-fl2v-turbo-8step", "minimax-h3-fl2v-turbo-4step-768p", "minimax-h3-ref2v-turbo-4step"):
        entry = vmr.VIDEO_MODEL_REGISTRY[lid]
        assert entry["type"] == "lora" and entry["local_subdir"] == "loras"
        assert entry["size_gb"] == 1.96 and entry["vram_mb"] == 0
        assert lid not in vmr.VIDEO_MODEL_REGISTRY[MODEL]["requires"]
        assert lid not in vmr.VIDEO_MODEL_REGISTRY[REF]["requires"]


def test_style_embeddings_are_a_tiny_required_companion():
    emb = vmr.VIDEO_MODEL_REGISTRY["minimax-h3-style-embeddings"]
    assert emb["type"] == "embedding" and emb["local_subdir"] == "embeddings"
    assert len(emb["check_files"]) == 10
    assert emb["check_files"][2] == "minimaxh3_bullet_time.safetensors"
    assert vmr.style_embedding_token(MODEL, "bullet_time") == "embedding:minimaxh3_bullet_time"
    assert vmr.style_embedding_token(MODEL, "nope") is None


def test_tier_defaults_follow_detected_vram():
    assert vmr.tier_defaults_for(MODEL, 16376) == {
        "tier": "16", "width": 864, "height": 480, "speed_profile": "standard", "frames": 124,
    }
    assert vmr.tier_defaults_for(MODEL, 24564)["tier"] == "24"
    assert vmr.tier_defaults_for(MODEL, 49140)["tier"] == "24"  # largest declared class
    assert vmr.tier_defaults_for(MODEL, 12288) == {}
    # A card reporting 15.9 GB still counts as the 16 GB class.
    assert vmr.vram_tier_for(15900, {"16": {}}) == "16"
    assert vmr.vram_tier_for(None, {"16": {}}) is None


def test_bigger_builds_declare_their_floor_and_encoder():
    full = vmr.VIDEO_MODEL_REGISTRY["minimax-h3-int8-full"]
    assert full["min_vram_gb"] == 24 and "minimax-h3-qwen3vl-int8" in full["requires"]
    assert full["check_files"] == ["minimax_h3_fl2va_int8_convrot.safetensors"]
    bf16 = vmr.VIDEO_MODEL_REGISTRY["minimax-h3-bf16"]
    assert bf16["min_vram_gb"] == 48 and "minimax-h3-qwen3vl-bf16" in bf16["requires"]
    assert vmr.tier_defaults_for("minimax-h3-int8-full", 16376) == {}
    assert vmr.tier_defaults_for("minimax-h3-bf16", 49140)["tier"] == "48"


def test_capabilities_give_older_families_defaults():
    wan = vmr.model_capabilities("wan22-5b")
    assert wan["modes"] == ["t2v", "i2v"] and wan["cfg"] is True and wan["audio_out"] is False
    assert wan["license"] is None and wan["speed_profiles"] == {}
    assert vmr.model_capabilities("wan22-14b")["modes"] == ["t2v"]
    assert vmr.model_capabilities("hunyuan-i2v")["modes"] == ["i2v"]
    assert vmr.model_capabilities("minimax-h3-vae") == {}


def test_verify_registry_catches_a_profile_with_a_bad_floor(monkeypatch):
    broken = dict(vmr.VIDEO_MODEL_REGISTRY[MODEL])
    broken["speed_profiles"] = {"odd": {"steps": 4, "min_steps": 8, "lora": "minimax-h3-fl2v-turbo-8step"}}
    broken["modes"] = ["t2v", "warp"]
    problems = vmr._verify_capabilities(MODEL, broken)
    assert any("min_steps <= steps" in p for p in problems)
    assert any("unknown mode 'warp'" in p for p in problems)


def test_reference_build_hands_keyframes_to_its_fl2va_sibling():
    assert vmr.i2v_model_for(REF) == MODEL
