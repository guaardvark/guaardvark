"""MOSS-Image Edit's graph and chat routing, and LoRA prompts kept as typed.

The edit graph is the official Comfy-Org template (image_moss_image_edit): the
photo at ~1 MP on a 16 px grid, MOSS-Image SFT with the edit LoRA at 1.0 and
skip-layer guidance on block 22, TextEncodeMOSSImageEdit for both
conditionings, euler/normal. A Studio batch that carries LoRAs skips the Media
Director rewrite, which dropped a LoRA's trigger word.
"""
from types import SimpleNamespace

import pytest

try:
    from backend.services import comfyui_image_generator as cig
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.services import stills_policy
    from backend.services.batch_image_generator import BatchImageGenerator, BatchImageRequest, BatchPrompt
    from backend.tools.image_tools import EditImageTool
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def test_edit_graph_is_the_official_template():
    wf = ComfyUIImageGenerator()._build_moss_edit_workflow(
        src_name="photo.png", instruction="make the coat red", steps=30, cfg=4.0, seed=3)
    types = {n["class_type"] for n in wf.values()}
    assert types == {"LoadImage", "ImageScaleToTotalPixels", "GetImageSize", "EmptyFlux2LatentImage",
                     "UNETLoader", "LoraLoaderModelOnly", "SkipLayerGuidanceDiTSimple", "CLIPLoader",
                     "VAELoader", "TextEncodeMOSSImageEdit", "KSampler", "VAEDecode", "SaveImage"}
    assert wf["scaled"]["inputs"]["megapixels"] == 1.0 and wf["scaled"]["inputs"]["resolution_steps"] == 16
    assert wf["latent"]["inputs"]["width"] == ["size", 0] and wf["latent"]["inputs"]["height"] == ["size", 1]
    assert wf["edit_lora"]["inputs"] == {"model": ["unet", 0], "lora_name": cig.MOSS_EDIT_LORA, "strength_model": 1.0}
    assert (wf["slg"]["inputs"]["single_layers"], wf["slg"]["inputs"]["double_layers"]) == ("22", "")
    pos = wf["positive"]["inputs"]
    assert pos["prompt"] == "make the coat red" and pos["edit_format"] == "Moss-Image-Edit"
    assert pos["images.image_0"] == ["scaled", 0] and pos["vae"] == ["vae_loader", 0]
    assert wf["negative"]["inputs"]["prompt"] == cig.MOSS_NEGATIVE
    s = wf["sampler"]["inputs"]
    assert (s["steps"], s["cfg"], s["sampler_name"], s["scheduler"]) == (30, 4.0, "euler", "normal")
    assert s["model"] == ["slg", 0]


def test_moss_edit_is_picked_by_name_and_last_on_auto(monkeypatch):
    for name in ("moss-image-edit", "moss-edit", "moss-image"):
        assert EditImageTool._pick_edit_backend(name) == "moss"
    installed = {"qwen": False, "kontext": False, "moss": True}
    monkeypatch.setattr(ComfyUIImageGenerator, "qwen_edit_installed", lambda self: installed["qwen"])
    monkeypatch.setattr(ComfyUIImageGenerator, "_kontext_installed", lambda self: installed["kontext"])
    monkeypatch.setattr(ComfyUIImageGenerator, "moss_edit_installed", lambda self: installed["moss"])
    assert EditImageTool._pick_edit_backend("auto") == "moss"
    installed["kontext"] = True
    assert EditImageTool._pick_edit_backend("auto") == "kontext"
    installed["qwen"] = True
    assert EditImageTool._pick_edit_backend("auto") == "qwen"


def test_a_lora_batch_skips_the_director_rewrite(monkeypatch):
    calls = []

    def fake_enhance(prompts, **kwargs):
        calls.append(list(prompts))
        return [f"A realistic photograph: {p}" for p in prompts]

    monkeypatch.setattr(stills_policy, "apply_enhance_to_prompts", fake_enhance)
    typed = "moss_vgouache_style, a lighthouse at dusk"
    p = BatchPrompt(id="1", prompt=typed, model="moss-image", adapter_loras=["/loras/gouache.safetensors"])
    req = BatchImageRequest(batch_id="b1", prompts=[p], output_dir="/nonexistent", director_mode=True)
    BatchImageGenerator._apply_director(SimpleNamespace(_remember_typed_prompt=lambda bp: None), req)
    assert p.prompt == typed and calls == []

    # Without LoRAs the same batch is rewritten as before.
    q = BatchPrompt(id="2", prompt=typed, model="moss-image")
    req = BatchImageRequest(batch_id="b2", prompts=[q], output_dir="/nonexistent", director_mode=True)
    BatchImageGenerator._apply_director(SimpleNamespace(_remember_typed_prompt=lambda bp: None), req)
    assert q.prompt.startswith("A realistic photograph") and calls
