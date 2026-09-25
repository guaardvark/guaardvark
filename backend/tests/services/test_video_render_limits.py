"""The render-limit resolver (backend/services/video_render_limits.py): every
per-model rule the render enforces, read from the registry.

The default tables below are today's behaviour; the strict tables are the
declared limits GUAARDVARK_VIDEO_STRICT_LIMITS=1 enforces instead.
"""
import pytest

from backend.services import video_model_registry as vmr
from backend.services import video_render_limits as rl
from backend.tests.fixtures import workflow_contract as wc
from backend.tests.fixtures.workflow_contract import comfy  # noqa: F401 — fixture

GENERATION_MODELS = [m for m, e in vmr.VIDEO_MODEL_REGISTRY.items() if e.get("type") in vmr.GENERATION_TYPES]


@pytest.fixture(autouse=True)
def _default_limits(monkeypatch):
    monkeypatch.delenv(rl.STRICT_LIMITS_ENV, raising=False)
    monkeypatch.delenv(rl.TEXT_ENCODER_DEVICE_ENV, raising=False)


# ── The data is complete ─────────────────────────────────────────────────────

@pytest.mark.parametrize("family", vmr.GENERATION_TYPES)
def test_every_family_declares_every_render_limit(family):
    spec = vmr.family_spec(family)
    for key in ("dimension_alignment", "max_pixel_area", "min_vram_gb", "frame_rule", "frame_snap", "negative_prompt"):
        assert key in spec, f"{family} does not declare {key}"


@pytest.mark.parametrize("model", GENERATION_MODELS)
def test_every_model_record_carries_the_render_limits(model):
    caps = vmr.model_capabilities(model)
    for key in (*vmr.RENDER_LIMIT_KEYS, "attention_verified", "min_vram_gb"):
        assert key in caps, f"{model} capability record lacks {key}"
    assert caps["min_vram_gb"] > 0
    assert "pytorch" in caps["attention_verified"]


def test_a_measurement_is_not_inherited_by_a_clone():
    entry = {"type": "wan", "like": "wan22-14b-i2v", "attention": "pytorch"}
    vmr.VIDEO_MODEL_REGISTRY["user-test-clone"] = entry
    try:
        caps = vmr.model_capabilities("user-test-clone")
        assert caps["attention"] == "pytorch"
        assert caps["attention_verified"] == {"pytorch": ["*"]}
    finally:
        vmr.VIDEO_MODEL_REGISTRY.pop("user-test-clone")


# ── Length ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,requested,expected", [
    ("wan22-14b", 50, 50),          # frame_snap None: sent as asked
    ("wan22-5b", 200, 200),
    ("cogvideox-5b", 44, 44),
    ("ltx23-distilled-fp8", 52, 49),   # down onto 8n+1
    ("ltx23-distilled-fp8", 3, 9),     # floor 9
    ("ltx25-distilled-int8", 0, 65),   # unset
    ("hunyuan-t2v", 75, 77),        # nearest 4n+1
    ("hunyuan-t2v", 0, 73),
    ("minimax-h3-int8", 120, 124),  # up onto 17k+5
    ("minimax-h3-int8", 1, 5),
    ("minimax-h3-int8", 0, 124),
])
def test_frames_by_default(model, requested, expected):
    assert rl.resolve_frames(model, requested) == expected


@pytest.mark.parametrize("model,requested,expected", [
    ("wan22-14b", 50, 49),
    ("wan22-14b", 121, 81),         # max_frames
    ("wan22-5b", 200, 121),
    ("cogvideox-5b", 44, 41),
    ("cogvideox-5b", 81, 49),
    ("ltx23-distilled-fp8", 200, 161),
    ("hunyuan-t2v", 75, 73),        # never lengthened
    ("hunyuan-t2v", 169, 129),
    ("minimax-h3-int8", 120, 124),  # the template still rounds up
    ("minimax-h3-int8", 400, 362),
])
def test_frames_under_strict_limits(model, requested, expected):
    assert rl.resolve_frames(model, requested, strict=True) == expected
    caps = vmr.model_capabilities(model)
    assert wc.on_frame_grid(model, expected) and expected <= caps["max_frames"]


def test_family_frame_count_serves_aliases():
    assert rl.family_frame_count("ltx", 52) == 49
    assert rl.resolve_frames("hunyuan", 75, "hunyuan") == 77


# ── Canvas ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,size,expected", [
    ("wan22-14b", (1296, 720), (1296, 720)),     # family grid 16
    ("wan22-14b", (1920, 1920), (992, 992)),     # 1.0 MPx cap, then 16 px
    ("ltx23-distilled-fp8", (800, 470), (800, 480)),
    ("minimax-h3-int8", (1344, 768), (1344, 768)),
    ("cogvideox-5b", (721, 481), (720, 480)),
])
def test_canvas_by_default(model, size, expected):
    assert rl.resolve_canvas(model, *size, 49) == expected


@pytest.mark.parametrize("model", ["wan22-14b", "wan22-14b-i2v", "wan22-5b"])
def test_canvas_under_strict_limits_uses_the_declared_grid(model):
    w, h = rl.resolve_canvas(model, 1296, 720, 49, strict=True)
    assert w % 32 == 0 and h % 32 == 0
    assert w * h <= vmr.model_capabilities(model)["max_pixel_area"]


def test_minimax_long_clip_takes_its_tier_area():
    w, h = rl.resolve_canvas("minimax-h3-int8", 1344, 768, 243)
    assert w * h <= 864 * 480


# ── Steps and guidance ───────────────────────────────────────────────────────

WAN_LIGHTNING = vmr.speed_profile_for("wan22-14b-i2v", "lightx2v-4")
H3_TURBO = vmr.speed_profile_for("minimax-h3-int8", "turbo-8")


@pytest.mark.parametrize("model,requested,explicit,profile,expected", [
    ("wan22-14b", 12, False, None, 12),           # no floor on Wan today
    ("wan22-14b", 0, False, None, 25),            # unset: default_steps (0 is refused by ComfyUI)
    ("wan22-14b-i2v", 25, False, WAN_LIGHTNING, 4),   # the profile's count
    ("wan22-14b-i2v", 6, True, WAN_LIGHTNING, 6),     # a typed count stands
    ("ltx23-distilled-fp8", 0, False, None, 8),
    ("ltx23-distilled-fp8", 4, False, None, 4),
    ("hunyuan-t2v", 10, False, None, 10),
    ("cogvideox-5b", 25, False, None, 25),        # below its 50 floor, not raised today
    ("minimax-h3-int8", 10, False, None, 20),     # MiniMax raises presets to its floor
    ("minimax-h3-int8", 10, True, None, 10),
    ("minimax-h3-int8", 0, False, H3_TURBO, 8),
])
def test_steps_by_default(model, requested, explicit, profile, expected):
    assert rl.resolve_steps(model, requested, explicit=explicit, profile=profile) == expected


@pytest.mark.parametrize("model,requested,explicit,expected", [
    ("wan22-14b", 12, False, 20),
    ("cogvideox-5b", 25, False, 50),
    ("cogvideox-5b", 25, True, 25),
    ("hunyuan-t2v", 10, False, 20),
])
def test_steps_under_strict_limits(model, requested, explicit, expected):
    assert rl.resolve_steps(model, requested, explicit=explicit, strict=True) == expected


@pytest.mark.parametrize("model,requested,expected", [
    ("ltx23-distilled-fp8", None, 1.0),
    ("ltx23-distilled-fp8", 7.5, 7.5),   # kept, logged
    ("wan22-14b", 3.5, 3.5),
    ("wan22-14b", None, None),
])
def test_cfg(model, requested, expected):
    assert rl.resolve_cfg(model, requested) == expected


def test_fps_falls_back_to_native():
    assert rl.resolve_fps("ltx23-distilled-fp8", 0) == 16
    assert rl.resolve_fps("minimax-h3-int8", None) == 24
    assert rl.resolve_fps("wan22-14b", 12) == 12


# ── Hardware ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,expected", [
    ("wan22-5b", 11), ("wan22-14b", 16), ("minimax-h3-bf16", 48), ("minimax-h3-int8-full", 24),
    ("cogvideox-5b", 16), ("hunyuan-i2v", 16),
])
def test_min_vram(model, expected):
    assert rl.min_vram_gb(model) == expected


def test_min_vram_for_an_alias_is_its_family():
    assert rl.min_vram_gb("wan22", "wan") == 16


@pytest.mark.parametrize("family,total,expected", [
    ("wan", 16384, "cpu"), ("wan", 24576, "default"), ("wan", None, "cpu"),
    ("ltx", 16384, "cpu"), ("hunyuan", 20480, "cpu"),
    ("minimax", 16384, "default"),   # NVFP4 encoder: emulated on CPU is the slowest place for it
])
def test_text_encoder_device(monkeypatch, family, total, expected):
    from backend.services import gpu_resource_coordinator
    monkeypatch.setattr(gpu_resource_coordinator, "get_available_vram", lambda: {"success": False})
    assert rl.text_encoder_device("", total, family=family) == expected


def test_text_encoder_device_override(monkeypatch):
    monkeypatch.setenv(rl.TEXT_ENCODER_DEVICE_ENV, "cpu")
    assert rl.text_encoder_device("", 49152, family="minimax") == "cpu"


# ── Attention ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,profile,launch,expected", [
    ("wan22-14b-i2v", None, "ck", None),               # Standard measured clean under ck
    ("wan22-14b-i2v", "standard", "ck", None),
    ("wan22-14b-i2v", "lightx2v-4", "ck", "pytorch"),  # 15 of 24 Lightning clips damaged
    ("wan22-14b-i2v", rl.UNKNOWN_PROFILE, "ck", "pytorch"),
    ("wan22-14b-i2v", None, "sage", "pytorch"),
    ("wan22-14b", None, "ck", "pytorch"),              # never measured
    ("minimax-h3-int8", None, "ck", None),             # identical frames under ck
    ("minimax-h3-int8", "turbo-8", "ck", "pytorch"),
    ("minimax-h3-bf16", None, "ck", "pytorch"),
    ("ltx23-distilled-fp8", None, "auto", "pytorch"),
    ("wan22-14b-i2v", "lightx2v-4", "pytorch", None),  # the default launch is never pinned
    ("cogvideox-5b", None, "ck", None),                # the wrapper takes no pin
])
def test_attention_pin(model, profile, launch, expected):
    assert rl.attention_pin(model, profile, launch) == expected


# ── Modes ────────────────────────────────────────────────────────────────────

def test_start_image_rules(tmp_path):
    image = wc.still_image(tmp_path)
    missing = str(tmp_path / "gone.png")
    assert rl.start_image("wan22-14b-i2v", image) == (image, None)
    assert rl.start_image("wan22-5b", image) == (image, None)
    assert rl.start_image("wan22-5b", missing) == (None, None)     # renders from text (F-28)
    assert rl.start_image("ltx23-distilled-fp8", None) == (None, None)
    assert rl.start_image("wan22-14b", image) == (
        None, "wan22-14b is text-to-video only. Use wan22-14b-i2v for image-to-video.")
    assert rl.start_image("cogvideox-5b", image)[1].endswith("Use cogvideox-5b-i2v for image-to-video.")
    for model in ("wan22-14b-i2v", "hunyuan-i2v", "cogvideox-5b-i2v"):
        img, err = rl.start_image(model, missing)
        assert img is None and err.endswith("requires an input image.")
        assert err.startswith(vmr.VIDEO_MODEL_REGISTRY[model]["name"])


# ── Strict limits through generate_video ─────────────────────────────────────

@pytest.mark.parametrize("model,fields,check", [
    ("wan22-14b", {"width": 1296, "height": 720}, lambda wf, req: req.width % 32 == 0 and req.height % 32 == 0),
    ("wan22-14b", {"duration_frames": 50}, lambda wf, req: wc.canvas(wf)[0][3] == 49),
    ("wan22-5b", {"duration_frames": 200}, lambda wf, req: wc.canvas(wf)[0][3] == 121),
    ("hunyuan-t2v", {"duration_frames": 75}, lambda wf, req: wc.canvas(wf)[0][3] == 73),
    ("ltx23-distilled-fp8", {"duration_frames": 201}, lambda wf, req: wc.canvas(wf)[0][3] == 161),
    ("minimax-h3-int8", {"duration_frames": 400}, lambda wf, req: wc.canvas(wf)[0][3] == 362),
    ("cogvideox-5b", {"duration_frames": 44},
     lambda wf, req: wc.one(wf, "CogVideoSampler")[1]["inputs"]["num_frames"] == 41),
])
def test_strict_limits_render_the_declared_values(comfy, monkeypatch, model, fields, check):
    monkeypatch.setenv(rl.STRICT_LIMITS_ENV, "1")
    comfy.card_for(model)
    request = {"width": 832, "height": 480, "duration_frames": 49, "interpolation_multiplier": 1, **fields}
    result, wf, req = comfy.render(model=model, prompt="a fox", **request)
    assert wf is not None, result.error
    wc.assert_valid(wf)
    assert check(wf, req)
