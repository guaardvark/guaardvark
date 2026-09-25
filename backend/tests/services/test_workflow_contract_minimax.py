"""MiniMax H3 graph contract: the fl2va builds (text, first frame, last
frame, first+last, guides) and the ref2va build (reference images, clips,
audio).

Every graph is checked against the ComfyUI /object_info snapshot, and the
values a request resolves to are followed into the nodes that use them.
Known gaps are strict xfails; docs/video-pipeline.md names each one.
"""
import pytest

from backend.services.video_model_registry import (
    VIDEO_MODEL_REGISTRY, minimax_comfyui_map, model_capabilities, snap_frames,
)
from backend.tests.fixtures import workflow_contract as wc
from backend.tests.fixtures.workflow_contract import comfy  # noqa: F401 — fixture

FL2VA = tuple(m for m, e in VIDEO_MODEL_REGISTRY.items() if e.get("type") == "minimax" and "flf2v" in (e.get("modes") or []))
REF = tuple(m for m, e in VIDEO_MODEL_REGISTRY.items() if e.get("type") == "minimax" and "ref2v" in (e.get("modes") or []))
MODELS = FL2VA + REF
LORAS = [{"filename": "style_a.safetensors", "strength": 0.6}, {"filename": "style_b.safetensors", "strength": 0.0}]
FRAME_MODES = {
    "t2v": {},
    "i2v": {"image_filename": "first.png"},
    "l2v": {"last_frame_filename": "last.png"},
    "flf2v": {"image_filename": "first.png", "last_frame_filename": "last.png"},
}


def _fl2va(model="minimax-h3-int8", **kw):
    args = dict(prompt="a fox sings", model_key=model, seed=7, width=864, height=480, num_frames=124, fps=24)
    args.update(kw)
    return wc.builder()._create_minimax_workflow(**args)


def _ref(model=None, **kw):
    args = dict(prompt="<Picture 1> sings", model_key=model or REF[0], seed=7, width=864, height=480,
                num_frames=124, fps=24, ref_images=["ref.png"])
    args.update(kw)
    return wc.builder()._create_minimax_ref_workflow(**args)


def _request(comfy, model, **fields):
    comfy.card_for(model)
    if model in REF:
        fields.setdefault("ref_images", [comfy.image])
    fields.setdefault("fps", model_capabilities(model)["native_fps"])
    fields.setdefault("interpolation_multiplier", 1)
    return comfy.render(model=model, prompt="a fox sings", **fields)


def _generator_node(wf):
    found = wc.nodes(wf, "MiniMaxH3ImageToVideo") or wc.nodes(wf, "MiniMaxH3ReferenceToVideo")
    assert len(found) == 1
    return found[0][1]


def test_both_builds_are_in_the_registry():
    assert "minimax-h3-int8" in FL2VA and REF


# ── Builder graphs ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", FL2VA)
@pytest.mark.parametrize("mode", FRAME_MODES)
@pytest.mark.parametrize("variant", [
    {},
    {"interpolation_multiplier": 2},
    {"lora_name": "turbo.safetensors", "lora_strength": 1.0},
    {"extra_loras": LORAS},
    {"text_encoder": "my_qwen.safetensors"},
])
def test_fl2va_builder_graph_is_valid(model, mode, variant):
    wf = _fl2va(model, **FRAME_MODES[mode], **variant)
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)
    gen = _generator_node(wf)
    assert ("first_frame" in gen["inputs"]) == ("image_filename" in FRAME_MODES[mode])
    assert ("last_frame" in gen["inputs"]) == ("last_frame_filename" in FRAME_MODES[mode])


@pytest.mark.parametrize("guides", [
    [{"kind": "audio", "filename": "voice.wav", "frame_idx": 0}],
    [{"kind": "image", "filename": "g.png", "frame_idx": -1}],
    [{"kind": "image", "filename": f"g{i}.png", "frame_idx": i * 5} for i in range(12)],
])
@pytest.mark.parametrize("loras", ([], LORAS))
def test_guides_chain_into_the_guider(guides, loras):
    wf = _fl2va(guides=guides, extra_loras=loras, lora_name="turbo.safetensors")
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)
    assert len(wc.nodes(wf, "MiniMaxH3AddGuide")) == len(guides)
    _, guider = wc.one(wf, "BasicGuider")
    assert wc.encoded_text(wf, guider["inputs"]["conditioning"]) == "a fox sings"
    chain = wc.model_path_classes(wf, guider["inputs"]["model"])
    assert chain.count("LoraLoaderModelOnly") == 1 + len(loras)
    assert chain[-1] == "UNETLoader"


@pytest.mark.parametrize("refs", [
    {"ref_images": ["a.png"]},
    {"ref_images": [f"r{i}.png" for i in range(9)]},
    {"ref_images": [], "ref_videos": [{"filename": "v.mp4", "audio": False}]},
    {"ref_videos": [{"filename": "v.mp4", "audio": True}, {"filename": "w.mp4", "audio": "w.wav"}],
     "ref_audios": ["voice.wav"]},
    {"ref_images": ["a.png"], "extra_loras": LORAS, "lora_name": "turbo.safetensors", "ref_image_size": "max"},
])
def test_ref2va_builder_graph_is_valid(refs):
    wf = _ref(**refs)
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)


@pytest.mark.parametrize("model", MODELS)
def test_loaders_name_the_files_the_registry_installs(model):
    wf = _ref(model) if model in REF else _fl2va(model, interpolation_multiplier=1)
    files = {v for k, v in wc.loader_files(wf).items() if wf[k[0]]["class_type"] not in ("LoadImage", "LoadAudio")}
    assert files == set(minimax_comfyui_map()[model].values())
    assert files <= wc.registry_files(model)


def test_soundtrack_is_muxed_into_the_clip():
    wf = _fl2va()
    audio = wc.video_output(wf)[1]["inputs"]["audio"]
    assert wc.source(wf, audio)["class_type"] == "VAEDecodeAudio"


# ── Rendered graphs: what generate_video resolved ────────────────────────────

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("ratio", ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"))
@pytest.mark.parametrize("frames", (124, 243))
def test_rendered_canvas_matches_the_resolved_request(comfy, model, ratio, frames):
    caps = model_capabilities(model)
    assert ratio in caps["aspect_ratios"]
    width, height = wc.ratio_dims(ratio, caps["max_pixel_area"], caps["dimension_alignment"])
    result, wf, req = _request(comfy, model, width=width, height=height, duration_frames=frames)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    wc.assert_resolved_canvas(model, req, ratio)
    [(_, w, h, length)] = wc.canvas(wf)
    assert (w, h, length) == (req.width, req.height, frames)
    assert wc.output_fps(wf) == req.fps


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("requested", (5, 50, 124, 362))
def test_rendered_frames_snap_up_to_the_template_grid(comfy, model, requested):
    result, wf, req = _request(comfy, model, duration_frames=requested, width=864, height=480)
    assert wf is not None, result.error
    [(_, _, _, length)] = wc.canvas(wf)
    assert length == snap_frames(model, requested, up=True)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason="docs/video-pipeline.md F-12: generate_video does not enforce max_frames")
def test_rendered_frames_never_exceed_max_frames(comfy, model):
    caps = model_capabilities(model)
    result, wf, req = _request(comfy, model, duration_frames=caps["max_frames"] + 40, width=864, height=480)
    [(_, _, _, length)] = wc.canvas(wf)
    assert length <= caps["max_frames"]


@pytest.mark.parametrize("model", MODELS)
def test_prompt_and_seed_land_in_the_graph(comfy, model):
    result, wf, req = _request(comfy, model, width=864, height=480, duration_frames=124, seed=4242,
                               negative_prompt="ignored by this family")
    assert wf is not None, result.error
    assert _generator_node(wf)["inputs"]["prompt"] == req.prompt
    assert wc.one(wf, "RandomNoise")[1]["inputs"]["noise_seed"] == 4242
    # No CFG path: the negative prompt has no node to reach.
    assert "CFGGuider" not in wc.class_types(wf)
    assert "ignored by this family" not in str(wf)


@pytest.mark.parametrize("model", FL2VA)
@pytest.mark.parametrize("frames", ("first", "last", "both"))
def test_rendered_start_and_end_frames(comfy, model, frames):
    fields = {}
    if frames in ("first", "both"):
        fields["first_frame_path"] = comfy.image
    if frames in ("last", "both"):
        fields["last_frame_path"] = comfy.image
    result, wf, req = _request(comfy, model, width=864, height=480, duration_frames=124, **fields)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    gen = _generator_node(wf)
    assert ("first_frame" in gen["inputs"]) == (frames in ("first", "both"))
    assert ("last_frame" in gen["inputs"]) == (frames in ("last", "both"))


def test_rendered_image_guide(comfy):
    result, wf, req = _request(comfy, "minimax-h3-int8", width=864, height=480, duration_frames=124,
                               guides=[{"kind": "image", "path": comfy.image, "frame_idx": 30}])
    assert wf is not None, result.error
    wc.assert_valid(wf)
    _, guide = wc.one(wf, "MiniMaxH3AddGuide")
    assert guide["inputs"]["frame_idx"] == 30
    assert wc.source(wf, guide["inputs"]["image"])["inputs"]["image"] == comfy.fake.uploads[-1]


@pytest.mark.parametrize("model", MODELS)
def test_optional_post_nodes_chain_into_the_video(comfy, monkeypatch, model):
    fields = {"ref_images": [comfy.image]} if model in REF else {}
    comfy.card_for(model)
    result, wf, req = wc.optional_features_render(comfy, monkeypatch, model, fps=24, width=864, height=480,
                                                  duration_frames=124, **fields)
    assert wf is not None, result.error
    wc.assert_optional_features(wf, 24)
    assert wc.source(wf, wc.video_output(wf)[1]["inputs"]["audio"])["class_type"] == "VAEDecodeAudio"


@pytest.mark.parametrize("model,profile,pinned", [
    ("minimax-h3-int8", None, False),     # Standard under ck measured identical to PyTorch
    ("minimax-h3-int8", "turbo-8", True),  # turbo profiles not compared under ck
] + [(m, None, True) for m in MODELS if m != "minimax-h3-int8"])
def test_attention_pinned_where_ck_is_not_verified(monkeypatch, model, profile, pinned):
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

    monkeypatch.setattr(ComfyUIVideoGenerator, "comfy_node_available", lambda self, cls: True)
    monkeypatch.setenv("GUAARDVARK_COMFYUI_ATTENTION", "ck")
    wf = (_ref(model, speed_profile=profile) if model in REF
          else _fl2va(model, speed_profile=profile, lora_name="turbo.safetensors" if profile else None,
                      extra_loras=LORAS))
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)
    pins = wc.nodes(wf, "ModelAttentionBackend")
    assert bool(pins) is pinned
    for cls in ("BasicGuider", "BasicScheduler"):
        _, node = wc.one(wf, cls)
        chain = wc.model_path_classes(wf, node["inputs"]["model"])
        assert ("ModelAttentionBackend" in chain) is pinned
        assert chain[-1] == "UNETLoader"
    assert len(pins) <= 1
