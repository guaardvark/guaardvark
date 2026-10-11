"""The Qwen-Image 2.1 text-to-image graph and its ComfyUI node check.

The graph is the official Comfy-Org template (image_qwen_image_2_1_t2i):
UNETLoader + CLIPLoader(qwen_image) + VAELoader, QwenImage21Cache on the model,
TextEncodeQwenImage21 for both conditionings, EmptyLatentImage, KSampler
euler/simple at 25 steps / cfg 1.0. The nodes exist from ComfyUI 0.37; the
generator checks the running server's /object_info before queueing.
"""
import pytest

try:
    from backend.services import comfyui_image_generator as cig
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def _build(model="qwen-image-21", **kw):
    args = dict(prompt="a fox in the snow", negative="", lora_names=[], width=1024, height=1024,
                seed=7, steps=0, cfg=0.0, model=model)
    args.update(kw)
    return ComfyUIImageGenerator(model=model)._build_workflow(**args)


def _types(wf):
    return {n["class_type"] for n in wf.values()}


def test_graph_is_the_official_template():
    wf = _build()
    assert _types(wf) == {
        "UNETLoader", "CLIPLoader", "VAELoader", "QwenImage21Cache", "TextEncodeQwenImage21",
        "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage",
    }
    assert wf["unet"]["inputs"] == {"unet_name": cig.QWEN21_UNET, "weight_dtype": "default"}
    assert wf["clip"]["inputs"] == {"clip_name": cig.QWEN21_CLIP, "type": "qwen_image"}
    assert wf["vae_loader"]["inputs"] == {"vae_name": cig.QWEN21_VAE}
    assert wf["cache"]["inputs"] == {"model": ["unet", 0], "device": "auto", "dtype": "default"}
    enc = wf["encode"]["inputs"]
    assert enc["clip"] == ["clip", 0] and enc["prompt"] == "a fox in the snow"
    assert enc["negative_prompt"] == "" and enc["resolution"] == 1024
    s = wf["sampler"]["inputs"]
    assert s["model"] == ["cache", 0]
    assert s["positive"] == ["encode", 0] and s["negative"] == ["encode", 1]
    assert (s["sampler_name"], s["scheduler"], s["denoise"]) == ("euler", "simple", 1.0)
    assert wf["vae"]["inputs"] == {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}
    # No FLUX or edit-graph nodes leak in.
    assert not _types(wf) & {"FluxGuidance", "ModelSamplingAuraFlow", "CFGNorm", "DualCLIPLoader", "UnetLoaderGGUF"}


def test_defaults_are_the_templates_and_a_typed_value_wins():
    s = _build()["sampler"]["inputs"]
    assert (s["steps"], s["cfg"]) == (25, 1.0)
    assert (cig.QWEN21_DEFAULT_STEPS, cig.QWEN21_DEFAULT_CFG) == (25, 1.0)
    # The registry declares the defaults; the graph reads them.
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
    assert VIDEO_MODEL_REGISTRY["qwen-image-21"]["default_steps"] == cig.QWEN21_DEFAULT_STEPS
    s = _build(steps=40, cfg=3.0)["sampler"]["inputs"]
    assert (s["steps"], s["cfg"]) == (40, 3.0)
    # No floor: a count a person typed stands, even a low one.
    s = _build(steps=4, cfg=1.0, steps_explicit=True)["sampler"]["inputs"]
    assert s["steps"] == 4
    s = _build(steps=8)["sampler"]["inputs"]
    assert s["steps"] == 8


def test_sides_snap_to_multiples_of_32():
    lat = _build(width=1000, height=1000)["latent"]["inputs"]
    assert (lat["width"], lat["height"], lat["batch_size"]) == (992, 992, 1)
    lat = _build(width=2048, height=1152)["latent"]["inputs"]
    assert (lat["width"], lat["height"]) == (2048, 1152)


def test_negative_is_empty_unless_one_was_asked_for():
    assert _build(negative=cig.DEFAULT_NEGATIVE)["encode"]["inputs"]["negative_prompt"] == ""
    assert _build(negative="blurry, text")["encode"]["inputs"]["negative_prompt"] == "blurry, text"


def test_user_ids_and_their_unet_reach_the_graph():
    gen = ComfyUIImageGenerator(model="user-qwen-image-21-ft-fp8", qwen21_unet="qwen-image-2.1-FT-fp8.safetensors")
    wf = gen._build_workflow(prompt="p", negative="", lora_names=[], width=1024, height=1024,
                             seed=1, steps=25, cfg=1.0, model="user-qwen-image-21-ft-fp8")
    assert wf["unet"]["inputs"]["unet_name"] == "qwen-image-2.1-FT-fp8.safetensors"
    assert "TextEncodeQwenImage21" in _types(wf)


def test_loras_on_qwen_are_refused_not_dropped():
    # The graph has no LoRA input; a LoRA request must fail plainly before queueing.
    gen = ComfyUIImageGenerator(model="qwen-image-21")
    with pytest.raises(RuntimeError, match="do not take LoRAs"):
        gen._build_workflow(prompt="p", negative="", lora_names=["style_v1.safetensors"],
                            width=1024, height=1024, seed=1, steps=25, cfg=1.0, model="qwen-image-21")
    with pytest.raises(RuntimeError, match="do not take LoRAs"):
        gen._build_workflow(prompt="p", negative="", lora_names=["style_v1.safetensors"],
                            width=1024, height=1024, seed=1, steps=25, cfg=1.0,
                            model="user-qwen-image-21-ft-fp8")


def test_flux_graphs_are_untouched():
    wf = _build(model="flux-dev", steps=28, cfg=3.5)
    assert "FluxGuidance" in _types(wf) and "TextEncodeQwenImage21" not in _types(wf)
    wf = _build(model="flux-schnell", steps=8, cfg=1.0)
    assert "UnetLoaderGGUF" in _types(wf) and "QwenImage21Cache" not in _types(wf)


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
    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        if isinstance(payload, Exception):
            raise payload
        return _Resp(payload)

    monkeypatch.setattr(cig.requests, "get", fake_get)
    return calls


OLD_COMFY = {"UNETLoader": {}, "CLIPLoader": {}, "VAELoader": {}, "KSampler": {}}
NEW_COMFY = {**OLD_COMFY, "TextEncodeQwenImage21": {}, "QwenImage21Cache": {}}


def test_required_nodes_come_from_the_registry_entry():
    gen = ComfyUIImageGenerator()
    assert gen.required_node_classes("qwen-image-21") == ("TextEncodeQwenImage21", "QwenImage21Cache")
    assert gen.required_node_classes("user-qwen-image-21-x") == ("TextEncodeQwenImage21", "QwenImage21Cache")
    assert gen.required_node_classes("flux-dev") == ()
    assert gen.required_node_classes("sdxl") == ()


def test_an_old_comfyui_is_refused_with_the_version_that_has_the_nodes(monkeypatch):
    _serve(monkeypatch, OLD_COMFY)
    gen = ComfyUIImageGenerator(comfy_url="http://old-comfy.test")
    assert gen.missing_node_classes(cig.QWEN21_NODES) == ["TextEncodeQwenImage21", "QwenImage21Cache"]
    with pytest.raises(RuntimeError, match="ComfyUI 0.37 or newer") as exc:
        gen.check_graph_nodes("qwen-image-21")
    assert "TextEncodeQwenImage21" in str(exc.value)
    # FLUX asks for nothing beyond core and passes on the same server.
    gen.check_graph_nodes("flux-dev")


def test_a_comfyui_with_the_nodes_passes(monkeypatch):
    _serve(monkeypatch, NEW_COMFY)
    gen = ComfyUIImageGenerator(comfy_url="http://new-comfy.test")
    assert gen.missing_node_classes(cig.QWEN21_NODES) == []
    gen.check_graph_nodes("qwen-image-21")


def test_an_unanswered_probe_does_not_refuse(monkeypatch):
    # ComfyUI's own validation reports a missing node when we could not ask.
    _serve(monkeypatch, cig.requests.exceptions.RequestException("down"))
    gen = ComfyUIImageGenerator(comfy_url="http://down-comfy.test")
    assert gen.missing_node_classes(cig.QWEN21_NODES) == []
    gen.check_graph_nodes("qwen-image-21")


def test_graphs_without_declared_nodes_make_no_probe(monkeypatch):
    calls = _serve(monkeypatch, OLD_COMFY)
    gen = ComfyUIImageGenerator(comfy_url="http://no-probe.test")
    for model in ("flux-dev", "flux-schnell", "sdxl", "zimage"):
        gen.check_graph_nodes(model)
    assert gen.missing_node_classes(()) == []
    assert calls["n"] == 0


def test_a_cached_empty_probe_is_retried_before_trusting_it(monkeypatch):
    # The engine list cached None while ComfyUI was starting; a Qwen render a
    # moment later must ask again instead of letting ComfyUI's raw error through.
    answers = [cig.requests.exceptions.RequestException("starting"), OLD_COMFY]
    monkeypatch.setattr(cig, "_ENGINE_CACHE_TTL_SECONDS", 5.0)
    monkeypatch.setattr(cig, "_ENGINE_CACHE", {})
    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _Resp(answer)

    monkeypatch.setattr(cig.requests, "get", fake_get)
    gen = ComfyUIImageGenerator(comfy_url="http://booting-comfy.test")
    assert gen.comfyui_installed_engines() == []          # cached None
    with pytest.raises(RuntimeError, match="ComfyUI 0.37 or newer"):
        gen.check_graph_nodes("qwen-image-21")             # fresh probe, old server
    assert calls["n"] == 2


def test_generate_image_refuses_before_queueing(monkeypatch):
    _serve(monkeypatch, OLD_COMFY)
    gen = ComfyUIImageGenerator(comfy_url="http://old-comfy.test")
    monkeypatch.setattr(gen, "_require_up", lambda msg: None)
    monkeypatch.setattr(gen, "_queue", lambda wf: pytest.fail("the graph must not be queued"))
    with pytest.raises(RuntimeError, match="ComfyUI 0.37 or newer"):
        gen.generate_image(prompt="p", output_path="/nonexistent/out.png", model="qwen-image-21")


def test_engine_list_and_node_check_share_one_object_info_probe(monkeypatch):
    calls = _serve(monkeypatch, NEW_COMFY)
    gen = ComfyUIImageGenerator(comfy_url="http://shared-probe.test")
    gen.comfyui_installed_engines()
    gen.missing_node_classes(cig.QWEN21_NODES)
    gen.check_graph_nodes("qwen-image-21")
    assert calls["n"] == 1


def test_admission_wraps_the_render_like_an_edit(monkeypatch, tmp_path):
    _serve(monkeypatch, NEW_COMFY)
    gen = ComfyUIImageGenerator(comfy_url="http://new-comfy.test")
    monkeypatch.setattr(gen, "_require_up", lambda msg: None)
    seen = {}

    def fake_run(workflow, output_path, *, job, vram_mb, timeout, gpu_wait=None):
        seen.update(job=job, vram_mb=vram_mb, workflow=workflow)
        return output_path

    monkeypatch.setattr(gen, "_run_edit_graph", fake_run)
    monkeypatch.setattr(gen, "_queue", lambda wf: pytest.fail("admitted renders go through _run_edit_graph"))
    out = gen.generate_image(prompt="p", output_path=str(tmp_path / "o.png"), model="qwen-image-21",
                             steps=25, cfg=1.0, admit_vram_mb=14000)
    assert out == str(tmp_path / "o.png")
    assert seen["vram_mb"] == 14000 and seen["job"] == "stills_comfy"
    assert "TextEncodeQwenImage21" in _types(seen["workflow"])
    assert gen.last_steps == 25
