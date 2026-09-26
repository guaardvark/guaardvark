"""HunyuanVideo graph contract: text- and image-to-video (GGUF UNet).

Every graph is checked against the ComfyUI /object_info snapshot, and the
values a request resolves to are followed into the nodes that use them.
Known gaps are strict xfails, each naming the gap.
"""
import pytest

from backend.services.video_model_registry import hunyuan_comfyui_map, model_capabilities, snap_frames
from backend.tests.fixtures import workflow_contract as wc
from backend.tests.fixtures.workflow_contract import comfy  # noqa: F401 — fixture

MODELS = ("hunyuan-t2v", "hunyuan-i2v")
LORAS = [{"filename": "style_a.safetensors", "strength": 0.6}, {"filename": "style_b.safetensors", "strength": 0.0}]


def _build(model, **kw):
    gen = wc.builder()
    args = dict(prompt="a red fox in snow", model_key=model, seed=7, width=1328, height=736, num_frames=49, fps=24)
    args.update(kw)
    if model == "hunyuan-i2v":
        return gen._create_hunyuan_i2v_workflow(image_filename="start.png", **args)
    return gen._create_hunyuan_t2v_workflow(**args)


def _request(comfy, model, **fields):
    if model == "hunyuan-i2v":
        fields.setdefault("metadata", {"image_path": comfy.image})
    fields.setdefault("fps", model_capabilities(model)["native_fps"])
    fields.setdefault("interpolation_multiplier", 1)
    return comfy.render(model=model, prompt="a red fox in snow", **fields)


# ── Builder graphs ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("variant", [
    {},
    {"interpolation_multiplier": 2},
    {"extra_loras": LORAS},
    {"text_encoder": "my_llava.safetensors"},
])
def test_builder_graph_is_valid(model, variant):
    wf = _build(model, **variant)
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)


@pytest.mark.parametrize("model", MODELS)
def test_user_loras_reach_the_sampler_with_their_strength(model):
    wf = _build(model, extra_loras=LORAS)
    wc.assert_loras_on_every_model_input(wf, [l["filename"] for l in LORAS])
    assert wc.lora_strengths(wf) == {"style_a.safetensors": 0.6, "style_b.safetensors": 0.0}


@pytest.mark.parametrize("model", MODELS)
def test_loaders_name_the_files_the_registry_installs(model):
    wf = _build(model, interpolation_multiplier=1)
    files = {v for k, v in wc.loader_files(wf).items() if wf[k[0]]["class_type"] != "LoadImage"}
    assert files == {v for k, v in hunyuan_comfyui_map()[model].items() if k != "type" and v}
    assert files <= wc.registry_files(model)


# ── Rendered graphs: what generate_video resolved ────────────────────────────

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("ratio", wc.RATIOS_WHEN_UNDECLARED)
def test_rendered_canvas_matches_the_resolved_request(comfy, model, ratio):
    caps = model_capabilities(model)
    width, height = wc.ratio_dims(ratio, caps["max_pixel_area"], caps["dimension_alignment"])
    result, wf, req = _request(comfy, model, width=width, height=height, duration_frames=49)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    wc.assert_resolved_canvas(model, req, ratio)
    [(_, w, h, frames)] = wc.canvas(wf)
    assert (w, h, frames) == (req.width, req.height, 49)
    assert wc.output_fps(wf) == req.fps


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("which", ("min", "max"))
def test_rendered_frames_on_grid_values(comfy, model, which):
    frames = snap_frames(model, 9, up=True) if which == "min" else model_capabilities(model)["max_frames"]
    result, wf, req = _request(comfy, model, duration_frames=frames, width=848, height=480)
    assert wf is not None, result.error
    [(_, _, _, length)] = wc.canvas(wf)
    assert length == frames


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason=(
    "Hunyuan rounds to the nearest 4n+1, so a request can "
    "come back longer; the registry's snap_frames never lengthens"))
def test_off_grid_frames_are_never_lengthened(comfy, model):
    result, wf, req = _request(comfy, model, duration_frames=75, width=848, height=480)
    [(_, _, _, length)] = wc.canvas(wf)
    assert wc.on_frame_grid(model, length) and length <= 75


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason="generate_video does not enforce max_frames")
def test_rendered_frames_never_exceed_max_frames(comfy, model):
    caps = model_capabilities(model)
    result, wf, req = _request(comfy, model, duration_frames=caps["max_frames"] + 40, width=848, height=480)
    [(_, _, _, length)] = wc.canvas(wf)
    assert length <= caps["max_frames"]


@pytest.mark.parametrize("model", MODELS)
def test_prompt_guidance_and_seed_land_in_the_sampler(comfy, model):
    result, wf, req = _request(comfy, model, width=848, height=480, duration_frames=49,
                               guidance_scale=6.0, seed=4242)
    assert wf is not None, result.error
    _, guider = wc.one(wf, "BasicGuider")
    assert wc.encoded_text(wf, guider["inputs"]["conditioning"]) == "a red fox in snow"
    assert wc.source(wf, guider["inputs"]["conditioning"])["inputs"]["guidance"] == 6.0
    assert wc.one(wf, "RandomNoise")[1]["inputs"]["noise_seed"] == 4242
    # The graph has no negative branch; a negative prompt has nowhere to go.
    assert "CFGGuider" not in wc.class_types(wf)
    if model == "hunyuan-i2v":
        assert wc.one(wf, "LoadImage")[1]["inputs"]["image"] == comfy.fake.uploads[-1]


@pytest.mark.parametrize("model", MODELS)
def test_optional_post_nodes_chain_into_the_video(comfy, monkeypatch, model):
    fields = {"metadata": {"image_path": comfy.image}} if model == "hunyuan-i2v" else {}
    fps = model_capabilities(model)["native_fps"]
    result, wf, req = wc.optional_features_render(comfy, monkeypatch, model, fps=fps, width=848, height=480,
                                                  duration_frames=49, **fields)
    assert wf is not None, result.error
    wc.assert_optional_features(wf, fps)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("backend,pinned", [("ck", True), ("pytorch", False)])
def test_attention_pinned_where_the_launch_backend_is_not_verified(comfy, monkeypatch, model, backend, pinned):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_ATTENTION", backend)
    result, wf, req = _request(comfy, model, width=848, height=480, duration_frames=49)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)
    _, guider = wc.one(wf, "BasicGuider")
    assert ("ModelAttentionBackend" in wc.model_path_classes(wf, guider["inputs"]["model"])) is pinned
