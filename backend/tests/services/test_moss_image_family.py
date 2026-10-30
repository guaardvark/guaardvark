"""MOSS-Image pastes resolve to their own family.

The Comfy-Org repo ships the FLUX.2 VAE (flux2-vae*.safetensors) beside the
model, and OpenMOSS's own repos are diffusers snapshots; before the family was
declared, Look up filed the first under FLUX.1 and the second under SD 1.5.
"""
import pytest

try:
    from backend.services import user_model_families as F
    from backend.services.image_render_limits import family_of, defaults_for
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


COMFY_FILES = [{"src": s} for s in (
    "diffusion_models/moss_image_base_int8_convrot.safetensors",
    "diffusion_models/moss_image_sft_bf16.safetensors",
    "diffusion_models/moss_image_sft_int8_convrot.safetensors",
    "loras/moss_image_edit_lora_rank_64_bf16.safetensors",
    "text_encoders/qwen3.5_9b_w4a8_gptq.safetensors",
    "vae/flux2-vae.safetensors",
    "vae/flux2-vae_small.safetensors",
)]


def _families(**kw):
    return [(m["family"], m["wired"]) for m in F.match_families(domain="image", **kw)]


def test_the_comfy_repo_is_moss_not_flux():
    assert _families(files=COMFY_FILES, src=None, hf_repo="Comfy-Org/MOSS-Image") == [("moss-image", True)]
    picked = "diffusion_models/moss_image_sft_int8_convrot.safetensors"
    assert _families(files=COMFY_FILES, src=picked, hf_repo="Comfy-Org/MOSS-Image") == [("moss-image", True)]


def test_the_diffusers_repo_is_moss_not_sd():
    files = [{"src": "transformer/diffusion_pytorch_model-00001-of-00005.safetensors"}]
    got = _families(files=files, src=None, hf_repo="OpenMOSS-Team/MOSS-Image-Base",
                    has_model_index=True, index_class="MOSSImagePipeline")
    assert got == [("moss-image", True)]
    # Its shards are refused at add time with the files that do load.
    why = F.weight_file_problem("moss-image", "diffusion_pytorch_model-00001-of-00005.safetensors")
    assert why and "Comfy-Org/MOSS-Image" in why
    assert F.weight_file_problem("moss-image", "moss_image_sft_bf16.safetensors") is None
    assert "GGUF" in F.weight_file_problem("moss-image", "moss_image_sft-Q8_0.gguf")


def test_a_moss_lora_is_a_moss_lora_not_a_zimage_one():
    files = [{"src": "moss_image_gouache_lora.safetensors"}]
    matches = F.match_families(domain="image", files=files, src=None, hf_repo="axiomofmind/Moss-image-gouache-lora")
    assert [(m["family"], m["wired"], m["role"]) for m in matches] == [("moss-image", True, "lora")]
    assert "moss-image" in F.image_lora_families()


def test_other_families_still_match():
    assert _families(files=[{"src": "flux1-dev-Q8_0.gguf"}], src=None,
                     hf_repo="city96/FLUX.1-dev-gguf")[0] == ("flux", True)
    qwen = [{"src": "split_files/diffusion_models/qwen_image_2.1_int8_convrot.safetensors"}]
    assert _families(files=qwen, src=None, hf_repo="Comfy-Org/Qwen-Image-2.1")[0] == ("qwen-image-21", True)


def test_ids_map_to_the_moss_family_everywhere():
    for model in ("moss-image", "user-moss-image-sft-bf16"):
        assert F.comfy_t2i_family(model) == "moss-image"
        assert family_of(model) == "moss-image"
    d = defaults_for("moss-image", strict=False)
    assert (d["width"], d["height"], d["steps"], d["guidance"], d["prompt_style"]) == (1024, 1024, 25, 2.5, "natural")
