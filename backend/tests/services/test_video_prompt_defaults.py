"""What a video request that leaves guidance and the negative prompt empty
renders with, traced into the graph generate_video queues.

GUAARDVARK_VIDEO_REFERENCE_DEFAULTS off (a fresh clone) must render exactly as
before: 7.5 on every model, the style negative with the identity-bleed guard.
On, each model gets the value its reference ComfyUI template uses, and the
guard only for a request with a character.
"""
import json

import pytest

from backend.services import video_render_limits as rl
from backend.services.video_model_registry import (
    LTX_REFERENCE_NEGATIVE, VIDEO_MODEL_REGISTRY, WAN_REFERENCE_NEGATIVE, model_capabilities,
)
from backend.tests.fixtures import workflow_contract as wc
from backend.tests.fixtures.workflow_contract import comfy  # noqa: F401 — fixture
from backend.utils.prompt_enhancer import IDENTITY_BLEED_NEGATIVE, NEGATIVE_PROMPTS, PROMPT_STYLE_IDS

# The 2026-09-24 report: Wan 2.2 14B T2V, style 3d_animation, enhance on, no guidance given.
CASE = dict(prompt="An outdoor concert on a green lawn, a black cartoon aardvark rapper on stage, hundreds of fans",
            prompt_style="3d_animation", enhance_prompt=True, width=864, height=480,
            duration_frames=81, num_inference_steps=25, interpolation_multiplier=1)

# The reference values, restated so a registry edit that changes one is seen here.
REFERENCE_CFG = {
    "wan22-14b": 3.5, "wan22-14b-i2v": 3.5, "wan22-5b": 5.0,
    "ltx23-distilled-fp8": 1.0, "ltx25-distilled-int8": 1.0,
    "hunyuan-t2v": 6.0, "cogvideox-5b": 6.0,
}


@pytest.fixture
def reference_on(monkeypatch):
    monkeypatch.setenv(rl.REFERENCE_DEFAULTS_ENV, "1")


@pytest.fixture(autouse=True)
def _reference_off_unless_asked(monkeypatch):
    monkeypatch.delenv(rl.REFERENCE_DEFAULTS_ENV, raising=False)
    # The Verbatim Prompts setting is a database read (through the app when no
    # app context is active); these tests are about the prompts it lets through.
    from backend.services import media_director
    monkeypatch.setattr(media_director, "verbatim_prompts_enabled", lambda: False)


def _guidance(wf):
    """Every guidance value in the graph: sampler/guider cfg and FluxGuidance."""
    out = []
    for _, node in sorted(wf.items()):
        inputs = node.get("inputs") or {}
        if "cfg" in inputs:
            out.append(inputs["cfg"])
        elif node["class_type"] == "FluxGuidance":
            out.append(inputs["guidance"])
    return out


def _negatives(wf):
    """The text on every sampler/guider negative input."""
    return {wc.encoded_text(wf, n["inputs"]["negative"])
            for _, n in wf.items() if wc._is_link((n.get("inputs") or {}).get("negative"))}


def _render(comfy, model, **fields):
    comfy.card_for(model)
    fields.setdefault("fps", model_capabilities(model)["native_fps"])
    if model.endswith("i2v"):
        fields.setdefault("metadata", {"image_path": comfy.image})
    result, wf, req = comfy.render(model=model, **fields)
    assert wf is not None, result.error
    return wf, req


# ── The reported case ────────────────────────────────────────────────────────

def _shifts(wf):
    return [n["inputs"]["shift"] for _, n in sorted(wc.nodes(wf, "ModelSamplingSD3"))]


def test_case_off_takes_the_model_guidance_and_keeps_the_style_negative(comfy):
    wf, req = _render(comfy, "wan22-14b", **CASE)
    assert _guidance(wf) == [3.5, 3.5]
    assert _shifts(wf) == [3.7, 3.7]  # the resolution-scaled curve at 864x480
    assert _negatives(wf) == {f"{NEGATIVE_PROMPTS['3d_animation']}, {IDENTITY_BLEED_NEGATIVE}"}
    assert "3D-animated, Pixar-style" in wc.encoded_text(wf, wc.nodes(wf, "KSamplerAdvanced")[0][1]["inputs"]["positive"])


def test_case_on_gets_the_template_cfg_and_negative(comfy, reference_on):
    wf, _ = _render(comfy, "wan22-14b", **CASE)
    assert _guidance(wf) == [3.5, 3.5]
    # The default sampler profile's fixed shift, as on 14B I2V and the 5B.
    assert _shifts(wf) == [8.0, 8.0]
    # The template negative, and no guard: "anthropomorphic, snout, animal head"
    # pushed against the aardvark the prompt asks for.
    assert _negatives(wf) == {WAN_REFERENCE_NEGATIVE}


def test_on_a_character_request_keeps_the_identity_guard(comfy, reference_on):
    wf, _ = _render(comfy, "wan22-14b", **CASE, adapters=[], metadata={"cast": True})
    assert _negatives(wf) == {f"{WAN_REFERENCE_NEGATIVE}, {IDENTITY_BLEED_NEGATIVE}"}


def test_on_a_named_guidance_and_negative_stand(comfy, reference_on):
    wf, _ = _render(comfy, "wan22-14b", **{**CASE, "guidance_scale": 7.5, "cfg_explicit": True,
                                           "negative_prompt": "washed out"})
    assert _guidance(wf) == [7.5, 7.5]
    assert _negatives(wf) == {"washed out"}


@pytest.mark.parametrize("profile,shift", [("official", 8.0), ("adaptive", 3.7), (None, 3.7)])
def test_t2v_takes_a_named_sampler_profiles_shift(comfy, profile, shift):
    wf, _ = _render(comfy, "wan22-14b", **CASE, wan_sampler_profile=profile)
    assert _shifts(wf) == [shift, shift]
    # Only the shift: the sampler stays euler, as on 14B I2V.
    assert {n["inputs"]["sampler_name"] for _, n in wc.nodes(wf, "KSamplerAdvanced")} == {"euler"}


# ── Every family ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", sorted(REFERENCE_CFG))
def test_on_guidance_reaches_the_graph(comfy, reference_on, model):
    assert model_capabilities(model)["cfg_when_unset"] == REFERENCE_CFG[model]
    wf, _ = _render(comfy, model, prompt="a red fox in snow", enhance_prompt=False)
    assert set(_guidance(wf)) == {REFERENCE_CFG[model]}


@pytest.mark.parametrize("model", sorted(REFERENCE_CFG))
def test_unset_guidance_is_the_models_own_without_the_setting(comfy, model):
    wf, _ = _render(comfy, model, prompt="a red fox in snow", enhance_prompt=False)
    assert set(_guidance(wf)) == {REFERENCE_CFG[model]}


def test_every_t2v_model_declares_guidance_or_takes_none():
    for mid in VIDEO_MODEL_REGISTRY:
        caps = model_capabilities(mid)
        if caps and caps.get("supports_t2v") and caps.get("cfg"):
            assert caps.get("cfg_when_unset") is not None, mid


def test_minimax_takes_no_guidance_either_way(comfy, reference_on):
    wf, _ = _render(comfy, "minimax-h3-int8", prompt="a red fox in snow", enhance_prompt=False)
    assert _guidance(wf) == []


@pytest.mark.parametrize("model", ["wan22-14b", "wan22-14b-i2v", "wan22-5b"])
@pytest.mark.parametrize("enhance", [True, False])
def test_on_the_wan_reference_negative_applies_with_or_without_the_enhancer(comfy, reference_on, model, enhance):
    wf, _ = _render(comfy, model, prompt="a red fox in snow", enhance_prompt=enhance, prompt_style="anime")
    assert _negatives(wf) == {WAN_REFERENCE_NEGATIVE}


@pytest.mark.parametrize("model", ["ltx23-distilled-fp8", "ltx25-distilled-int8"])
def test_on_ltx_keeps_the_style_negative(comfy, reference_on, model):
    # The LTX template negative names "cartoon"; it is an A/B variant, not a default.
    assert LTX_REFERENCE_NEGATIVE not in (model_capabilities(model).get("negative_when_unset") or "")
    wf, _ = _render(comfy, model, prompt="a red fox in snow", enhance_prompt=True, prompt_style="anime")
    assert _negatives(wf) == {NEGATIVE_PROMPTS["anime"]}


def test_off_without_the_enhancer_the_builder_keeps_its_own_negative(comfy):
    wf, req = _render(comfy, "wan22-14b", prompt="a red fox in snow", enhance_prompt=False)
    assert req.negative_prompt == ""
    [negative] = _negatives(wf)
    assert negative.startswith("blurry, low quality, worst quality, deformed")


# ── CogVideoX negatives ─────────────────────────

@pytest.mark.parametrize("model", ["cogvideox-5b", "cogvideox-5b-i2v"])
def test_cogvideox_sends_a_typed_negative(comfy, model):
    wf, _ = _render(comfy, model, prompt="a red fox in snow", width=720, height=480, duration_frames=49,
                    negative_prompt="washed out")
    assert _negatives(wf) == {"washed out"}


def test_cogvideox_default_negative_only_with_reference_on(comfy, monkeypatch):
    fields = dict(prompt="a red fox in snow", width=720, height=480, duration_frames=49, enhance_prompt=True)
    wf, req = _render(comfy, "cogvideox-5b", **fields)
    assert req.negative_prompt and _negatives(wf) == {""}
    monkeypatch.setenv(rl.REFERENCE_DEFAULTS_ENV, "1")
    wf, _ = _render(comfy, "cogvideox-5b", **fields)
    assert _negatives(wf) == {NEGATIVE_PROMPTS["cinematic"]}


# ── Withheld styles ──────────────────────────────────────────────────────────

@pytest.fixture
def withheld(monkeypatch):
    """wan22-14b stops offering 3d_animation, as an A/B result would declare it."""
    monkeypatch.setitem(VIDEO_MODEL_REGISTRY["wan22-14b"], "prompt_styles_withheld",
                        {"3d_animation": "washed out in the 2026-09 A/B"})


def test_offered_styles_are_every_style_by_default():
    assert model_capabilities("wan22-14b")["prompt_styles"] == list(PROMPT_STYLE_IDS)


def test_a_withheld_style_is_not_offered_and_not_rendered(comfy, withheld):
    caps = model_capabilities("wan22-14b")
    assert "3d_animation" not in caps["prompt_styles"] and "cinematic" in caps["prompt_styles"]
    comfy.card_for("wan22-14b")
    result, wf, _ = comfy.render(model="wan22-14b", **CASE, fps=16)
    assert wf is None and not result.success
    assert "does not offer the '3d_animation' prompt style: washed out in the 2026-09 A/B" in result.error
    # Unenhanced, or under Verbatim Prompts, the style adds nothing, so it is not refused.
    result, wf, _ = comfy.render(model="wan22-14b", **{**CASE, "enhance_prompt": False}, fps=16)
    assert wf is not None


def test_verbatim_prompts_do_not_refuse_a_withheld_style(comfy, withheld, monkeypatch):
    from backend.services import media_director

    monkeypatch.setattr(media_director, "verbatim_prompts_enabled", lambda: True)
    comfy.card_for("wan22-14b")
    result, wf, req = comfy.render(model="wan22-14b", **CASE, fps=16)
    assert wf is not None, result.error
    assert req.prompt == CASE["prompt"]


def test_mcp_refuses_a_withheld_or_unknown_style(withheld):
    from backend.tools.image_tools import VideoGeneratorTool

    params, err = VideoGeneratorTool.resolve_request("a fox", model="wan22-14b", style="3d_animation")
    assert params is None and "washed out in the 2026-09 A/B" in err and "cinematic" in err
    params, err = VideoGeneratorTool.resolve_request("a fox", model="wan22-14b", style="vaporwave")
    assert params is None and err.startswith("Unknown style 'vaporwave'")
    params, err = VideoGeneratorTool.resolve_request("a fox", model="wan22-14b", style="Anime")
    assert err is None and params["prompt_style"] == "anime"
    # The MCP tool never names guidance, so the batch treats it as unset.
    assert "guidance_scale" not in params


# ── REST route and batch plumbing ────────────────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    from flask import Flask

    from backend.api import batch_video_generation_api as bva

    calls = []

    class _Batches:
        service_available = True

        def start_batch_from_prompts(self, prompts, **params):
            from types import SimpleNamespace
            calls.append(params)
            return SimpleNamespace(batch_id="b1", status="queued", stage="queued")

    monkeypatch.setattr(bva, "get_batch_video_generator", lambda: _Batches())
    monkeypatch.setattr(bva, "prepare_video_model", lambda model_id: (True, ""))
    monkeypatch.setattr("backend.services.video_model_registry.preflight_video_model", lambda model_id: (True, ""))
    monkeypatch.setattr(bva, "_gpu_queue_hint", lambda: {})
    app = Flask(__name__)
    app.register_blueprint(bva.batch_video_bp)
    return app.test_client(), calls


def test_route_passes_no_guidance_when_none_is_named(api):
    client, calls = api
    assert client.post("/api/batch-video/generate/text", json={"prompts": ["a fox"], "model": "wan22-14b"}).status_code == 200
    assert client.post("/api/batch-video/generate/text",
                       json={"prompts": ["a fox"], "model": "wan22-14b", "guidance_scale": 3.5}).status_code == 200
    assert [c["guidance_scale"] for c in calls] == [None, 3.5]


def test_route_refuses_a_withheld_style(api, withheld):
    client, calls = api
    body = {"prompts": ["a fox"], "model": "wan22-14b", "prompt_style": "3d_animation"}
    resp = client.post("/api/batch-video/generate/text", json=body)
    assert resp.status_code == 400 and "washed out in the 2026-09 A/B" in resp.get_json()["error"]["message"]
    assert client.post("/api/batch-video/generate/text", json={**body, "enhance_prompt": False}).status_code == 200
    assert len(calls) == 1


def test_preview_shows_what_the_render_will_use(api, monkeypatch):
    client, _ = api
    body = {"prompt": "a fox", "model": "wan22-14b", "prompt_style": "3d_animation"}
    off = client.post("/api/batch-video/enhance-preview", json=body).get_json()["data"]
    assert off["cfg_when_unset"] == 3.5 and off["reference_defaults"] is False
    assert off["default_negative_prompt"].endswith(IDENTITY_BLEED_NEGATIVE)
    monkeypatch.setenv(rl.REFERENCE_DEFAULTS_ENV, "1")
    on = client.post("/api/batch-video/enhance-preview", json=body).get_json()["data"]
    assert on["cfg_when_unset"] == 3.5 and on["default_negative_prompt"] == WAN_REFERENCE_NEGATIVE
    assert on["enhanced_prompt"] == off["enhanced_prompt"]


def _batch(tmp_path, **params):
    import queue
    import threading

    from backend.services.batch_video_generator import BatchVideoGenerator, BatchVideoItem

    gen = BatchVideoGenerator.__new__(BatchVideoGenerator)
    gen.base_output_dir = tmp_path
    gen.active_batches, gen.cancel_events, gen.queue_order = {}, {}, []
    gen.batch_lock = threading.Lock()
    gen.batch_queue = queue.Queue()
    gen._save_metadata = lambda status: None
    status = gen._start_batch(batch_id="b1", items=[BatchVideoItem(id="i1", prompt="a fox")],
                              model="wan22-14b", **params)
    request, _ = gen.batch_queue.get_nowait()
    return request, status


@pytest.mark.parametrize("given,explicit,value,retry", [
    ({}, False, 7.5, None),
    ({"guidance_scale": None}, False, 7.5, None),
    ({"guidance_scale": 3.5}, True, 3.5, 3.5),
    ({"guidance_scale": "7.5"}, True, 7.5, 7.5),
])
def test_batch_records_whether_guidance_was_named(tmp_path, given, explicit, value, retry):
    request, status = _batch(tmp_path, **given)
    assert (request.cfg_explicit, request.guidance_scale) == (explicit, value)
    assert status.retry_data["params"]["guidance_scale"] == retry


def _run_clip(tmp_path, monkeypatch, **params):
    """Run the batch worker on one text item and return the VideoGenerationRequest
    it hands the video generator (the seam _run_batch_inner calls)."""
    from flask import Flask

    from backend.services.comfyui_video_generator import VideoGenerationResult

    seen = []

    class _Recorder:
        service_available = True

        def generate_video(self, request):
            seen.append(request)
            return VideoGenerationResult(success=False, error="recorded, not rendered")

    request, status = _batch(tmp_path, **params)
    import threading

    from backend.services.batch_video_generator import BatchVideoGenerator

    gen = BatchVideoGenerator.__new__(BatchVideoGenerator)
    gen.video_generator = _Recorder()
    gen.cancel_events = {"b1": threading.Event()}
    gen.batch_lock = threading.Lock()
    gen.active_batches = {}
    monkeypatch.chdir(tmp_path)
    with Flask(__name__).app_context():
        gen._run_batch_inner(request, status)
    [clip] = seen
    return clip


@pytest.mark.parametrize("given,explicit,value", [({}, False, 7.5), ({"guidance_scale": 3.5}, True, 3.5)])
def test_each_clip_carries_whether_guidance_was_named(tmp_path, monkeypatch, given, explicit, value):
    clip = _run_clip(tmp_path, monkeypatch, enhance_prompt=False, **given)
    assert (clip.cfg_explicit, clip.guidance_scale) == (explicit, value)


@pytest.mark.xfail(strict=True, reason=(
    "batch metadata (steps_explicit, upscale, teacache_threshold, "
    "feta_weight) never reaches the per-clip request"))
def test_batch_metadata_reaches_each_clip(tmp_path, monkeypatch):
    clip = _run_clip(tmp_path, monkeypatch, enhance_prompt=False,
                     metadata={"steps_explicit": True, "upscale": True, "teacache_threshold": 0.2})
    assert clip.metadata.get("steps_explicit") and clip.metadata.get("upscale")


# ── The A/B script's plans (scripts/video_prompt_ab.py) ──────────────────────

def _ab():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "video_prompt_ab.py"
    spec = importlib.util.spec_from_file_location("video_prompt_ab", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ab_case_plan_changes_one_factor_at_a_time():
    ab = _ab()
    plan = {v["name"]: v for v in ab.plan_variants("case", "wan22-14b")}
    assert list(plan) == ["reported", "cfg-reference", "negative-no-guard", "negative-template",
                          "shift-8", "enhancer-off", "reference-setting"]
    reported = plan["reported"]
    assert (reported["style"], reported["enhance"], reported["guidance_scale"]) == ("3d_animation", True, 7.5)
    assert reported["negative_prompt"] == f"{NEGATIVE_PROMPTS['3d_animation']}, {IDENTITY_BLEED_NEGATIVE}"
    assert plan["cfg-reference"]["guidance_scale"] == 3.5
    assert plan["negative-no-guard"]["negative_prompt"] == NEGATIVE_PROMPTS["3d_animation"]
    assert plan["negative-template"]["negative_prompt"] == WAN_REFERENCE_NEGATIVE
    assert not plan["enhancer-off"]["enhance"]
    assert plan["shift-8"]["wan_sampler_profile"] == "official"
    assert plan["reference-setting"]["wan_sampler_profile"] == "official"
    assert "shift-8" not in {v["name"] for v in ab.plan_variants("case", "wan22-14b-i2v")}


@pytest.mark.parametrize("model", ["wan22-14b", "wan22-5b", "ltx23-distilled-fp8", "cogvideox-5b"])
@pytest.mark.parametrize("style", ["3d_animation", "anime", "none"])
def test_ab_setting_variant_sends_what_the_backend_would(monkeypatch, model, style):
    ab = _ab()
    monkeypatch.setenv(rl.REFERENCE_DEFAULTS_ENV, "1")
    enhance = style != "none"
    assert ab.negative_text("setting", model, style, enhance) == rl.default_negative(
        model, style, enhanced=enhance, character=False)
    assert ab.cfg_value("reference", model) == rl.cfg_when_unset(model)


def test_ab_styles_plan_covers_the_offered_styles(withheld):
    names = [v["name"] for v in _ab().plan_variants("styles", "wan22-14b")]
    assert names == [f"style-{s}" for s in PROMPT_STYLE_IDS if s != "3d_animation"]


def test_ab_dry_run_queues_nothing(capsys):
    assert _ab().main(["--dry-run", "--model", "wan22-14b", "--seeds", "1,2"]) == 0
    out = capsys.readouterr().out
    assert "14 clip(s) of wan22-14b; nothing was queued." in out
    first = json.loads(out.splitlines()[0])["body"]
    assert first["seed"] == 1 and first["guidance_scale"] == 7.5 and first["enhance_prompt"] == "true"


class _Backend:
    """The four routes the A/B script calls, at the requests.Session seam."""

    def __init__(self, interrupt_on_poll=False):
        self.queued, self.cancelled = [], []
        self.interrupt_on_poll = interrupt_on_poll

    def __call__(self):
        return self

    @staticmethod
    def _json(payload, ok=True, content=b""):
        from types import SimpleNamespace
        return SimpleNamespace(json=lambda: payload, ok=ok, content=content)

    def post(self, url, json=None, timeout=None):
        if url.endswith("/enhance-preview"):
            return self._json({"success": True, "data": {"enhanced_prompt": json["prompt"] + " +style"}})
        if url.endswith("/generate/text"):
            self.queued.append(json)
            return self._json({"success": True, "data": {"batch_id": f"b{len(self.queued)}"}})
        if url.endswith("/cancel"):
            self.cancelled.append(url)
            return self._json({"success": True})
        raise AssertionError(url)

    def get(self, url, timeout=None):
        if "/status/" in url:
            if self.interrupt_on_poll:
                raise KeyboardInterrupt
            return self._json({"data": {"status": "completed", "results": [
                {"success": True, "video_path": "item/videos/clip.mp4"}]}})
        if "/video/" in url:
            return self._json(None, content=b"not really an mp4")
        raise AssertionError(url)


def test_ab_run_queues_downloads_and_reports(tmp_path, monkeypatch):
    import requests

    ab = _ab()
    backend = _Backend()
    monkeypatch.setattr(requests, "Session", backend)
    monkeypatch.setattr(ab.time, "sleep", lambda s: None)
    assert ab.main(["--model", "wan22-14b", "--plan", "case", "--out", str(tmp_path)]) == 0
    assert [b["seed"] for b in backend.queued] == [1234] * 7
    assert backend.queued[1]["guidance_scale"] == 3.5 and "wan_sampler_profile" not in backend.queued[0]
    saved = json.loads((tmp_path / "results.json").read_text())
    assert [v["status"] for v in saved["variants"]] == ["completed"] * 7
    assert saved["variants"][0]["positive_prompt"].endswith(" +style")
    assert (tmp_path / "reported_s1234.mp4").read_bytes() == b"not really an mp4"
    assert 'src="reported_s1234.mp4"' in (tmp_path / "index.html").read_text()


def test_ab_ctrl_c_cancels_the_clip_in_flight(tmp_path, monkeypatch):
    import requests

    monkeypatch.delenv("GUAARDVARK_API", raising=False)
    monkeypatch.setenv("GUAARDVARK_URL", "http://localhost:5055/")

    ab = _ab()
    backend = _Backend(interrupt_on_poll=True)
    monkeypatch.setattr(requests, "Session", backend)
    monkeypatch.setattr(ab.time, "sleep", lambda s: None)
    assert ab.main(["--model", "wan22-14b", "--out", str(tmp_path)]) == 130
    assert backend.cancelled == ["http://localhost:5055/api/batch-video/batch/b1/cancel"]
    saved = json.loads((tmp_path / "results.json").read_text())
    assert [v["status"] for v in saved["variants"]] == ["cancelled"]
