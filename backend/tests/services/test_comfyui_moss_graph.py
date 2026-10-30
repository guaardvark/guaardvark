"""The MOSS-Image text-to-image graph and its ComfyUI node check.

The graph is the official Comfy-Org template (image_moss_image_t2i) with its
prompt enhancer off: UNETLoader + CLIPLoader(moss_image) + VAELoader,
CLIPTextEncode for both conditionings, EmptyFlux2LatentImage, KSampler
er_sde/simple at 25 steps / cfg 2.5. The model is in ComfyUI from 0.42; the
generator checks the running server's /object_info before queueing.
"""
import pytest

try:
    from backend.services import comfyui_image_generator as cig
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def _build(model="moss-image", **kw):
    args = dict(prompt="a fox in the snow", negative="", lora_names=[], width=1024, height=1024,
                seed=7, steps=0, cfg=0.0, model=model)
    args.update(kw)
    return ComfyUIImageGenerator(model=model)._build_workflow(**args)


def _types(wf):
    return {n["class_type"] for n in wf.values()}


def test_graph_is_the_official_template():
    wf = _build()
    assert _types(wf) == {
        "UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "EmptyFlux2LatentImage",
        "KSampler", "VAEDecode", "SaveImage",
    }
    assert wf["unet"]["inputs"] == {"unet_name": cig.MOSS_UNET, "weight_dtype": "default"}
    assert wf["clip"]["inputs"] == {"clip_name": cig.MOSS_CLIP, "type": "moss_image"}
    assert wf["vae_loader"]["inputs"] == {"vae_name": cig.MOSS_VAE}
    assert wf["positive"]["inputs"] == {"clip": ["clip", 0], "text": "a fox in the snow"}
    s = wf["sampler"]["inputs"]
    assert s["model"] == ["unet", 0]
    assert s["positive"] == ["positive", 0] and s["negative"] == ["negative", 0]
    assert (s["sampler_name"], s["scheduler"], s["denoise"]) == ("er_sde", "simple", 1.0)
    assert wf["vae"]["inputs"] == {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}
    # No FLUX or Qwen-graph nodes leak in.
    assert not _types(wf) & {"FluxGuidance", "DualCLIPLoader", "UnetLoaderGGUF", "TextEncodeQwenImage21"}


def test_defaults_are_the_templates_and_a_typed_value_wins():
    s = _build()["sampler"]["inputs"]
    assert (s["steps"], s["cfg"]) == (25, 2.5)
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
    entry = VIDEO_MODEL_REGISTRY["moss-image"]
    assert (entry["default_steps"], entry["default_cfg"]) == (cig.MOSS_DEFAULT_STEPS, cig.MOSS_DEFAULT_CFG)
    s = _build(steps=50, cfg=3.0)["sampler"]["inputs"]
    assert (s["steps"], s["cfg"]) == (50, 3.0)


def test_sides_snap_to_multiples_of_16():
    lat = _build(width=1000, height=1030)["latent"]["inputs"]
    assert (lat["width"], lat["height"], lat["batch_size"]) == (992, 1024, 1)
    lat = _build(width=1920, height=1088)["latent"]["inputs"]
    assert (lat["width"], lat["height"]) == (1920, 1088)


def test_the_generic_negative_becomes_the_templates():
    assert _build(negative=cig.DEFAULT_NEGATIVE)["negative"]["inputs"]["text"] == cig.MOSS_NEGATIVE
    assert _build(negative="")["negative"]["inputs"]["text"] == cig.MOSS_NEGATIVE
    assert _build(negative="blurry, text")["negative"]["inputs"]["text"] == "blurry, text"


def test_user_ids_and_their_unet_reach_the_graph():
    gen = ComfyUIImageGenerator(model="user-moss-image-sft-bf16", moss_unet="moss_image_sft_bf16.safetensors")
    wf = gen._build_workflow(prompt="p", negative="", lora_names=[], width=1024, height=1024,
                             seed=1, steps=25, cfg=2.5, model="user-moss-image-sft-bf16")
    assert wf["unet"]["inputs"]["unet_name"] == "moss_image_sft_bf16.safetensors"
    assert wf["clip"]["inputs"]["type"] == "moss_image"


def test_loras_chain_model_only_between_loader_and_sampler():
    gen = ComfyUIImageGenerator(model="moss-image", lora_strength=0.9)
    wf = gen._build_workflow(prompt="p", negative="", lora_names=["style_a.safetensors", "style_b.safetensors"],
                             width=1024, height=1024, seed=1, steps=25, cfg=2.5, model="moss-image")
    assert wf["lora_0"] == {"class_type": "LoraLoaderModelOnly", "inputs": {
        "model": ["unet", 0], "lora_name": "style_a.safetensors", "strength_model": 0.9}}
    assert wf["lora_1"]["inputs"]["model"] == ["lora_0", 0]
    assert wf["sampler"]["inputs"]["model"] == ["lora_1", 0]
    # The encoder is untouched: MOSS-Image LoRAs train the transformer only.
    assert wf["positive"]["inputs"]["clip"] == ["clip", 0]
    # No LoRA, no LoRA nodes.
    assert not any(k.startswith("lora_") for k in _build())


def test_other_graphs_are_untouched():
    assert "TextEncodeQwenImage21" in _types(_build(model="qwen-image-21", steps=25, cfg=1.0))
    wf = _build(model="flux-dev", steps=28, cfg=3.5)
    assert "FluxGuidance" in _types(wf) and "EmptyFlux2LatentImage" not in _types(wf)


# ── the node check against /object_info ──────────────────────────────────────

class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _serve(monkeypatch, payload):
    monkeypatch.setattr(cig, "_ENGINE_CACHE_TTL_SECONDS", 5.0)
    monkeypatch.setattr(cig, "_ENGINE_CACHE", {})

    def fake_get(*args, **kwargs):
        return _Resp(payload)

    monkeypatch.setattr(cig.requests, "get", fake_get)


# ComfyUI 0.39 has the FLUX.2 latent node but not the MOSS-Image model.
COMFY_039 = {"UNETLoader": {}, "CLIPLoader": {}, "VAELoader": {}, "KSampler": {}, "EmptyFlux2LatentImage": {}}
COMFY_042 = {**COMFY_039, "MOSSImageLayoutPrompt": {}}


def test_required_nodes_come_from_the_registry_entry():
    gen = ComfyUIImageGenerator()
    assert gen.required_node_classes("moss-image") == ("MOSSImageLayoutPrompt", "EmptyFlux2LatentImage")
    assert gen.required_node_classes("user-moss-image-x") == ("MOSSImageLayoutPrompt", "EmptyFlux2LatentImage")
    assert gen.required_node_classes("qwen-image-21") == ("TextEncodeQwenImage21", "QwenImage21Cache")


def test_comfyui_039_is_refused_with_the_version_that_has_the_model(monkeypatch):
    _serve(monkeypatch, COMFY_039)
    gen = ComfyUIImageGenerator(comfy_url="http://comfy-039.test")
    with pytest.raises(RuntimeError, match="MOSS-Image needs ComfyUI 0.42 or newer") as exc:
        gen.check_graph_nodes("moss-image")
    assert "MOSSImageLayoutPrompt" in str(exc.value)
    # The Qwen message still names its own model and release.
    with pytest.raises(RuntimeError, match="Qwen-Image 2.1 needs ComfyUI 0.37 or newer"):
        gen.check_graph_nodes("qwen-image-21")


def test_comfyui_042_passes(monkeypatch):
    _serve(monkeypatch, COMFY_042)
    gen = ComfyUIImageGenerator(comfy_url="http://comfy-042.test")
    gen.check_graph_nodes("moss-image")
