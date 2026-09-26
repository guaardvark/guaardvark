"""Wan 2.2 graph contract: 14B T2V, 14B I2V (MoE, GGUF) and TI2V 5B.

Every graph is checked against the ComfyUI /object_info snapshot, and the
values a request resolves to are followed into the nodes that use them.
Known gaps are strict xfails so they flip when fixed, each naming the gap.
"""
import pytest

from backend.services.video_model_registry import (
    model_capabilities, snap_frames, speed_profile_for, wan_comfyui_map,
)
from backend.tests.fixtures import workflow_contract as wc
from backend.tests.fixtures.workflow_contract import comfy  # noqa: F401 — fixture

MODELS = ("wan22-14b", "wan22-14b-i2v", "wan22-5b")
I2V = {"wan22-14b-i2v", "wan22-5b"}
LORAS = [{"filename": "style_a.safetensors", "strength": 0.6}, {"filename": "style_b.safetensors", "strength": 0.0}]


def _build(model, *, image=None, **kw):
    gen = wc.builder()
    args = dict(prompt="a red fox in snow", negative_prompt="blurry", model_key=model, seed=7,
                width=1312, height=736, num_frames=49, fps=16)
    args.update(kw)
    if model == "wan22-5b":
        return gen._create_wan22_5b_workflow(image_filename=image, **args)
    if model == "wan22-14b-i2v":
        return gen._create_wan22_i2v_workflow(image_filename=image or "start.png", **args)
    return gen._create_wan22_t2v_workflow(**args)


def _samplers(wf):
    return wc.nodes(wf, "KSamplerAdvanced") or wc.nodes(wf, "KSampler")


def _request(comfy, model, **fields):
    if model in I2V and "metadata" not in fields:
        fields["metadata"] = {"image_path": comfy.image}
    fields.setdefault("fps", model_capabilities(model)["native_fps"])
    fields.setdefault("interpolation_multiplier", 1)
    return comfy.render(model=model, prompt="a red fox in snow", **fields)


# ── Builder graphs ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("variant", [
    {},
    {"interpolation_multiplier": 2},
    {"extra_loras": LORAS},
    {"text_encoder": "my_umt5.safetensors"},
])
def test_builder_graph_is_valid(model, variant):
    wf = _build(model, **variant)
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)


@pytest.mark.parametrize("model", ("wan22-14b", "wan22-14b-i2v"))
def test_speed_profile_loras_go_on_their_own_expert(model):
    profile = speed_profile_for(model, "lightx2v-4")
    files = profile["lora_files"]
    wf = _build(model, lora_high=files["unet_high"], lora_low=files["unet_low"], lora_strength=1.0,
                num_inference_steps=profile["steps"], guidance_scale=profile["cfg"], shift_override=profile["shift"])
    wc.assert_valid(wf)
    high, low = _samplers(wf)
    unet = wan_comfyui_map()[model]
    for (_, sampler), lora, expert in ((high, files["unet_high"], unet["unet_high"]), (low, files["unet_low"], unet["unet_low"])):
        chain = [wf[r[0]] for r in _model_refs(wf, sampler["inputs"]["model"])]
        assert [n["inputs"]["lora_name"] for n in chain if n["class_type"] == "LoraLoaderModelOnly"] == [lora]
        assert chain[-1]["inputs"]["unet_name"] == expert
        assert [n["inputs"]["shift"] for n in chain if n["class_type"] == "ModelSamplingSD3"] == [profile["shift"]]


def _model_refs(wf, ref):
    refs = []
    while wc._is_link(ref):
        refs.append(ref)
        ref = (wf[ref[0]].get("inputs") or {}).get("model")
    return refs


@pytest.mark.parametrize("model", MODELS)
def test_user_loras_reach_every_expert_with_their_strength(model):
    wf = _build(model, extra_loras=LORAS)
    wc.assert_valid(wf)
    wc.assert_loras_on_every_model_input(wf, [l["filename"] for l in LORAS])
    assert wc.lora_strengths(wf) == {"style_a.safetensors": 0.6, "style_b.safetensors": 0.0}


@pytest.mark.parametrize("model", MODELS)
def test_loaders_name_the_files_the_registry_installs(model):
    wf = _build(model, image="start.png" if model in I2V else None, interpolation_multiplier=1)
    files = {k: v for k, v in wc.loader_files(wf).items() if wf[k[0]]["class_type"] != "LoadImage"}
    assert files
    assert set(files.values()) <= wc.registry_files(model)
    unet = wan_comfyui_map()[model]
    loaders = {wf[nid]["class_type"]: v for (nid, _), v in files.items() if wf[nid]["class_type"] in ("CLIPLoader", "VAELoader")}
    assert loaders == {"CLIPLoader": unet["clip"], "VAELoader": unet["vae"]}


# ── Rendered graphs: what generate_video resolved ────────────────────────────

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("ratio", ("16:9", "9:16", "1:1"))
def test_rendered_canvas_matches_the_resolved_request(comfy, model, ratio):
    caps = model_capabilities(model)
    assert ratio in caps["aspect_ratios"]
    width, height = wc.ratio_dims(ratio, caps["max_pixel_area"], caps["dimension_alignment"])
    result, wf, req = _request(comfy, model, width=width, height=height, duration_frames=49)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    wc.assert_resolved_canvas(model, req, ratio)
    [(_, w, h, frames)] = wc.canvas(wf)
    assert (w, h, frames) == (req.width, req.height, 49)
    assert wc.output_fps(wf) == req.fps


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason="Wan snaps to the family's 16 px, the entries declare 32")
def test_rendered_canvas_is_on_the_declared_grid(comfy, model):
    result, wf, req = _request(comfy, model, width=1296, height=720, duration_frames=49)
    assert wf is not None, result.error
    wc.assert_resolved_canvas(model, req, "16:9")


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("which", ("min", "max"))
def test_rendered_frames_on_grid_values(comfy, model, which):
    caps = model_capabilities(model)
    frames = snap_frames(model, 9, up=True) if which == "min" else caps["max_frames"]
    result, wf, req = _request(comfy, model, duration_frames=frames, width=832, height=480)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    [(_, _, _, length)] = wc.canvas(wf)
    assert length == frames


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason="Wan never snaps frames to its declared 4n+1 grid")
def test_rendered_frames_are_snapped_to_the_grid(comfy, model):
    result, wf, req = _request(comfy, model, duration_frames=50, width=832, height=480)
    [(_, _, _, length)] = wc.canvas(wf)
    assert wc.on_frame_grid(model, length) and length <= 50


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.xfail(strict=True, reason="generate_video does not enforce max_frames")
def test_rendered_frames_never_exceed_max_frames(comfy, model):
    caps = model_capabilities(model)
    result, wf, req = _request(comfy, model, duration_frames=caps["max_frames"] + 40, width=832, height=480)
    [(_, _, _, length)] = wc.canvas(wf)
    assert length <= caps["max_frames"]


@pytest.mark.parametrize("model", MODELS)
def test_prompt_negative_and_seed_land_in_the_samplers(comfy, model):
    result, wf, req = _request(comfy, model, width=832, height=480, duration_frames=49,
                               negative_prompt="washed out, clip art", seed=4242)
    assert wf is not None, result.error
    for _, sampler in _samplers(wf):
        assert wc.encoded_text(wf, sampler["inputs"]["positive"]) == "a red fox in snow"
        assert wc.encoded_text(wf, sampler["inputs"]["negative"]) == "washed out, clip art"
    first = _samplers(wf)[0][1]["inputs"]
    assert first.get("noise_seed", first.get("seed")) == 4242
    if model in I2V:
        _, loader = wc.one(wf, "LoadImage")
        assert loader["inputs"]["image"] == comfy.fake.uploads[-1]


@pytest.mark.parametrize("model", MODELS)
def test_optional_post_nodes_chain_into_the_video(comfy, monkeypatch, model):
    fields = {"metadata": {"image_path": comfy.image}} if model in I2V else {}
    fps = model_capabilities(model)["native_fps"]
    result, wf, req = wc.optional_features_render(comfy, monkeypatch, model, fps=fps, width=832, height=480,
                                                  duration_frames=49, **fields)
    assert wf is not None, result.error
    wc.assert_optional_features(wf, fps)


@pytest.mark.parametrize("model", MODELS)
def test_rendered_loaders_name_registry_files(comfy, model):
    result, wf, req = _request(comfy, model, width=832, height=480, duration_frames=49)
    files = {v for k, v in wc.loader_files(wf).items() if wf[k[0]]["class_type"] != "LoadImage"}
    assert files <= wc.registry_files(model)


@pytest.mark.xfail(strict=True, reason="RIFE's rife49.pth is not a registry file; the node downloads it on first use")
def test_rife_checkpoint_is_a_registry_file(comfy):
    result, wf, req = _request(comfy, "wan22-5b", width=832, height=480, duration_frames=49, interpolation_multiplier=2)
    _, rife = wc.one(wf, "RIFE VFI")
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
    assert any(rife["inputs"]["ckpt_name"] in wc.registry_files(mid) for mid in VIDEO_MODEL_REGISTRY)


# ── Attention pin ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("backend,model,pinned", [
    ("ck", "wan22-14b", True),        # T2V: ck never measured
    ("ck", "wan22-14b-i2v", False),   # Standard under ck measured clean
    ("sage", "wan22-14b-i2v", True),  # sage never measured
    ("ck", "wan22-5b", True),         # 5B: ck never measured
    ("pytorch", "wan22-14b", False),  # default launch: graph left alone
])
def test_attention_pinned_where_the_launch_backend_is_not_verified(comfy, monkeypatch, backend, model, pinned):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_ATTENTION", backend)
    result, wf, req = _request(comfy, model, width=832, height=480, duration_frames=49)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    assert not wc.orphan_nodes(wf)
    pins = wc.nodes(wf, "ModelAttentionBackend")
    if not pinned:
        assert not pins
        return
    assert all(p["inputs"]["attention"] == "pytorch attention" for _, p in pins)
    samplers = _samplers(wf)
    assert len(pins) == len(samplers)
    for _, sampler in samplers:
        chain = wc.model_path_classes(wf, sampler["inputs"]["model"])
        assert "ModelAttentionBackend" in chain
        # The pin sits after the LoRAs, so every LoRA runs under the pinned backend.
        assert chain.index("ModelAttentionBackend") < len(chain) - 1


@pytest.mark.parametrize("profile,pinned", [
    ("lightx2v-4", True),   # ck damaged 15 of 24 Lightning clips
    (None, False),          # Standard: verified clean under ck
])
def test_i2v_lightning_is_pinned_under_ck(monkeypatch, profile, pinned):
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

    monkeypatch.setattr(ComfyUIVideoGenerator, "comfy_node_available", lambda self, cls: True)
    monkeypatch.setenv("GUAARDVARK_COMFYUI_ATTENTION", "ck")
    files = speed_profile_for("wan22-14b-i2v", "lightx2v-4")["lora_files"]
    wf = _build("wan22-14b-i2v", image="start.png", lora_high=files["unet_high"], lora_low=files["unet_low"],
                speed_profile=profile)
    wc.assert_valid(wf)
    assert bool(wc.nodes(wf, "ModelAttentionBackend")) is pinned


def test_builder_not_told_the_profile_pins_under_ck(monkeypatch):
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

    monkeypatch.setattr(ComfyUIVideoGenerator, "comfy_node_available", lambda self, cls: True)
    monkeypatch.setenv("GUAARDVARK_COMFYUI_ATTENTION", "ck")
    wf = _build("wan22-14b-i2v", image="start.png")
    assert len(wc.nodes(wf, "ModelAttentionBackend")) == 2


_PROFILE_GAP = pytest.mark.xfail(strict=True, reason=(
    "14B T2V ignores wan_sampler_profile and keeps the "
    "resolution-scaled shift; 14B I2V takes the profile's shift but keeps euler"))


@pytest.mark.parametrize("model", [
    pytest.param("wan22-14b", marks=_PROFILE_GAP),
    pytest.param("wan22-14b-i2v", marks=_PROFILE_GAP),
    "wan22-5b",
])
def test_official_sampler_profile_reaches_every_sampler(comfy, model):
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

    profile = ComfyUIVideoGenerator.WAN5B_SAMPLER_PROFILES["official"]
    result, wf, req = _request(comfy, model, width=736, height=416, duration_frames=49, wan_sampler_profile="official")
    assert wf is not None, result.error
    assert {s["inputs"]["sampler_name"] for _, s in _samplers(wf)} == {profile["sampler"]}
    assert {n["inputs"]["shift"] for _, n in wc.nodes(wf, "ModelSamplingSD3")} == {profile["shift"]}
