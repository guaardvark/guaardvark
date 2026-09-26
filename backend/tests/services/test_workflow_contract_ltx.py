"""LTX graph contract: LTX 2.3 (one pass) and LTX 2.5 (half-size pass, ×2
latent upsample, refine pass), text- and image-to-video.

Every graph is checked against the ComfyUI /object_info snapshot, and the
values a request resolves to are followed into the nodes that use them.
Known gaps are strict xfails, each naming the gap.
"""
import pytest

from backend.services.video_model_registry import ltx_comfyui_map, model_capabilities, snap_frames
from backend.tests.fixtures import workflow_contract as wc
from backend.tests.fixtures.workflow_contract import comfy  # noqa: F401 — fixture

MODELS = ("ltx23-distilled-fp8", "ltx25-distilled-int8")
MODES = ("t2v", "i2v")
LORAS = [{"filename": "style_a.safetensors", "strength": 0.6}, {"filename": "style_b.safetensors", "strength": 0.0}]


def _build(model, mode, **kw):
    gen = wc.builder()
    args = dict(prompt="a red fox in snow", negative_prompt="blurry", model_key=model, seed=7,
                width=1344, height=768, num_frames=49, fps=16.0)
    args.update(kw)
    v = "25" if model.startswith("ltx25") else "23"
    if mode == "i2v":
        return getattr(gen, f"_create_ltx{v}_i2v_workflow")(image_filename="start.png", **args)
    return getattr(gen, f"_create_ltx{v}_t2v_workflow")(**args)


def _request(comfy, model, mode, **fields):
    if mode == "i2v":
        fields.setdefault("metadata", {"image_path": comfy.image})
    fields.setdefault("fps", model_capabilities(model)["native_fps"])
    fields.setdefault("interpolation_multiplier", 1)
    return comfy.render(model=model, prompt="a red fox in snow", **fields)


def _output_size(model, wf):
    """(width, height) of the decoded clip: the latent's size, doubled by the
    2.5 upsampler."""
    [(_, w, h, _)] = wc.canvas(wf)
    return (w * 2, h * 2) if model.startswith("ltx25") else (w, h)


# ── Builder graphs ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("variant", [
    {},
    {"interpolation_multiplier": 2},
    {"audio_out": True},
    {"extra_loras": LORAS},
    {"text_encoder": "my_gemma.safetensors"},
])
def test_builder_graph_is_valid(model, mode, variant):
    wf = _build(model, mode, **variant)
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("audio_out", (True, False))
def test_audio_track_follows_the_audio_out_flag(model, mode, audio_out):
    wf = _build(model, mode, audio_out=audio_out)
    wc.assert_valid(wf)
    audio = wc.video_output(wf)[1]["inputs"].get("audio")
    if audio_out:
        assert wc.source(wf, audio)["class_type"] == "LTXVAudioVAEDecode"
    else:
        assert audio is None


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", MODES)
def test_user_loras_reach_every_sampler_with_their_strength(model, mode):
    wf = _build(model, mode, extra_loras=LORAS)
    wc.assert_loras_on_every_model_input(wf, [l["filename"] for l in LORAS])
    assert wc.lora_strengths(wf) == {"style_a.safetensors": 0.6, "style_b.safetensors": 0.0}


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", MODES)
def test_loaders_name_the_files_the_registry_installs(model, mode):
    wf = _build(model, mode, interpolation_multiplier=1)
    files = {v for k, v in wc.loader_files(wf).items() if wf[k[0]]["class_type"] != "LoadImage"}
    assert files == {v for k, v in ltx_comfyui_map()[model].items() if k != "type"}
    assert files <= wc.registry_files(model)


# ── Rendered graphs: what generate_video resolved ────────────────────────────

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("ratio", wc.RATIOS_WHEN_UNDECLARED)
def test_rendered_canvas_matches_the_resolved_request(comfy, model, mode, ratio):
    caps = model_capabilities(model)
    width, height = wc.ratio_dims(ratio, caps["max_pixel_area"], caps["dimension_alignment"] * 2)
    result, wf, req = _request(comfy, model, mode, width=width, height=height, duration_frames=49)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    wc.assert_resolved_canvas(model, req, ratio)
    assert _output_size(model, wf) == (req.width, req.height)
    assert wc.output_fps(wf) == req.fps


def test_ltx25_output_size_equals_the_request(comfy):
    result, wf, req = _request(comfy, "ltx25-distilled-int8", "t2v", width=800, height=480, duration_frames=49)
    assert _output_size("ltx25-distilled-int8", wf) == (req.width, req.height)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("which", ("min", "max", "off_grid"))
def test_rendered_frames_land_on_the_grid(comfy, model, which):
    caps = model_capabilities(model)
    frames = {"min": snap_frames(model, 9, up=True), "max": caps["max_frames"], "off_grid": 52}[which]
    result, wf, req = _request(comfy, model, "t2v", duration_frames=frames, width=832, height=480)
    assert wf is not None, result.error
    [(_, _, _, length)] = wc.canvas(wf)
    assert wc.on_frame_grid(model, length) and length <= frames
    assert length == snap_frames(model, frames)
    _, audio = wc.one(wf, "LTXVEmptyLatentAudio")
    assert audio["inputs"]["frames_number"] == length


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason="generate_video does not enforce max_frames")
def test_rendered_frames_never_exceed_max_frames(comfy, model):
    caps = model_capabilities(model)
    result, wf, req = _request(comfy, model, "t2v", duration_frames=caps["max_frames"] + 40, width=832, height=480)
    [(_, _, _, length)] = wc.canvas(wf)
    assert length <= caps["max_frames"]


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", MODES)
def test_prompt_negative_and_seed_land_in_the_samplers(comfy, model, mode):
    result, wf, req = _request(comfy, model, mode, width=832, height=480, duration_frames=49,
                               negative_prompt="washed out, clip art", seed=4242)
    assert wf is not None, result.error
    guides = wc.nodes(wf, "CFGGuider") or wc.nodes(wf, "KSampler")
    for _, guide in guides:
        assert wc.encoded_text(wf, guide["inputs"]["positive"]) == "a red fox in snow"
        assert wc.encoded_text(wf, guide["inputs"]["negative"]) == "washed out, clip art"
    if model.startswith("ltx25"):
        assert sorted(n["inputs"]["noise_seed"] for _, n in wc.nodes(wf, "RandomNoise")) == [4242, 4243]
    else:
        assert wc.one(wf, "KSampler")[1]["inputs"]["seed"] == 4242
    if mode == "i2v":
        assert wc.one(wf, "LoadImage")[1]["inputs"]["image"] == comfy.fake.uploads[-1]


@pytest.mark.parametrize("model", MODELS)
def test_ltx23_steps_reach_the_sampler_and_ltx25_keeps_its_schedule(comfy, model):
    result, wf, req = _request(comfy, model, "t2v", width=832, height=480, duration_frames=49, num_inference_steps=12)
    if model.startswith("ltx25"):
        # The distilled 2.5 schedule is fixed (8 + 3 sigmas); the request's steps do not apply.
        assert not wc.nodes(wf, "KSampler")
        assert len(wc.nodes(wf, "ManualSigmas")) == 2
    else:
        assert wc.one(wf, "KSampler")[1]["inputs"]["steps"] == 12


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", MODES)
def test_optional_post_nodes_chain_into_the_video(comfy, monkeypatch, model, mode):
    fields = {"metadata": {"image_path": comfy.image}} if mode == "i2v" else {}
    fps = model_capabilities(model)["native_fps"]
    result, wf, req = wc.optional_features_render(comfy, monkeypatch, model, fps=fps, width=832, height=480,
                                                  duration_frames=49, **fields)
    assert wf is not None, result.error
    wc.assert_optional_features(wf, fps)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("backend,pinned", [("ck", True), ("pytorch", False)])
def test_attention_pinned_where_the_launch_backend_is_not_verified(comfy, monkeypatch, model, mode, backend, pinned):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_ATTENTION", backend)
    result, wf, req = _request(comfy, model, mode, width=832, height=480, duration_frames=49)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)
    pins = wc.nodes(wf, "ModelAttentionBackend")
    assert bool(pins) is pinned
    samplers = wc.nodes(wf, "CFGGuider") or wc.nodes(wf, "KSampler")
    for _, sampler in samplers:
        assert ("ModelAttentionBackend" in wc.model_path_classes(wf, sampler["inputs"]["model"])) is pinned
