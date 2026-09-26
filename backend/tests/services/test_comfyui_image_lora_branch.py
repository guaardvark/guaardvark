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


# ── /object_info engine probe is cached ───────────────────────────────────────
# _is_model_downloaded -> _comfyui_assets_present hits the live engine list, and
# get_available_models calls it per model. Cache the list so a listing does not
# fan out one probe per row, and a down ComfyUI does not cost a timeout per row.

class _FakeResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {}


def test_engine_list_cached_between_calls(monkeypatch):
    from backend.services import comfyui_image_generator as cig

    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        return _FakeResp()

    monkeypatch.setattr(cig.requests, "get", fake_get)
    monkeypatch.setattr(cig, "_ENGINE_CACHE_TTL_SECONDS", 5.0)
    monkeypatch.setattr(cig, "_ENGINE_CACHE", {})

    gen = cig.ComfyUIImageGenerator(comfy_url="http://engine-cache.test")
    gen.comfyui_installed_engines()
    gen.comfyui_installed_engines()

    assert calls["n"] == 1


def test_unreachable_engine_probe_cached_empty(monkeypatch):
    from backend.services import comfyui_image_generator as cig

    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        raise cig.requests.exceptions.RequestException("down")

    monkeypatch.setattr(cig.requests, "get", fake_get)
    monkeypatch.setattr(cig, "_ENGINE_CACHE_TTL_SECONDS", 5.0)
    monkeypatch.setattr(cig, "_ENGINE_CACHE", {})

    gen = cig.ComfyUIImageGenerator(comfy_url="http://engine-down.test")
    assert gen.comfyui_installed_engines() == []
    assert gen.comfyui_installed_engines() == []

    assert calls["n"] == 1


def test_zimage_loras_override_a_non_zimage_model(monkeypatch):
    # A Z-Image-format LoRA paired with a non-Z-Image model must be corrected to
    # the Z-Image graph (not refused, not dropped) so a trained identity applies.
    # Requires the opt-in: without it the LoRA is never linked into ComfyUI.
    from backend.services import media_model_registry as mmr

    monkeypatch.setenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", "1")

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
    gen = ComfyUIImageGenerator(model="flux-schnell")
    wf = gen._build_workflow(
        prompt="sage_harlow, cinematic portrait", negative="",
        lora_names=["zimage_elara_v1.safetensors"], width=1024, height=1024,
        seed=1, steps=9, cfg=1.0, model="flux-schnell",
    )
    types = _class_types(wf)
    assert "UNETLoader" in types, "must be the Z-Image UNET, not the schnell GGUF"
    assert "ModelSamplingAuraFlow" in types
    assert "LoraLoaderModelOnly" in types, "the Z-Image LoRA must be applied model-only"
    assert wf["sampling"]["inputs"]["model"][0] == "lora_0"

def test_zimage_loras_refuse_a_non_zimage_model_without_optin(monkeypatch):
    # Without GUAARDVARK_ZIMAGE_USE_COMFYUI the LoRA is never linked into ComfyUI
    # (ensure_lora_in_comfyui returns early), so the reroute is refused up front
    # with a clear error instead of failing later inside ComfyUI.
    from backend.services import media_model_registry as mmr

    monkeypatch.delenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", raising=False)

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
    gen = ComfyUIImageGenerator(model="flux-schnell")
    with pytest.raises(RuntimeError, match="GUAARDVARK_ZIMAGE_USE_COMFYUI=1"):
        gen._build_workflow(
            prompt="sage_harlow, cinematic portrait", negative="",
            lora_names=["zimage_elara_v1.safetensors"], width=1024, height=1024,
            seed=1, steps=9, cfg=1.0, model="flux-schnell",
        )
