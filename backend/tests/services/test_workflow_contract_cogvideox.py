"""CogVideoX graph contract: 5B text-to-video (wrapper's hub loader) and
5B 1.5 image-to-video (single-file loaders), through ComfyUI-CogVideoXWrapper.

Every graph is checked against the ComfyUI /object_info snapshot, and the
values a request resolves to are followed into the nodes that use them.
Known gaps are strict xfails; docs/video-pipeline.md names each one.
"""
import pytest

from backend.services.video_model_registry import cogvideox_comfyui_map, model_capabilities
from backend.tests.fixtures import workflow_contract as wc
from backend.tests.fixtures.workflow_contract import comfy  # noqa: F401 — fixture

MODELS = ("cogvideox-5b", "cogvideox-5b-i2v")
# CogVideoX declares no pixel budget; this is the canvas its builders default to.
CANVAS_AREA = 720 * 480


def _build(model, **kw):
    gen = wc.builder()
    args = dict(prompt="a red fox in snow", negative_prompt="blurry", seed=7, width=720, height=480,
                num_frames=49, fps=8)
    args.update(kw)
    if model == "cogvideox-5b-i2v":
        files = gen._cogvideox_i2v_files(model)
        return gen._create_cogvideox_i2v_workflow(image_filename="start.png", model_file=files["unet"],
                                                  vae_file=files["vae"], **args)
    return gen._create_cogvideox_text2video_workflow(model_name="THUDM/CogVideoX-5b", **args)


def _request(comfy, model, **fields):
    if model == "cogvideox-5b-i2v":
        fields.setdefault("metadata", {"image_path": comfy.image})
    fields.setdefault("fps", model_capabilities(model)["native_fps"])
    fields.setdefault("interpolation_multiplier", 1)
    return comfy.render(model=model, prompt="a red fox in snow", **fields)


def _frames(wf):
    return wc.one(wf, "CogVideoSampler")[1]["inputs"]["num_frames"]


# ── Builder graphs ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("variant", [{}, {"interpolation_multiplier": 2}, {"interpolation_multiplier": 1}])
def test_builder_graph_is_valid(model, variant):
    wf = _build(model, **variant)
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("teacache,feta", [(0.3, None), (None, 1.0), (0.3, 1.0)])
def test_teacache_and_enhance_a_video_feed_the_sampler(model, teacache, feta):
    gen = wc.builder()
    wf = _build(model)
    sampler_id, sampler = wc.one(wf, "CogVideoSampler")
    gen._add_cogvideox_optional_nodes(wf, sampler_id, teacache_threshold=teacache, feta_weight=feta)
    wc.assert_valid(wf)
    if teacache is not None:
        assert wc.source(wf, sampler["inputs"]["teacache_args"])["inputs"]["rel_l1_thresh"] == teacache
    if feta is not None:
        assert wc.source(wf, sampler["inputs"]["feta_args"])["inputs"]["weight"] == feta


@pytest.mark.parametrize("model", MODELS)
def test_builder_puts_prompt_and_negative_in_their_encoders(model):
    wf = _build(model)
    _, sampler = wc.one(wf, "CogVideoSampler")
    assert wc.encoded_text(wf, sampler["inputs"]["positive"]) == "a red fox in snow"
    assert wc.encoded_text(wf, sampler["inputs"]["negative"]) == "blurry"


def test_i2v_loaders_name_the_files_the_registry_installs():
    wf = _build("cogvideox-5b-i2v", interpolation_multiplier=1)
    files = {v for k, v in wc.loader_files(wf).items() if wf[k[0]]["class_type"] != "LoadImage"}
    assert set(cogvideox_comfyui_map()["cogvideox-5b-i2v"].values()) <= files
    assert files <= wc.registry_files("cogvideox-5b-i2v")


@pytest.mark.xfail(strict=True, reason=(
    "docs/video-pipeline.md F-43: the T2V graph loads the T5 encoder, which cogvideox-5b "
    "does not list in its requires"))
def test_t2v_loaders_name_the_files_the_registry_installs():
    wf = _build("cogvideox-5b", interpolation_multiplier=1)
    files = set(wc.loader_files(wf).values())
    assert files <= wc.registry_files("cogvideox-5b")


def test_t2v_loads_the_hub_model_the_registry_installs():
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY

    wf = _build("cogvideox-5b")
    _, loader = wc.one(wf, "DownloadAndLoadCogVideoModel")
    entry = VIDEO_MODEL_REGISTRY["cogvideox-5b"]
    assert loader["inputs"]["model"] == entry["hf_repo"]
    # The wrapper looks in models/CogVideo/<repo name> before it downloads.
    assert entry["local_subdir"] == "CogVideo/" + entry["hf_repo"].split("/")[-1]


# ── Rendered graphs: what generate_video resolved ────────────────────────────

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("ratio", wc.RATIOS_WHEN_UNDECLARED)
def test_rendered_canvas_matches_the_resolved_request(comfy, model, ratio):
    caps = model_capabilities(model)
    width, height = wc.ratio_dims(ratio, CANVAS_AREA, caps["dimension_alignment"])
    result, wf, req = _request(comfy, model, width=width, height=height, duration_frames=49)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    wc.assert_resolved_canvas(model, req, ratio)
    [(_, w, h, _)] = wc.canvas(wf)
    assert (w, h, _frames(wf)) == (req.width, req.height, 49)
    assert wc.output_fps(wf) == req.fps


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason="docs/video-pipeline.md F-13: CogVideoX never snaps frames to its 8n+1 grid")
def test_rendered_frames_are_snapped_to_the_grid(comfy, model):
    result, wf, req = _request(comfy, model, duration_frames=44, width=720, height=480)
    assert wc.on_frame_grid(model, _frames(wf)) and _frames(wf) <= 44


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason="docs/video-pipeline.md F-12: generate_video does not enforce max_frames")
def test_rendered_frames_never_exceed_max_frames(comfy, model):
    caps = model_capabilities(model)
    result, wf, req = _request(comfy, model, duration_frames=caps["max_frames"] + 32, width=720, height=480)
    assert _frames(wf) <= caps["max_frames"]


@pytest.mark.parametrize("model", MODELS)
def test_prompt_and_seed_land_in_the_sampler(comfy, model):
    result, wf, req = _request(comfy, model, width=720, height=480, duration_frames=49, seed=4242)
    assert wf is not None, result.error
    _, sampler = wc.one(wf, "CogVideoSampler")
    assert wc.encoded_text(wf, sampler["inputs"]["positive"]) == "a red fox in snow"
    assert sampler["inputs"]["seed"] == 4242
    if model == "cogvideox-5b-i2v":
        assert wc.one(wf, "LoadImage")[1]["inputs"]["image"] == comfy.fake.uploads[-1]


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason="docs/video-pipeline.md F-5: generate_video does not pass negative_prompt to either CogVideoX builder")
def test_rendered_negative_prompt_reaches_the_negative_encoder(comfy, model):
    result, wf, req = _request(comfy, model, width=720, height=480, duration_frames=49, negative_prompt="washed out")
    _, sampler = wc.one(wf, "CogVideoSampler")
    assert wc.encoded_text(wf, sampler["inputs"]["negative"]) == "washed out"


@pytest.mark.parametrize("model", MODELS)
def test_rendered_teacache_and_enhance_a_video(comfy, model):
    result, wf, req = _request(comfy, model, width=720, height=480, duration_frames=49,
                               metadata={"teacache_threshold": 0.25, "feta_weight": 1.5,
                                         **({"image_path": comfy.image} if model.endswith("i2v") else {})})
    assert wf is not None, result.error
    wc.assert_valid(wf)
    _, sampler = wc.one(wf, "CogVideoSampler")
    assert wc.source(wf, sampler["inputs"]["teacache_args"])["inputs"]["rel_l1_thresh"] == 0.25
    assert wc.source(wf, sampler["inputs"]["feta_args"])["inputs"]["weight"] == 1.5


@pytest.mark.parametrize("model", MODELS)
def test_optional_post_nodes_chain_into_the_video(comfy, monkeypatch, model):
    fields = {"metadata": {"image_path": comfy.image}} if model.endswith("i2v") else {}
    fps = model_capabilities(model)["native_fps"]
    result, wf, req = wc.optional_features_render(comfy, monkeypatch, model, fps=fps, width=720, height=480,
                                                  duration_frames=49, **fields)
    assert wf is not None, result.error
    wc.assert_optional_features(wf, fps)
    # FreeU and a legacy lora_name are skipped on this family rather than wired wrongly.
    assert not wc.nodes(wf, "FreeU_V2") and not wc.nodes(wf, "LoraLoader")


@pytest.mark.parametrize("model", MODELS)
def test_wrapper_graph_is_never_pinned(comfy, monkeypatch, model):
    # The CogVideoX wrapper picks its own attention_mode; there is no MODEL edge to pin.
    monkeypatch.setenv("GUAARDVARK_COMFYUI_ATTENTION", "ck")
    fields = {"metadata": {"image_path": comfy.image}} if model.endswith("i2v") else {}
    result, wf, req = _request(comfy, model, width=720, height=480, duration_frames=49, **fields)
    assert wf is not None, result.error
    assert not wc.nodes(wf, "ModelAttentionBackend")
