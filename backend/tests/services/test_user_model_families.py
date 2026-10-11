"""Family table: URL parse, match, src sanitise, duplicates."""

from __future__ import annotations

import pytest

from backend.services import user_model_families as umf


def test_parse_hf_url_variants():
    assert umf.parse_hf_url("https://huggingface.co/Comfy-Org/MiniMax-H3") == {
        "hf_repo": "Comfy-Org/MiniMax-H3", "revision": "main", "src": None,
    }
    assert umf.parse_hf_url("https://hf.co/org/repo")["hf_repo"] == "org/repo"
    blob = umf.parse_hf_url(
        "https://huggingface.co/Comfy-Org/MiniMax-H3/blob/main/loras/x.safetensors"
    )
    assert blob["src"] == "loras/x.safetensors"
    resolve = umf.parse_hf_url(
        "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/x.safetensors?download=true"
    )
    assert resolve["src"] == "loras/x.safetensors"
    tree = umf.parse_hf_url("https://huggingface.co/org/repo/tree/v1.2")
    assert tree["hf_repo"] == "org/repo"
    assert tree["revision"] == "v1.2"
    assert tree["src"] is None
    blob_rev = umf.parse_hf_url(
        "https://huggingface.co/org/repo/blob/v1.2/weights/model.safetensors"
    )
    assert blob_rev["revision"] == "v1.2"
    assert blob_rev["src"] == "weights/model.safetensors"
    pr = umf.parse_hf_url(
        "https://huggingface.co/org/repo/tree/refs/pr/12"
    )
    assert pr["revision"] == "refs/pr/12" and pr["src"] is None
    pr_file = umf.parse_hf_url(
        "https://huggingface.co/org/repo/blob/refs/pr/1/loras/x.safetensors"
    )
    assert pr_file["revision"] == "refs/pr/1"
    assert pr_file["src"] == "loras/x.safetensors"
    branch = umf.parse_hf_url(
        "https://huggingface.co/org/repo/resolve/refs/heads/dev/weights/a.safetensors"
    )
    assert branch["revision"] == "refs/heads/dev"
    assert branch["src"] == "weights/a.safetensors"
    spaced = umf.parse_hf_url(
        "https://huggingface.co/org/repo/blob/main/K2%20Style%20(v2).safetensors"
    )
    assert spaced["src"] == "K2 Style (v2).safetensors"
    encoded_pr = umf.parse_hf_url("https://huggingface.co/org/repo/blob/refs%2Fpr%2F3/x.safetensors")
    assert encoded_pr["revision"] == "refs/pr/3" and encoded_pr["src"] == "x.safetensors"
    assert umf.parse_hf_url("Comfy-Org/MiniMax-H3")["hf_repo"] == "Comfy-Org/MiniMax-H3"
    assert umf.hf_inspect_url("org/repo") == "https://huggingface.co/org/repo"
    assert umf.hf_inspect_url("org/repo", "v1.2") == "https://huggingface.co/org/repo/tree/v1.2"
    assert umf.hf_inspect_url("org/repo", "main", "weights/a.safetensors") == (
        "https://huggingface.co/org/repo/blob/main/weights/a.safetensors"
    )
    with pytest.raises(ValueError, match="Hugging Face"):
        umf.parse_hf_url("https://civitai.com/models/1")
    with pytest.raises(ValueError, match="dataset"):
        umf.parse_hf_url("https://huggingface.co/datasets/org/repo")
    with pytest.raises(ValueError, match="Space"):
        umf.parse_hf_url("https://huggingface.co/spaces/org/app")
    with pytest.raises(ValueError, match="org/repo"):
        umf.parse_hf_url("only-one-token")


def test_sanitize_repo_src_rejects_parent_dir():
    with pytest.raises(ValueError, match="inside the repo"):
        umf.sanitize_repo_src("../evil.safetensors")
    with pytest.raises(ValueError, match="inside the repo"):
        umf.sanitize_repo_src("foo/../../etc/passwd")
    assert umf.sanitize_repo_src("loras/a.safetensors") == "loras/a.safetensors"


def test_match_flux_not_sdxl():
    matches = umf.match_families(
        domain="image", files=[{"src": "flux1-dev.safetensors"}], src=None,
        hf_repo="black-forest-labs/FLUX.1-dev", has_model_index=False,
    )
    assert matches[0]["family"] == "flux" and matches[0]["wired"] is True
    assert matches[0]["role"] == "generation"


def test_match_lora_filename():
    matches = umf.match_families(
        domain="image", files=[], src="my_lora.safetensors", hf_repo="x/y", has_model_index=False,
    )
    assert matches[0]["role"] == "lora" and matches[0]["family"] == "zimage"


def test_match_flux_lora_not_zimage():
    matches = umf.match_families(
        domain="image",
        files=[{"src": "flux-dev-lora.safetensors"}],
        src=None,
        hf_repo="someone/flux-style-lora",
        has_model_index=False,
    )
    assert matches[0]["role"] == "lora" and matches[0]["family"] == "flux"


def test_match_wan_moe_pair():
    matches = umf.match_families(
        domain="video",
        files=[
            {"src": "NSFW/Wan2.2_Remix_NSFW_i2v_14b_high_lighting_v2.0.safetensors"},
            {"src": "NSFW/Wan2.2_Remix_NSFW_i2v_14b_low_lighting_v2.0.safetensors"},
        ],
        src=None, hf_repo="FX-FeiHou/wan2.2-Remix",
    )
    assert matches[0]["role"] == "generation"
    assert matches[0]["like"] == "wan22-14b-i2v"
    assert matches[0]["moe"] is True


def test_match_unwired_qwen_image():
    # Qwen-Image 1.0 is refused by name, and the refusal names 2.1 as the supported family.
    matches = umf.match_families(
        domain="image", files=[], src=None, hf_repo="Qwen/Qwen-Image",
        has_model_index=True, pipeline_tag="qwen-image",
    )
    assert matches[0]["wired"] is False
    assert matches[0]["family"] == "qwen-image"
    assert "not wired" in matches[0]["reason"]
    assert "2.1" in matches[0]["reason"]


# ── Qwen-Image 2.1 (real Hugging Face listings, 2026-10-11) ─────────────────

_ABENZERPS_ROOT = [
    "qwen-image-2.1-UC-BF16.gguf", "qwen-image-2.1-UC-MLX-4bit.safetensors",
    "qwen-image-2.1-UC-MLX-6bit.safetensors", "qwen-image-2.1-UC-MLX-8bit.safetensors",
    "qwen-image-2.1-UC-NVFP4.safetensors", "qwen-image-2.1-UC-Q4_0.gguf",
    "qwen-image-2.1-UC-Q8_0.gguf", "qwen-image-2.1-UC-fp8.safetensors",
    "qwen-image-2.1-UC-int8_convrot.safetensors", "qwen-image-2.1-uncensored-lora.safetensors",
    "text_encoders/qwen3vl_8b_bf16.safetensors", "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
    "vae/qwen_image_2.1_vae_bf16.safetensors",
]
_COMFY_ORG_21_ROOT = [
    "diffusion_models/qwen_image_2.1_bf16.safetensors",
    "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
    "loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors",
    "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
    "vae/qwen_image_2.1_vae_bf16.safetensors",
]


def test_qwen_image_21_repack_repo_root_is_the_wired_21_family():
    matches = umf.match_families(
        domain="image", files=[{"src": f} for f in _ABENZERPS_ROOT], src=None,
        hf_repo="abenzerps/Qwen-Image-2.1-Uncensored-GGUF", has_model_index=False,
        pipeline_tag="text-to-image", base_model=["Qwen/Qwen-Image-2.1"],
    )
    top = matches[0]
    assert (top["family"], top["wired"], top["like"]) == ("qwen-image-21", True, "qwen-image-21")
    # The LoRA and encoders beside the model do not make the repo a LoRA.
    assert top["role"] == "generation"
    assert not any(m["family"] == "qwen-image" for m in matches)


def test_qwen_image_21_single_file_pastes_resolve_whatever_the_suffix():
    # Matching names the family; whether the file loads is weight_file_problem's call.
    for src in ("qwen-image-2.1-UC-fp8.safetensors", "qwen-image-2.1-UC-Q8_0.gguf"):
        m = umf.match_families(
            domain="image", files=[], src=src,
            hf_repo="abenzerps/Qwen-Image-2.1-Uncensored-GGUF", has_model_index=False,
        )
        assert (m[0]["family"], m[0]["role"]) == ("qwen-image-21", "generation"), src


def test_comfy_org_qwen_image_21_repo_is_the_wired_21_family():
    m = umf.match_families(
        domain="image", files=[{"src": f} for f in _COMFY_ORG_21_ROOT], src=None,
        hf_repo="Comfy-Org/Qwen-Image-2.1", has_model_index=False,
        base_model=["Qwen/Qwen-Image-2.1"],
    )
    assert (m[0]["family"], m[0]["role"], m[0]["wired"]) == ("qwen-image-21", "generation", True)


def test_card_base_model_resolves_a_repack_without_a_version_in_its_names():
    m = umf.match_families(
        domain="image", files=[{"src": "model-uc-fp8.safetensors"}], src=None,
        hf_repo="someone/uncensored-repack", has_model_index=False,
        base_model="Qwen/Qwen-Image-2.1",
    )
    assert m[0]["family"] == "qwen-image-21" and m[0]["wired"] is True


def test_qwen_claim_order_edit_then_21_then_10():
    edit = umf.match_families(
        domain="image", files=[{"src": "qwen_image_edit_2509_fp8_e4m3fn.safetensors"}], src=None,
        hf_repo="Comfy-Org/Qwen-Image-Edit_ComfyUI", has_model_index=False,
    )
    assert edit[0]["family"] == "qwen-image-edit" and edit[0]["wired"] is True
    v21 = umf.match_families(
        domain="image", files=[{"src": "qwen_image_2.1_int8_convrot.safetensors"}], src=None,
        hf_repo="mirror/qwen-image", has_model_index=False,
    )
    assert v21[0]["family"] == "qwen-image-21" and v21[0]["wired"] is True
    v10 = umf.match_families(
        domain="image", files=[{"src": "qwen_image_fp8_e4m3fn.safetensors"}], src=None,
        hf_repo="Comfy-Org/Qwen-Image_ComfyUI", has_model_index=False,
    )
    assert v10[0]["family"] == "qwen-image" and v10[0]["wired"] is False
    assert "2.1" in v10[0]["reason"]


def test_a_diffusers_21_repo_with_the_10_class_still_resolves_to_21():
    m = umf.match_families(
        domain="image", files=[], src=None, hf_repo="Qwen/Qwen-Image-2.1",
        has_model_index=True, index_class="QwenImagePipeline",
    )
    assert m[0]["family"] == "qwen-image-21" and m[0]["wired"] is True


@pytest.mark.parametrize("family,name,fragment", [
    ("qwen-image-21", "qwen-image-2.1-UC-Q8_0.gguf", "GGUF loader does not read"),
    ("qwen-image-21", "qwen-image-2.1-UC-MLX-8bit.safetensors", "Apple MLX"),
    ("flux", "flux1-dev-MLX-4bit.safetensors", "Apple MLX"),
    ("qwen-image-edit", "qwen_image_edit_MLX.safetensors", "Apple MLX"),
    ("qwen-image-21", "qwen-image-2.1.ckpt", "pick a .safetensors"),
])
def test_weight_files_a_comfy_loader_cannot_read_are_refused_by_name(family, name, fragment):
    problem = umf.weight_file_problem(family, name)
    assert problem and problem.startswith(name) and fragment in problem


@pytest.mark.parametrize("family,name", [
    ("qwen-image-21", "qwen-image-2.1-UC-fp8.safetensors"),
    ("qwen-image-21", "diffusion_models/qwen_image_2.1_int8_convrot.safetensors"),
    ("flux", "flux1-dev-Q8_0.gguf"),
    ("flux", "flux1-dev.safetensors"),
    ("sdxl", "anything.gguf"),
])
def test_weight_files_the_loader_reads_pass(family, name):
    assert umf.weight_file_problem(family, name) is None


@pytest.mark.parametrize("family", ["flux", "qwen-image-edit"])
@pytest.mark.parametrize("suffix", umf.WEIGHT_SUFFIXES)
def test_flux_and_edit_rows_still_take_every_weight_suffix(family, suffix):
    # Only Qwen-Image 2.1 narrows the suffixes; the other Comfy rows keep taking
    # what they took before (the MLX refusal is by name, not suffix).
    assert umf.weight_file_problem(family, f"model{suffix}") is None


def test_a_multi_base_lora_card_is_not_refused_for_a_base_it_also_names():
    # The card's base_model feeds the Qwen 2.1 claim only; a FLUX LoRA whose card
    # also lists SD3.5 as a base stays a FLUX LoRA instead of an SD3 refusal.
    m = umf.match_families(
        domain="image", files=[{"src": "flux-dev-style-lora.safetensors"}], src=None,
        hf_repo="someone/flux-style-lora", has_model_index=False,
        base_model=["black-forest-labs/FLUX.1-dev", "stabilityai/stable-diffusion-3.5-large"],
    )
    assert (m[0]["family"], m[0]["role"], m[0]["wired"]) == ("flux", "lora", True)
    # And a base_model naming an unwired family alone does not refuse a plain file.
    m = umf.match_families(
        domain="image", files=[{"src": "merged.safetensors"}], src=None,
        hf_repo="someone/sdxl-merge", has_model_index=False,
        base_model=["stabilityai/stable-diffusion-3.5-large"],
    )
    assert all(x["wired"] for x in m) or m == []


@pytest.mark.parametrize("model_id,family", [
    ("flux-dev", "flux"), ("flux", "flux"), ("FLUX1-dev", "flux"), ("comfyui (flux-schnell)", "flux"),
    ("user-flux-fp8", "flux"), ("qwen-image-21", "qwen-image-21"),
    ("user-qwen-image-21-uc-fp8", "qwen-image-21"),
    ("user-qwen-image-edit-x", None), ("zimage-turbo", None), ("sd-xl", None), ("", None), (None, None),
    ("user-sdxl-juggernaut", None), ("comfyui", None),
])
def test_comfy_t2i_family_reads_the_family_off_the_id(model_id, family):
    assert umf.comfy_t2i_family(model_id) == family


def test_base_model_from_card_data():
    class _Info:
        card_data = {"base_model": ["Qwen/Qwen-Image-2.1"], "license": "other"}

    assert umf._base_model_from_info(_Info()) == ["Qwen/Qwen-Image-2.1"]
    _Info.card_data = {"base_model": "Qwen/Qwen-Image-2.1"}
    assert umf._base_model_from_info(_Info()) == ["Qwen/Qwen-Image-2.1"]
    _Info.card_data = {}
    assert umf._base_model_from_info(_Info()) == []


def test_match_wired_qwen_image_edit_repo():
    matches = umf.match_families(
        domain="image", files=[{"src": "qwen_image_edit_2509_fp8_e4m3fn.safetensors"}],
        src=None, hf_repo="Comfy-Org/Qwen-Image-Edit_ComfyUI",
        has_model_index=False,
    )
    assert matches[0]["wired"] is True
    assert matches[0]["family"] == "qwen-image-edit"


def test_match_index_class_qwen_edit_not_unwired_t2i():
    matches = umf.match_families(
        domain="image", files=[], src=None, hf_repo="org/untitled-edit",
        has_model_index=True, index_class="QwenImageEditPlusPipeline",
    )
    assert matches[0]["wired"] is True
    assert matches[0]["family"] == "qwen-image-edit"


def test_match_index_class_flux_without_filename_tokens():
    matches = umf.match_families(
        domain="image", files=[], src=None, hf_repo="someone/mystery-stills",
        has_model_index=True, index_class="diffusers.FluxPipeline",
    )
    assert matches[0]["family"] == "flux" and matches[0]["wired"] is True
    assert "class:FluxPipeline" in matches[0]["reason"]


def test_match_index_class_qwen_without_name_tokens():
    matches = umf.match_families(
        domain="image", files=[], src=None, hf_repo="org/untitled",
        has_model_index=True, index_class="QwenImagePipeline",
    )
    assert matches[0]["wired"] is False
    assert matches[0]["family"] == "qwen-image"


def test_find_duplicate():
    catalog = {"models": {
        "user-sdxl-a": {
            "hf_repo": "x/y", "revision": "main",
            "files": [{"src": "merged.safetensors"}], "kind": "single_file",
        }
    }}
    assert umf.find_duplicate(
        catalog, hf_repo="x/y", revision="main",
        files=[{"src": "merged.safetensors"}],
    ) == "user-sdxl-a"
    assert umf.find_duplicate(
        catalog, hf_repo="x/y", revision="main",
        files=[{"src": "other.safetensors"}],
    ) is None


# ── video: the Ep 19 findings (real Hugging Face listings, 2026-09-27) ──────

_LTX_VIDEO_ROOT = [
    "ltx-video-2b-v0.9.1.safetensors", "ltxv-13b-0.9.7-dev.safetensors",
    "ltxv-13b-0.9.7-distilled-lora128.safetensors", "ltxv-2b-0.9.8-distilled.safetensors",
    "ltxv-spatial-upscaler-0.9.8.safetensors", "text_encoder/model-00001-of-00004.safetensors",
    "transformer/diffusion_pytorch_model-00001-of-00002.safetensors",
    "vae/diffusion_pytorch_model.safetensors",
]
_WAN_5B_NATIVE_ROOT = [
    "Wan2.2_VAE.pth", "diffusion_pytorch_model-00001-of-00003.safetensors",
    "diffusion_pytorch_model-00002-of-00003.safetensors",
    "diffusion_pytorch_model-00003-of-00003.safetensors", "models_t5_umt5-xxl-enc-bf16.pth",
]
_MOCHI_ROOT = [
    "decoder.safetensors", "dit.safetensors", "encoder.safetensors",
    "text_encoder/model-00001-of-00002.safetensors",
    "transformer/diffusion_pytorch_model-00001-of-00005.safetensors",
    "vae/diffusion_pytorch_model.safetensors",
]
_WAN_I2V_MOE_ROOT = [
    "HighNoise/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf",
    "LowNoise/Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf",
    "VAE/Wan2.1_VAE.safetensors",
]


def _video(files=None, src=None, repo="", cls=None):
    return umf.match_families(
        domain="video", files=[{"src": f} for f in (files or [])], src=src, hf_repo=repo,
        has_model_index=bool(cls), index_class=cls,
    )


def test_wan_5b_file_is_not_the_14b_moe():
    m = _video(src="split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
               repo="Comfy-Org/Wan_2.2_ComfyUI_Repackaged")
    assert (m[0]["role"], m[0]["like"], m[0]["moe"]) == ("generation", "wan22-5b", False)
    assert m[0].get("shipped") is True


def test_wan_5b_elsewhere_by_name_alone():
    m = _video(src="Wan2.2-TI2V-5B-Q8_0.gguf", repo="someone/Wan2.2-TI2V-5B-GGUF")
    assert (m[0]["like"], m[0]["moe"]) == ("wan22-5b", False)


def test_wan_5b_native_repo_root_is_a_generation_model():
    m = _video(_WAN_5B_NATIVE_ROOT, repo="Wan-AI/Wan2.2-TI2V-5B")
    assert (m[0]["role"], m[0]["like"]) == ("generation", "wan22-5b")


def test_ltx_video_09_is_refused_by_name():
    m = _video(_LTX_VIDEO_ROOT, repo="Lightricks/LTX-Video", cls="LTXPipeline")
    assert m[0]["wired"] is False and "LTX-Video 0.9" in m[0]["reason"]


def test_mochi_is_refused_by_name():
    root = _video(_MOCHI_ROOT, repo="genmo/mochi-1-preview", cls="MochiPipeline")
    one = _video(src="split_files/diffusion_models/mochi_preview_bf16.safetensors",
                 repo="Comfy-Org/mochi_preview_repackaged")
    assert root[0]["wired"] is False and root[0]["label"] == "Mochi"
    assert one[0]["wired"] is False and one[0]["label"] == "Mochi"


def test_unknown_diffusers_video_pipeline_is_refused_by_class():
    m = _video(["transformer/diffusion_pytorch_model.safetensors"], repo="lab/new-video-model",
               cls="SomeNewVideoPipeline")
    assert m[0]["wired"] is False and "SomeNewVideoPipeline" in m[0]["reason"]


def test_wan_i2v_moe_repo_root_keeps_its_vae_out_of_the_role():
    m = _video(_WAN_I2V_MOE_ROOT, repo="QuantStack/Wan2.2-I2V-A14B-GGUF")
    assert (m[0]["role"], m[0]["like"], m[0]["moe"]) == ("generation", "wan22-14b-i2v", True)


@pytest.mark.parametrize("src,repo,like", [
    ("hunyuan-video-t2v-720p-Q8_0.gguf", "someone/hunyuan-mirror", "hunyuan-t2v"),
    ("hunyuan-video-i2v-720p-Q8_0.gguf", "someone/hunyuan-mirror", "hunyuan-i2v"),
    ("CogVideoX_1_5_5b_I2V_fp8.safetensors", "someone/cog-mirror", "cogvideox-5b-i2v"),
    ("ltx-2.3-22b-dev-fp8.safetensors", "someone/ltx-mirror", "ltx23-distilled-fp8"),
    ("ltx-2.5-22b-dev-int8.safetensors", "someone/ltx-mirror", "ltx25-distilled-int8"),
    ("minimax_h3_fl2va_fp8.safetensors", "someone/h3-mirror", "minimax-h3-int8"),
    ("split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
     "someone/wan-mirror", "wan22-14b"),
])
def test_other_families_are_not_filed_under_wan(src, repo, like):
    m = _video(src=src, repo=repo)
    assert m[0]["like"] == like


def test_every_shipped_generation_file_maps_to_its_own_entry():
    from backend.services.video_model_registry import GENERATION_TYPES, VIDEO_MODEL_REGISTRY

    for mid, entry in VIDEO_MODEL_REGISTRY.items():
        if mid.startswith("user-") or entry.get("type") not in GENERATION_TYPES:
            continue
        for f in entry.get("files") or []:
            m = _video(src=f["src"], repo=entry["hf_repo"])
            assert m and m[0]["like"] == mid, (mid, f["src"], m[:1])


def test_wan_lora_stays_a_lora():
    m = _video(src="split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
               repo="Comfy-Org/Wan_2.2_ComfyUI_Repackaged")
    assert (m[0]["role"], m[0]["like"]) == ("lora", "wan22-14b-i2v")


def test_wan_5b_lora_is_filed_under_the_5b():
    m = _video(["HSToric_Color_Wan2.2_5B_LoRA.safetensors"],
               repo="AlekseyCalvin/HSToric_Color_Wan2.2_5B_LoRA_BySilverAgePoets")
    assert (m[0]["role"], m[0]["like"]) == ("lora", "wan22-5b")
