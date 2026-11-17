"""User video catalog: HF URL parse, persist, shipped ids stay put."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services import user_video_models as uvm
from backend.services import video_model_registry as vmr
from backend.tests.fixtures.model_files import write_model_file


@pytest.fixture
def catalog_dir(tmp_path, monkeypatch):
    path = tmp_path / "user_video_models.json"
    monkeypatch.setattr(uvm, "_CATALOG_PATH_OVERRIDE", path)
    return path


def test_parse_hf_url_variants():
    assert uvm.parse_hf_url("https://huggingface.co/Comfy-Org/MiniMax-H3") == {
        "hf_repo": "Comfy-Org/MiniMax-H3", "revision": "main", "src": None,
    }
    blob = uvm.parse_hf_url(
        "https://huggingface.co/Comfy-Org/MiniMax-H3/blob/main/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    )
    assert blob["hf_repo"] == "Comfy-Org/MiniMax-H3"
    assert blob["src"].endswith("minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors")
    resolve = uvm.parse_hf_url(
        "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/x.safetensors?download=true"
    )
    assert resolve["src"] == "loras/x.safetensors"
    assert uvm.parse_hf_url("Comfy-Org/MiniMax-H3")["hf_repo"] == "Comfy-Org/MiniMax-H3"
    assert uvm.parse_hf_url("https://hf.co/Comfy-Org/MiniMax-H3")["hf_repo"] == "Comfy-Org/MiniMax-H3"
    with pytest.raises(ValueError, match="Hugging Face"):
        uvm.parse_hf_url("https://civitai.com/models/1")
    with pytest.raises(ValueError, match="org/repo"):
        uvm.parse_hf_url("only-one-token")
    with pytest.raises(ValueError, match="dataset"):
        uvm.parse_hf_url("https://huggingface.co/datasets/someone/data")


def test_suggest_role_lora_and_moe():
    role, like = uvm.suggest_role_and_like(
        [], src="loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    )
    assert role == "lora" and like == "minimax-h3-int8"
    role, like = uvm.suggest_role_and_like([
        {"src": "NSFW/Wan2.2_Remix_NSFW_i2v_14b_high_lighting_v2.0.safetensors"},
        {"src": "NSFW/Wan2.2_Remix_NSFW_i2v_14b_low_lighting_v2.0.safetensors"},
    ])
    assert role == "generation" and like == "wan22-14b-i2v"


def test_add_lora_persists_and_registers(catalog_dir):
    mid, entry, problems = uvm.add_user_model(
        role="lora",
        like_id="minimax-h3-int8",
        hf_repo="Comfy-Org/MiniMax-H3",
        files=[{"src": "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "size": 100}],
        name="My turbo",
    )
    try:
        assert mid.startswith("user-")
        assert entry["type"] == "lora"
        assert entry["local_subdir"] == "loras"
        assert entry["applies_to"] == ["minimax-h3-int8"]
        assert vmr.VIDEO_MODEL_REGISTRY[mid]["check_files"] == [
            "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
        ]
        saved = json.loads(catalog_dir.read_text())
        assert mid in saved["models"]
        assert uvm.is_user_model_id(mid)
    finally:
        uvm.remove_user_model(mid, delete_files=False)


def test_add_generation_moe_needs_both_experts(catalog_dir):
    with pytest.raises(ValueError, match="HighNoise"):
        uvm.add_user_model(
            role="generation",
            like_id="wan22-14b-i2v",
            hf_repo="FX-FeiHou/wan2.2-Remix",
            files=[{"src": "NSFW/Wan2.2_Remix_NSFW_i2v_14b_high_lighting_v2.0.safetensors", "expert": "high"}],
        )
    mid, entry, _ = uvm.add_user_model(
        role="generation",
        like_id="wan22-14b-i2v",
        hf_repo="FX-FeiHou/wan2.2-Remix",
        files=[
            {"src": "NSFW/Wan2.2_Remix_NSFW_i2v_14b_high_lighting_v2.0.safetensors", "expert": "high"},
            {"src": "NSFW/Wan2.2_Remix_NSFW_i2v_14b_low_lighting_v2.0.safetensors", "expert": "low"},
        ],
        name="Remix I2V",
    )
    try:
        dsts = [f["dst"] for f in entry["files"]]
        assert any("HighNoise" in d for d in dsts)
        assert any("LowNoise" in d for d in dsts)
        assert entry["type"] == "wan"
        assert entry["like"] == "wan22-14b-i2v"
        assert "wan-vae" in entry["requires"]
        mapped = vmr.wan_comfyui_map()[mid]
        assert mapped["unet_high"] and mapped["unet_low"]
    finally:
        uvm.remove_user_model(mid, delete_files=False)


def test_cannot_remove_shipped_id(catalog_dir):
    with pytest.raises(ValueError, match="shipped"):
        uvm.remove_user_model("minimax-h3-int8")
    assert "minimax-h3-int8" in vmr.VIDEO_MODEL_REGISTRY


def test_add_refuses_whole_repo_with_no_files(catalog_dir):
    with pytest.raises(ValueError, match="at least one"):
        uvm.add_user_model(role="lora", like_id="minimax-h3-int8", hf_repo="x/y", files=[])


def test_remove_does_not_delete_shipped_shared_file(catalog_dir, tmp_path, monkeypatch):
    models_dir = tmp_path / "comfy" / "models"
    lora_dir = models_dir / "loras"
    lora_dir.mkdir(parents=True)
    shared = lora_dir / "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    shared.write_bytes(b"x")
    monkeypatch.setattr(vmr, "comfyui_models_dir", lambda: models_dir)
    mid, _, _ = uvm.add_user_model(
        role="lora",
        like_id="minimax-h3-int8",
        hf_repo="Comfy-Org/MiniMax-H3",
        files=[{"src": "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"}],
        name="Shared pointer",
    )
    result = uvm.remove_user_model(mid, delete_files=True)
    assert shared.exists()
    assert result["deleted_files"] == []
    assert "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors" in result["kept_shared_files"]


def test_chain_model_only_loras():
    from backend.services.comfyui_video_workflows import ComfyUIVideoWorkflowMixin
    mixin = ComfyUIVideoWorkflowMixin()
    wf = {"1": {"class_type": "UNETLoader", "inputs": {}}}
    ref, nid = mixin._chain_model_only_loras(
        wf, ["1", 0],
        [{"filename": "a.safetensors", "strength": 0.7}, {"filename": "b.safetensors", "strength": 0.5}],
        40,
    )
    assert wf["40"]["class_type"] == "LoraLoaderModelOnly"
    assert wf["40"]["inputs"]["model"] == ["1", 0]
    assert wf["41"]["inputs"]["model"] == ["40", 0]
    assert ref == ["41", 0]
    assert nid == 42


def test_suggest_role_encoder():
    role, like = uvm.suggest_role_and_like(
        [], src="qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors"
    )
    assert role == "encoder" and like == "minimax-h3-int8"
    role, like = uvm.suggest_role_and_like([{"src": "umt5_xxl_fp16.safetensors"}])
    assert role == "encoder" and like == "wan22-5b"


def test_add_encoder_replaces_shipped_companion(catalog_dir):
    mid, entry, _ = uvm.add_user_model(
        role="encoder",
        like_id="minimax-h3-int8",
        hf_repo="sakamakismile/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4",
        files=[{"src": "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors", "size": 15687142551}],
        name="Heretic NVFP4",
    )
    try:
        assert entry["type"] == "encoder"
        assert entry["replaces"] == "minimax-h3-qwen3vl-nvfp4"
        # Lands beside the shipped companion so the CLIPLoader finds it.
        assert entry["local_subdir"] == vmr.VIDEO_MODEL_REGISTRY["minimax-h3-qwen3vl-nvfp4"]["local_subdir"]
        # Every H3 build that loads the same shipped encoder can use it.
        assert "minimax-h3-int8" in entry["applies_to"]
        assert "minimax-h3-ref2va-int8" in entry["applies_to"]
        assert "minimax-h3-bf16" not in entry["applies_to"]
        assert vmr.VIDEO_MODEL_REGISTRY[mid]["check_files"] == ["qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors"]
        # Not a generation model: the loader map must not pick it up as a UNET.
        assert mid not in vmr.minimax_comfyui_map()
    finally:
        uvm.remove_user_model(mid, delete_files=False)


def test_add_encoder_needs_one_file_and_a_wired_family(catalog_dir):
    with pytest.raises(ValueError, match="one file"):
        uvm.add_user_model(
            role="encoder", like_id="wan22-5b", hf_repo="x/y",
            files=[{"src": "a.safetensors"}, {"src": "b.safetensors"}],
        )
    with pytest.raises(ValueError, match="does not take a replacement text encoder"):
        uvm.add_user_model(
            role="encoder", like_id="cogvideox-5b-i2v", hf_repo="x/y",
            files=[{"src": "t5.safetensors"}],
        )
    with pytest.raises(ValueError, match="does not stack LoRAs"):
        uvm.add_user_model(
            role="lora", like_id="cogvideox-5b", hf_repo="x/y",
            files=[{"src": "a_lora.safetensors"}],
        )


def test_resolve_text_encoder(catalog_dir, tmp_path, monkeypatch):
    models_dir = tmp_path / "comfy" / "models"
    monkeypatch.setattr(vmr, "comfyui_models_dir", lambda: models_dir)
    assert uvm.resolve_text_encoder("minimax-h3-int8", None) == (None, None)
    assert uvm.resolve_text_encoder("minimax-h3-int8", "minimax-h3-qwen3vl-nvfp4")[1]
    mid, entry, _ = uvm.add_user_model(
        role="encoder", like_id="minimax-h3-int8",
        hf_repo="sakamakismile/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4",
        files=[{"src": "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors"}],
    )
    try:
        filename, err = uvm.resolve_text_encoder("minimax-h3-int8", mid)
        assert filename is None and "not installed" in err
        target = models_dir / entry["local_subdir"] / "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors"
        write_model_file(target)
        assert uvm.resolve_text_encoder("minimax-h3-int8", mid) == (
            "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors", None,
        )
        filename, err = uvm.resolve_text_encoder("wan22-5b", mid)
        assert filename is None and "does not replace" in err
    finally:
        uvm.remove_user_model(mid, delete_files=False)


def test_an_encoder_naming_no_models_replaces_none(monkeypatch):
    monkeypatch.setitem(vmr.VIDEO_MODEL_REGISTRY, "user-enc-empty", {
        "name": "Loose encoder", "type": "encoder", "user": True, "applies_to": [],
        "files": [{"dst": "loose.safetensors"}], "local_subdir": "text_encoders"})
    monkeypatch.setattr(vmr, "is_model_installed", lambda mid: True)
    filename, err = uvm.resolve_text_encoder("wan22-5b", "user-enc-empty")
    assert filename is None and "does not replace" in err


def test_graphs_load_the_chosen_encoder():
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
    # Skip __init__ (it probes a live ComfyUI); graph builders only need the class-level maps.
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    wf = gen._create_minimax_workflow(prompt="a test", model_key="minimax-h3-int8", seed=1)
    assert wf["2"]["inputs"]["clip_name"] == "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    wf = gen._create_wan22_5b_workflow(prompt="a test", model_key="wan22-5b", seed=1, text_encoder="umt5_user.safetensors")
    assert wf["3"]["inputs"]["clip_name"] == "umt5_user.safetensors"
    wf = gen._create_minimax_workflow(
        prompt="a test", model_key="minimax-h3-int8", seed=1,
        text_encoder="qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors",
    )
    assert wf["2"]["inputs"]["clip_name"] == "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors"


def test_ltx_and_hunyuan_graphs_take_loras_and_encoder():
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    loras = [{"filename": "a.safetensors", "strength": 0.7}]

    wf = gen._create_ltx23_t2v_workflow(prompt="t", seed=1, extra_loras=loras, text_encoder="gemma_user.safetensors")
    assert wf["2"]["inputs"]["clip_name1"] == "gemma_user.safetensors"
    assert wf["2"]["inputs"]["clip_name2"] == "ltx-2.3_text_projection_bf16.safetensors"  # projection stays
    lora_nodes = [k for k, n in wf.items() if n.get("class_type") == "LoraLoaderModelOnly"]
    assert len(lora_nodes) == 1 and wf[lora_nodes[0]]["inputs"]["model"] == ["1", 0]
    sampling = next(n for n in wf.values() if n.get("class_type") == "ModelSamplingLTXV")
    assert sampling["inputs"]["model"] == [lora_nodes[0], 0]

    wf = gen._create_ltx25_t2v_workflow(prompt="t", seed=1, extra_loras=loras, text_encoder="gemma4_user.safetensors")
    assert wf["2"]["inputs"]["clip_name"] == "gemma4_user.safetensors"
    guider = next(n for n in wf.values() if n.get("class_type") == "CFGGuider")
    assert wf[guider["inputs"]["model"][0]]["class_type"] == "LoraLoaderModelOnly"

    wf = gen._create_hunyuan_t2v_workflow(prompt="t", seed=1, extra_loras=loras, text_encoder="llava_user.safetensors")
    assert wf["2"]["inputs"]["clip_name2"] == "llava_user.safetensors"
    assert wf["2"]["inputs"]["clip_name1"] == "clip_l.safetensors"  # clip_l stays
    assert wf[wf["20"]["inputs"]["model"][0]]["class_type"] == "LoraLoaderModelOnly"

    # No LoRAs, no encoder: graphs are byte-for-byte what they were.
    plain = gen._create_hunyuan_t2v_workflow(prompt="t", seed=1)
    assert plain["20"]["inputs"]["model"] == ["1", 0]
    assert not [n for n in plain.values() if n.get("class_type") == "LoraLoaderModelOnly"]


def test_lora_role_applies_to_ltx_and_hunyuan(catalog_dir):
    for like in ("ltx25-distilled-int8", "hunyuan-t2v", "wan22-5b"):
        mid, entry, _ = uvm.add_user_model(role="lora", like_id=like, hf_repo="x/y", files=[{"src": "l.safetensors"}])
        try:
            assert entry["applies_to"] == [like]
        finally:
            uvm.remove_user_model(mid, delete_files=False)
    mid, entry, _ = uvm.add_user_model(
        role="encoder", like_id="hunyuan-t2v", hf_repo="x/y", files=[{"src": "llava_user.safetensors"}],
    )
    try:
        assert entry["replaces"] == "hunyuan-llava-te"
        assert set(entry["applies_to"]) == {"hunyuan-t2v", "hunyuan-i2v"}
        assert entry["local_subdir"] == "text_encoders"
    finally:
        uvm.remove_user_model(mid, delete_files=False)
