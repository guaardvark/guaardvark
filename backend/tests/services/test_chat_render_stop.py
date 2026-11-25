"""Stop in chat ends the render that chat turn started, and only that one.

The chat tool hands the turn's Stop check (agent_control_service's
thread-local) down as ``should_stop``. The offline Diffusers path asks it before
and after the model load and on every denoise step; the ComfyUI path asks it
while it polls and then deletes and interrupts its own prompt id. Either way
the GPU claim is released on the way out and the tool reports a stop, not a
success or a generic failure.

No GPU, no app, no database: generators run with their heavy seams stubbed and
ComfyUI is a recorded fake.
"""
from __future__ import annotations

import contextlib
import threading
from types import SimpleNamespace

import pytest
import requests

from backend.services import comfyui_image_generator as cig
from backend.services import gpu_resource_policy as grp
from backend.services import offline_image_generator as offline
from backend.services import stills_pipeline
from backend.services.gpu_resource_policy import RenderStopped


# ── offline Diffusers path ─────────────────────────────────────────────────

class _Pipe:
    """A pipeline that runs its steps and calls the step callback like Diffusers."""

    def __init__(self):
        self.steps = []
        self.decoded = False

    def __call__(self, *, num_inference_steps, callback_on_step_end=None, **_):
        for i in range(num_inference_steps):
            self.steps.append(i)
            if callback_on_step_end is not None:
                callback_on_step_end(self, i, 1000 - i, {})
        self.decoded = True
        return SimpleNamespace(images=[None])


@pytest.fixture
def offline_gen(monkeypatch):
    """OfflineImageGenerator with every GPU, network and settings seam stubbed."""
    calls = {"session": [], "load": 0, "evict": 0, "unload": 0, "pins": []}

    @contextlib.contextmanager
    def gpu_session(*a, **k):
        calls["session"].append("enter")
        try:
            yield True
        finally:
            calls["session"].append("exit")

    monkeypatch.setattr(grp, "gpu_session", gpu_session)
    monkeypatch.setattr(grp, "compositor_vram_reserve_mb", lambda: 0)
    import backend.services.gpu_memory_orchestrator as gmo
    monkeypatch.setattr(gmo, "get_orchestrator", lambda: SimpleNamespace(
        begin_use=lambda slot: calls["pins"].append(("begin", slot)),
        end_use=lambda slot: calls["pins"].append(("end", slot)),
    ))
    import backend.services.media_director as md
    monkeypatch.setattr(md, "verbatim_prompts_enabled", lambda: True)
    monkeypatch.setattr(offline.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(offline, "forget_pipeline_slot", lambda: None)

    gen = offline.OfflineImageGenerator.__new__(offline.OfflineImageGenerator)
    gen.service_available = True
    gen._generation_lock = threading.RLock()
    gen._device = "cuda"
    gen.available_models = {"zimage-turbo": "Tongyi-MAI/Z-Image-Turbo"}
    gen._pipeline = _Pipe()
    gen.gpu_fault_message = lambda: None
    gen._notify_vision_pipeline = lambda action: None
    gen._has_text_intent = lambda prompt: False
    gen.is_comfy_only_model = lambda model: False
    gen.missing_model_message = lambda model: None
    gen._model_family = lambda model_id: "zimage"
    gen._ram_estimate_gb = lambda *a: 1.0
    gen._vram_estimate_mb = lambda *a: 1000
    gen._soft_clamp_family_sampling = lambda request, family: None
    gen._seed_generator = lambda seed: None
    gen._set_vae_tiling = lambda wanted: False
    gen._ram_watchdog_callback = lambda: None
    gen._skip_negative_prompt = lambda *a: True
    gen._ensure_flow_scheduler = lambda family: None
    gen._prompt_token_limit = lambda family: 512
    gen._unload_loras = lambda: None

    def ensure_vram(*a):
        calls["evict"] += 1

    def load(*a, **k):
        calls["load"] += 1
        return True

    def unload(*a, **k):
        calls["unload"] += 1
        return True

    gen._ensure_vram_for_pipeline = ensure_vram
    gen._load_pipeline = load
    gen._unload_pipeline = unload
    gen.calls = calls
    return gen


def _request(should_stop):
    return offline.ImageGenerationRequest(
        prompt="a brass key", model="zimage-turbo", width=512, height=512,
        num_inference_steps=9, seed=7, should_stop=should_stop,
    )


def test_a_stop_mid_denoise_ends_the_render_without_decoding(offline_gen):
    pipe = offline_gen._pipeline
    result = offline_gen.generate_image(_request(lambda: len(pipe.steps) >= 3))

    assert not result.success
    assert result.metadata == {"stopped": True}
    assert "Stopped" in result.error
    assert result.image_path is None
    assert pipe.steps == [0, 1, 2]
    assert not pipe.decoded
    # The pipeline is unloaded and the GPU claim released on the way out.
    assert offline_gen.calls["unload"] == 1
    assert offline_gen.calls["session"] == ["enter", "exit"]
    assert offline_gen.calls["pins"] == [("begin", "sd:pipeline"), ("end", "sd:pipeline")]


def test_a_stop_while_the_gpu_was_claimed_skips_the_eviction_and_load(offline_gen):
    result = offline_gen.generate_image(_request(lambda: True))

    assert result.metadata == {"stopped": True}
    assert offline_gen.calls["evict"] == 0 and offline_gen.calls["load"] == 0
    assert offline_gen._pipeline.steps == []
    assert offline_gen.calls["session"] == ["enter", "exit"]


def test_a_stop_during_the_load_ends_before_the_first_step(offline_gen):
    stopped = []

    def load(*a, **k):
        stopped.append(True)
        return True

    offline_gen._load_pipeline = load
    result = offline_gen.generate_image(_request(lambda: bool(stopped)))

    assert result.metadata == {"stopped": True}
    assert offline_gen._pipeline.steps == []
    assert offline_gen.calls["unload"] == 1
    assert offline_gen.calls["session"] == ["enter", "exit"]


def test_a_render_nobody_stops_still_finishes(offline_gen, tmp_path):
    from PIL import Image
    offline_gen._pipeline = lambda **kw: SimpleNamespace(images=[Image.new("RGB", (4, 4))])
    offline_gen.cache_dir = tmp_path
    offline_gen._current_model = "Tongyi-MAI/Z-Image-Turbo"

    result = offline_gen.generate_image(_request(lambda: False))

    assert result.success and result.image_path
    assert not (result.metadata or {}).get("stopped")


def test_the_step_callback_keeps_the_ram_watchdog():
    gen = offline.OfflineImageGenerator.__new__(offline.OfflineImageGenerator)
    watched = []

    def watchdog(pipe, step, timestep, kwargs):
        watched.append(step)
        return kwargs

    gen._ram_watchdog_callback = lambda: watchdog
    assert gen._denoise_callback(None) is watchdog

    stop = []
    cb = gen._denoise_callback(lambda: bool(stop))
    assert cb(None, 0, 999, {"latents": 1}) == {"latents": 1}
    assert watched == [0]
    stop.append(True)
    with pytest.raises(RenderStopped):
        cb(None, 1, 998, {})
    assert watched == [0]


# ── ComfyUI path ───────────────────────────────────────────────────────────

@pytest.fixture
def comfy(monkeypatch):
    """A ComfyUIImageGenerator talking to a recorded fake server."""
    posts, polls, sleeps = [], [], []

    def post(url, json=None, timeout=None):
        posts.append((url.rsplit("/", 1)[-1], json))
        return SimpleNamespace(status_code=200)

    def get(url, timeout=None):
        polls.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})

    monkeypatch.setattr(cig, "requests", SimpleNamespace(post=post, get=get, exceptions=requests.exceptions))
    import time as _time
    monkeypatch.setattr(cig, "time", SimpleNamespace(time=_time.time, sleep=sleeps.append))
    gen = cig.ComfyUIImageGenerator(comfy_url="http://comfy.test")
    gen.posts, gen.polls = posts, polls
    return gen


def test_a_stopped_wait_cancels_only_its_own_prompt(comfy):
    answers = iter([False, True])
    with pytest.raises(RenderStopped):
        comfy._wait("p-1", should_stop=lambda: next(answers))

    assert len(comfy.polls) == 1
    # Delete before interrupt, both scoped to this prompt: no bare /interrupt,
    # no queue clear, so a Studio or batch job on the same ComfyUI keeps running.
    assert comfy.posts == [("queue", {"delete": ["p-1"]}), ("interrupt", {"prompt_id": "p-1"})]


def test_a_wait_nobody_stops_is_unchanged(comfy, monkeypatch):
    comfy.polls.clear()
    monkeypatch.setattr(cig, "requests", SimpleNamespace(
        post=lambda *a, **k: pytest.fail("nothing to cancel"),
        get=lambda url, timeout=None: SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"p-1": {"outputs": {"9": {}}}}),
        exceptions=requests.exceptions,
    ))
    assert comfy._wait("p-1", should_stop=lambda: False) == {"9": {}}


def test_a_comfy_still_stopped_before_queueing_is_never_queued(comfy, monkeypatch, tmp_path):
    monkeypatch.setattr(comfy, "_require_up", lambda message: None)
    monkeypatch.setattr(comfy, "_preflight_loras", lambda paths: None)
    monkeypatch.setattr(comfy, "_queue", lambda wf: pytest.fail("queued after Stop"))
    with pytest.raises(RenderStopped):
        comfy.generate_image(prompt="a key", output_path=str(tmp_path / "k.png"), model="sdxl",
                             should_stop=lambda: True)
    assert comfy.posts == []


def test_an_admitted_comfy_still_releases_its_gpu_claim_when_stopped(comfy, monkeypatch, tmp_path):
    claim = []

    @contextlib.contextmanager
    def session(op_id, gpu_wait, **kw):
        claim.append("enter")
        try:
            yield True
        finally:
            claim.append("exit")

    queued = []
    monkeypatch.setattr(comfy, "_edit_gpu_session", session)
    monkeypatch.setattr(comfy, "_queue", lambda wf, client_id=None: queued.append(wf) or "p-2")
    with pytest.raises(RenderStopped):
        comfy._run_edit_graph({}, str(tmp_path / "o.png"), job="stills_comfy", vram_mb=1000,
                              timeout=300, should_stop=lambda: bool(queued))
    assert claim == ["enter", "exit"]
    assert comfy.posts == [("queue", {"delete": ["p-2"]}), ("interrupt", {"prompt_id": "p-2"})]


# ── stills façade ──────────────────────────────────────────────────────────

def test_the_stills_facade_hands_the_stop_to_the_renderer_and_marks_the_result(monkeypatch):
    seen = []

    def render(request):
        seen.append(request.should_stop)
        return offline.ImageGenerationResult(success=False, error="Stopped", metadata={"stopped": True})

    monkeypatch.setattr(offline, "get_image_generator", lambda: SimpleNamespace(
        generate_image=render, missing_model_message=lambda model: None))
    stop = lambda: False  # noqa: E731 — the façade's own check must not fire
    result = stills_pipeline.run_stills_pipeline(
        ["a brass key"], model="zimage-turbo", verbatim=True, hold_gpu=False, keep_pipeline=True,
        should_stop=stop,
    )[0]
    assert seen == [stop]
    assert not result.success and result.metadata["stopped"] is True


def test_stills_not_yet_started_are_skipped_once_stopped(monkeypatch):
    monkeypatch.setattr(stills_pipeline, "_generate_one", lambda **kw: pytest.fail("rendered after Stop"))
    results = stills_pipeline.run_stills_pipeline(
        ["a brass key", "a copper key"], model="zimage-turbo", verbatim=True, hold_gpu=False,
        should_stop=lambda: True,
    )
    assert [r.metadata.get("stopped") for r in results] == [True, True]
    assert not any(r.success for r in results)


def test_a_stopped_comfy_zimage_still_reads_as_stopped(monkeypatch):
    def generate_image(self, **kw):
        assert kw["should_stop"] is stop
        raise RenderStopped("while ComfyUI rendered prompt p-3")

    stop = lambda: True  # noqa: E731
    monkeypatch.setattr(cig.ComfyUIImageGenerator, "generate_image", generate_image)
    result = stills_pipeline._generate_comfy_zimage(
        prompt="p", negative="", model="comfyui (zimage)", width=512, height=512, steps=8,
        guidance=1.0, seed=1, enhance_mode="none", output="", output_dir=None, should_stop=stop,
    )
    assert not result.success and result.metadata == {"stopped": True}
    assert "Stopped" in result.error


def test_a_stopped_cast_still_reads_as_stopped(monkeypatch, tmp_path):
    from backend.services.character_still_pipeline import render_character_still

    def generate_image(self, **kw):
        raise RenderStopped("while ComfyUI rendered prompt p-4")

    monkeypatch.setattr(cig.ComfyUIImageGenerator, "generate_image", generate_image)
    subject = SimpleNamespace(training_settings_json={"base_model_id": "flux-dev"})
    result = render_character_still("a portrait", subjects=[subject], apply_subject_loras=False,
                                    include_bible=False, output_path=tmp_path / "c.png",
                                    should_stop=lambda: True)
    assert not result.success and result.metadata["stopped"] is True


# ── the chat tool ──────────────────────────────────────────────────────────

def _tool_run(monkeypatch, still):
    from backend.tools.image_tools import ImageGeneratorTool
    seen = {}

    def run(prompts, **kw):
        seen["should_stop"] = kw.get("should_stop")
        return [still]

    monkeypatch.setattr(stills_pipeline, "run_stills_pipeline", run)
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda prompt: [])
    return ImageGeneratorTool().execute(prompt="a brass key", model="zimage-turbo"), seen


def test_chat_stop_reaches_the_render_and_reads_as_stopped(monkeypatch):
    from backend.services import agent_control_service as acs
    stop = lambda: True  # noqa: E731
    acs.set_chat_stop_check(stop)
    try:
        result, seen = _tool_run(monkeypatch, stills_pipeline._stopped_still("a brass key", "none"))
    finally:
        acs.set_chat_stop_check(None)
    assert seen["should_stop"] is stop
    assert not result.success
    assert result.error.startswith("Stopped by the user")
    assert result.metadata == {"stopped": True}


def test_outside_a_chat_turn_nothing_can_stop_the_render(monkeypatch):
    from backend.services import agent_control_service as acs
    acs.set_chat_stop_check(None)
    failed = stills_pipeline.StillResult(success=False, error="CUDA error: out of memory")
    result, seen = _tool_run(monkeypatch, failed)
    assert seen["should_stop"] is None
    assert not result.success and "ran out of memory" in result.error


# ── the chat engine's pool threads ─────────────────────────────────────────

def test_parallel_tool_calls_see_the_turns_stop_check(monkeypatch):
    """Two calls in one step run on pool threads; each sees a Stop check for
    the session, and the turn thread's own check is put back afterwards."""
    import backend.services.unified_chat_engine as uce
    from backend.services import agent_control_service as acs
    from backend.tests.test_unified_chat_iteration_limit import _Result, _engine, _run, tool_call

    e = _engine(monkeypatch, replies=[tool_call("one") + "\n" + tool_call("two"), "Done."])
    asked = []
    monkeypatch.setattr(uce, "is_aborted", lambda session_id: asked.append(session_id) or False)
    turn_thread = threading.current_thread()
    seen = []

    def execute_tool(name, on_output=None, agent_context=None, **params):
        check = acs.get_chat_stop_check()
        seen.append((threading.current_thread() is turn_thread, check is not None and check() is False))
        return _Result("ok")

    e.registry.execute_tool = execute_tool
    outer = lambda: False  # noqa: E731
    acs.set_chat_stop_check(outer)
    try:
        _run(e)
        assert acs.get_chat_stop_check() is outer
    finally:
        acs.set_chat_stop_check(None)
    assert seen == [(False, True), (False, True)]
    assert "sess-iter" in asked
