"""MiniMax H3 Comfy graph shape — the official template, audio kept."""
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator


def _gen():
    # Skip __init__ (it probes a live ComfyUI); graph builders only need
    # class-level MINIMAX_MODELS.
    return ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)


def _t2v(**kw):
    args = dict(prompt="a dog running through a meadow", num_frames=124,
                width=864, height=480, seed=42, fps=24)
    args.update(kw)
    return _gen()._create_minimax_workflow(**args)


def _class_types(wf):
    return {n["class_type"] for n in wf.values() if isinstance(n, dict) and "class_type" in n}


def test_family_detection():
    assert ComfyUIVideoGenerator._model_family("minimax-h3-int8") == "minimax"
    assert ComfyUIVideoGenerator._model_family("ltx25-distilled-int8") == "ltx"


def test_frame_count_snaps_up_to_17k_plus_5():
    f = ComfyUIVideoGenerator._minimax_frame_count
    assert f(124) == 124
    assert f(73) == 73
    assert f(120) == 124      # never shortened
    assert f(125) == 141
    assert f(1) == 5
    assert f(None) == 124


def test_t2v_is_the_template_graph():
    wf = _t2v()
    types = _class_types(wf)
    assert {"UNETLoader", "CLIPLoader", "VAELoader", "MiniMaxH3ImageToVideo",
            "BasicGuider", "KSamplerSelect", "BasicScheduler", "RandomNoise",
            "SamplerCustomAdvanced", "VAEDecode", "VAEDecodeAudio",
            "VHS_VideoCombine"} <= types
    # No CFG path and no negative prompt on this model.
    assert "CFGGuider" not in types
    assert "CLIPTextEncode" not in types
    assert "LoadImage" not in types
    assert wf["2"]["inputs"]["type"] == "minimax"
    assert wf["8"]["inputs"]["sampler_name"] == "res_multistep"
    assert wf["9"]["inputs"] == {"model": ["1", 0], "scheduler": "simple", "steps": 20, "denoise": 1.0}
    assert wf["6"]["inputs"]["length"] == 124
    assert "first_frame" not in wf["6"]["inputs"]


def test_filenames_match_registry():
    from backend.services import video_model_registry as vmr
    m = vmr.minimax_comfyui_map()["minimax-h3-int8"]
    wf = _t2v()
    assert wf["1"]["inputs"]["unet_name"] == m["unet"]
    assert wf["2"]["inputs"]["clip_name"] == m["clip"]
    assert wf["3"]["inputs"]["vae_name"] == m["vae"]
    assert wf["4"]["inputs"]["vae_name"] == m["audio_vae"]


def test_audio_is_decoded_and_muxed():
    wf = _t2v()
    assert wf["13"]["inputs"] == {"samples": ["11", 0], "vae": ["4", 0]}
    combine = wf["14"]["inputs"]
    assert combine["audio"] == ["13", 0]
    assert combine["images"] == ["12", 0]
    assert combine["frame_rate"] == 24.0
    assert combine["format"] == "video/h264-mp4"


def test_i2v_feeds_first_frame_only():
    wf = _t2v(image_filename="start.png")
    assert wf["5"] == {"class_type": "LoadImage", "inputs": {"image": "start.png"}}
    assert wf["6"]["inputs"]["first_frame"] == ["5", 0]
    assert "last_frame" not in wf["6"]["inputs"]
    assert wf["14"]["inputs"]["filename_prefix"] == "minimax_h3_i2v"


def test_steps_and_seed_are_honoured():
    wf = _t2v(num_inference_steps=30, seed=7)
    assert wf["9"]["inputs"]["steps"] == 30
    assert wf["10"]["inputs"]["noise_seed"] == 7


def test_clip_device_defaults_to_gpu_managed(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_WAN_CLIP_DEVICE", raising=False)
    assert ComfyUIVideoGenerator._minimax_clip_device() == "default"
    monkeypatch.setenv("GUAARDVARK_WAN_CLIP_DEVICE", "cpu")
    assert ComfyUIVideoGenerator._minimax_clip_device() == "cpu"


# ── optional inputs: last frame, turbo LoRA, guides ──────────────────────────

def test_last_frame_and_first_plus_last():
    wf = _t2v(last_frame_filename="end.png")
    assert wf["16"] == {"class_type": "LoadImage", "inputs": {"image": "end.png"}}
    assert wf["6"]["inputs"]["last_frame"] == ["16", 0]
    assert "first_frame" not in wf["6"]["inputs"]
    assert wf["14"]["inputs"]["filename_prefix"] == "minimax_h3_l2v"
    wf = _t2v(image_filename="start.png", last_frame_filename="end.png")
    assert wf["6"]["inputs"]["first_frame"] == ["5", 0]
    assert wf["6"]["inputs"]["last_frame"] == ["16", 0]
    assert wf["14"]["inputs"]["filename_prefix"] == "minimax_h3_flf2v"


def test_turbo_lora_patches_the_model_for_guider_and_scheduler():
    wf = _t2v(lora_name="turbo.safetensors", lora_strength=0.9, num_inference_steps=8)
    assert wf["15"] == {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {"model": ["1", 0], "lora_name": "turbo.safetensors", "strength_model": 0.9},
    }
    assert wf["7"]["inputs"]["model"] == ["15", 0]
    assert wf["9"]["inputs"]["model"] == ["15", 0]
    assert wf["9"]["inputs"]["steps"] == 8
    # Absent by default.
    assert "15" not in _t2v()


def test_guides_chain_before_the_guider():
    wf = _t2v(guides=[
        {"kind": "audio", "filename": "line.wav", "frame_idx": 0},
        {"kind": "image", "filename": "mid.png", "frame_idx": 60},
    ])
    assert wf["17"] == {"class_type": "LoadAudio", "inputs": {"audio": "line.wav"}}
    assert wf["18"]["class_type"] == "MiniMaxH3AddGuide"
    assert wf["18"]["inputs"] == {
        "positive": ["6", 0], "audio_vae": ["4", 0], "latent": ["6", 1],
        "audio": ["17", 0], "frame_idx": 0,
    }
    assert wf["19"] == {"class_type": "LoadImage", "inputs": {"image": "mid.png"}}
    assert wf["20"]["inputs"] == {
        "positive": ["18", 0], "vae": ["3", 0], "latent": ["6", 1],
        "image": ["19", 0], "frame_idx": 60,
    }
    assert wf["7"]["inputs"]["conditioning"] == ["20", 0]
    # Without guides the guider reads the node's own conditioning.
    assert _t2v()["7"]["inputs"]["conditioning"] == ["6", 0]


def test_guide_frame_index_is_checked_against_the_snapped_clip():
    import pytest
    with pytest.raises(ValueError, match="outside the clip's 124 frames"):
        _t2v(guides=[{"kind": "audio", "filename": "a.wav", "frame_idx": 124}])
    # Negative indices count from the end, as the node does.
    wf = _t2v(guides=[{"kind": "image", "filename": "end.png", "frame_idx": -1}])
    assert wf["18"]["inputs"]["frame_idx"] == -1
    with pytest.raises(ValueError, match="kind audio|image"):
        _t2v(guides=[{"kind": "video", "filename": "x.mp4"}])
