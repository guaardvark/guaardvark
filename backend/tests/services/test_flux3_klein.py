"""FLUX 3 Image Klein: its graph, its family and the FLUX.1 names it shares.

The graph is the official Comfy-Org distilled template
(image_flux3_klein_distilled_t2i) with the prompt upsampler on, as the model
card asks. Every FLUX 3 id and file name contains "flux", so FLUX.1's row and
the "flux" substring checks must not claim them; "klein" alone also names
FLUX.2 Klein, a different model.
"""
import pytest

try:
    from backend.services import comfyui_image_generator as cig
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.services import user_model_families as F
    from backend.services.image_render_limits import family_of, defaults_for
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def _build(monkeypatch, **kw):
    monkeypatch.setattr(cig, "_flux3_upsampler_instructions", lambda: "You are the prompt upsampler.")
    args = dict(prompt="a fox in the snow", negative="", lora_names=[], width=1024, height=1024,
                seed=7, steps=0, cfg=None, model="flux3-klein")
    args.update(kw)
    return ComfyUIImageGenerator(model="flux3-klein")._build_workflow(**args)


def test_graph_is_the_official_distilled_template_with_the_upsampler(monkeypatch):
    wf = _build(monkeypatch)
    assert wf["clip"]["inputs"] == {"clip_name": cig.FLUX3_CLIP, "type": "flux3"}
    assert wf["upsampler_clip"]["inputs"] == {"clip_name": cig.FLUX3_UPSAMPLER, "type": "flux3"}
    up = wf["upsample"]["inputs"]
    assert up["clip"] == ["upsampler_clip", 0] and up["prompt"] == "a fox in the snow"
    assert up["system_prompt"] == "You are the prompt upsampler." and up["max_length"] == 3584
    assert wf["positive"]["inputs"]["text"] == ["upsample", 0]
    s = wf["sampler"]["inputs"]
    assert (s["steps"], s["cfg"], s["sampler_name"], s["scheduler"]) == (15, 1.0, "er_sde", "simple")
    assert wf["latent"]["class_type"] == "EmptyFlux2LatentImage"
    # Distilled: a typed guidance is not used.
    assert _build(monkeypatch, cfg=5.0)["sampler"]["inputs"]["cfg"] == 1.0


def test_no_upsampler_instructions_refuses_instead_of_sampling_without(monkeypatch):
    monkeypatch.setattr(cig, "_flux3_upsampler_instructions", lambda: None)
    with pytest.raises(RuntimeError, match="prompt upsampler"):
        ComfyUIImageGenerator(model="flux3-klein")._build_workflow(
            prompt="p", negative="", lora_names=[], width=1024, height=1024, seed=1, steps=15, cfg=1.0,
            model="flux3-klein")


def test_flux3_names_are_not_flux1():
    assert F.comfy_t2i_family("flux3-klein") == "flux3-klein"
    assert F.comfy_t2i_family("flux-dev") == "flux"
    assert family_of("flux3-klein") == "flux3-klein" and family_of("flux-dev") == "flux"
    files = [{"src": "diffusion_models/flux3-image-klein-int8-convrot.safetensors"},
             {"src": "vae/flux2-vae.safetensors"}]
    got = F.match_families(domain="image", files=files, src=None, hf_repo="black-forest-labs/flux-3-image-klein-comfy")
    assert [(m["family"], m["wired"]) for m in got] == [("flux3-klein", True)]
    d = defaults_for("flux3-klein", strict=False)
    assert (d["steps"], d["guidance"], d["prompt_style"]) == (15, 1.0, "natural")


def test_klein_files_it_cannot_sample_are_refused():
    assert "Blackwell" in F.weight_file_problem("flux3-klein", "flux3-image-klein-nvfp4.safetensors")
    assert "base" in F.weight_file_problem("flux3-klein", "flux3-image-klein-base-int8-convrot.safetensors")
    assert F.weight_file_problem("flux3-klein", "flux3-image-klein-int8-convrot.safetensors") is None
