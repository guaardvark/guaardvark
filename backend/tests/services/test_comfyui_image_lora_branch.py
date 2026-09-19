"""Keyframe LoRA branch-selection guard (subject-16 model-collapse fix).

The character LoRAs this app trains are SDXL. Branch selection in
ComfyUIImageGenerator._build_workflow is by model STRING, which historically let
a stray model name silently drop the LoRA (the flux-schnell branch has no LoRA
nodes; the flux-dev branch expects FLUX-format LoRAs). These tests pin the
guarantee: whenever LoRAs are present the workflow MUST contain a LoRA loader
node, regardless of the requested model string.
"""
import pytest

try:
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def _class_types(workflow: dict) -> set[str]:
    return {node.get("class_type") for node in workflow.values()}


def _build(model: str, lora_names: list[str]) -> dict:
    gen = ComfyUIImageGenerator(model=model)
    return gen._build_workflow(
        prompt="sage_harlow, cinematic portrait", negative="",
        lora_names=lora_names, width=1024, height=1024,
        seed=1, steps=28, cfg=1.0, model=model,
    )


def _build_zimage(lora_names: list[str], monkeypatch) -> dict:
    """Build on the Z-Image branch with Z-Image sidecars so the registry does not
    fall back to the legacy SDXL default (which reroutes the branch)."""
    from backend.services import media_model_registry as mmr

    def fake_resolve(lora_paths):
        return {
            "base_model_id": "zimage-turbo",
            "family": "zimage",
            "inference_engine": "offline",
            "comfy_model_tag": "zimage",
            "offline_model_key": "zimage-turbo",
            "lora_format": "zimage",
            "profile": {"id": "zimage-turbo", "family": "zimage", "comfy_model_tag": "zimage"},
        }

    monkeypatch.setattr(mmr, "resolve_inference_for_loras", fake_resolve)
    gen = ComfyUIImageGenerator(model="zimage")
    return gen._build_workflow(
        prompt="sage_harlow, cinematic portrait", negative="",
        lora_names=lora_names, width=1024, height=1024,
        seed=1, steps=28, cfg=1.0, model="zimage",
    )


def test_flux_schnell_with_loras_does_not_drop_them():
    # The flux-schnell branch has no LoRA nodes; the guard must reroute to SDXL
    # so the LoRA is actually applied.
    wf = _build("flux-schnell", ["sage_harlow_v3.safetensors"])
    types = _class_types(wf)
    assert "LoraLoader" in types, "SDXL LoRA chain expected after guard reroute"
    assert "DiffusersLoader" in types, "should be on the SDXL branch"


def test_flux_dev_with_loras_reroutes_to_sdxl():
    # flux-dev would load an SDXL LoRA in the wrong format — guard reroutes it.
    wf = _build("flux-dev", ["sage_harlow_v3.safetensors"])
    types = _class_types(wf)
    assert "LoraLoader" in types
    assert "DiffusersLoader" in types


def test_sdxl_with_loras_builds_lora_chain():
    wf = _build("sdxl", ["sage_harlow_v3.safetensors"])
    types = _class_types(wf)
    assert "LoraLoader" in types
    assert "DiffusersLoader" in types


def test_flux_without_loras_keeps_flux_branch():
    # No LoRAs → flux branch is fine (plain stylistic still), guard is inert.
    wf = _build("flux-schnell", [])
    types = _class_types(wf)
    assert "UnetLoaderGGUF" in types
    assert "LoraLoader" not in types


def test_zimage_with_loras_builds_model_only_lora_chain(monkeypatch):
    # Z-Image character LoRAs train only the transformer (not the text encoder),
    # so they must be applied model-only via LoraLoaderModelOnly on the Z-Image
    # branch. This is the identity-lock route for trained Z-Image LoRAs in ComfyUI.
    wf = _build_zimage(["zimage_elara_v1.safetensors"], monkeypatch)
    types = _class_types(wf)
    assert "UNETLoader" in types, "should stay on the Z-Image branch"
    assert "LoraLoaderModelOnly" in types, "Z-Image LoRA must be applied model-only"
    assert "ModelSamplingAuraFlow" in types, "Z-Image sampler still wraps the UNet"
    # The LoRA chain must feed the sampler (not the raw unet), so identity applies.
    sampling_inputs = wf["sampling"]["inputs"]
    assert sampling_inputs["model"][0] == "lora_0", "AuraFlow should wrap the LoRA output"
    assert wf["sampler"]["inputs"]["model"] == ["sampling", 0]


def test_zimage_reroutes_sdxl_loras_to_sdxl_branch(monkeypatch):
    # An SDXL-format LoRA requested on a Z-Image model must reroute to SDXL so it
    # is applied (LoraLoader), not loaded into a Z-Image graph that would reject it.
    wf = _build("zimage", ["sage_harlow_v3.safetensors"])
    types = _class_types(wf)
    assert "LoraLoader" in types
    assert "DiffusersLoader" in types
    assert "LoraLoaderModelOnly" not in types


def test_zimage_without_loras_has_no_lora_node(monkeypatch):
    wf = _build_zimage([], monkeypatch)
    types = _class_types(wf)
    assert "LoraLoaderModelOnly" not in types
    assert "LoraLoader" not in types
    # UNet still feeds AuraFlow sampling directly.
    assert wf["sampling"]["inputs"]["model"] == ["unet", 0]
