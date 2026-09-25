"""The ComfyUI video path books its VRAM with the orchestrator before queuing.

ComfyUI never waits: a Wan 14B graph queued right after a keyframe batch
loaded against 10 GB of leftover diffusers weights (2026-09-12: ~1 GB
usable, 9.6 GB offloaded, 43 min for a 5 s clip). The generator now asks the
orchestrator for the registry estimate, waits out a refusal with backoff up
to GUAARDVARK_VIDEO_VRAM_WAIT_S, and never submits when the card stays short.
"""

from types import SimpleNamespace

import pytest

from backend.services import comfyui_video_generator as cvg
from backend.services import gpu_memory_orchestrator as gmo
from backend.services.comfyui_video_generator import (
    ComfyUIVideoGenerator, VideoGenerationRequest, VRAM_WAIT_ENV,
)


class FakeOrchestrator:
    def __init__(self, refusals=0):
        self.refusals = refusals
        self.requests = []
        self.pinned = []
        self.dropped = []

    def request_model(self, slot_id, vram_estimate_mb, **kw):
        self.requests.append((slot_id, vram_estimate_mb, kw))
        if self.refusals > 0:
            self.refusals -= 1
            raise RuntimeError(f"GPU short for {slot_id}: only 1200MB free")

    def begin_use(self, slot_id):
        self.pinned.append(slot_id)

    def end_use(self, slot_id):
        self.pinned.remove(slot_id)

    def drop_booking(self, slot_id):
        self.dropped.append(slot_id)
        return True


@pytest.fixture
def clock(monkeypatch):
    """Deterministic time: sleep advances the clock, nothing really waits."""
    state = SimpleNamespace(now=1000.0, sleeps=[])

    def sleep(s):
        state.sleeps.append(s)
        state.now += s

    monkeypatch.setattr(cvg, "time", SimpleNamespace(time=lambda: state.now, sleep=sleep))
    return state


@pytest.fixture
def generator(monkeypatch):
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    gen._vram_booking = None
    monkeypatch.setattr(
        "backend.services.gpu_resource_policy.compositor_vram_reserve_mb", lambda: 800,
    )
    return gen


def _install(monkeypatch, orch):
    monkeypatch.setattr(gmo, "get_orchestrator", lambda: orch)
    monkeypatch.setattr(gmo, "get_orchestrator_if_created", lambda: orch)


def test_fits_first_time_books_the_registry_estimate(generator, clock, monkeypatch):
    orch = FakeOrchestrator()
    _install(monkeypatch, orch)

    assert generator._ensure_vram_for_model("wan22-14b-i2v", "VideoGen_1") is None

    slot, estimate, kw = orch.requests[0]
    assert slot == "video:comfyui:VideoGen_1"
    assert estimate == 11000  # wan22-14b-i2v registry vram_mb
    assert kw["hard_fit"] is True and kw.get("vram_reserve_mb", 0) == 0
    assert orch.pinned == [slot] and generator._vram_booking == slot
    assert clock.sleeps == []

    generator._release_vram_booking()
    assert orch.pinned == [] and orch.dropped == [slot]
    assert generator._vram_booking is None


def test_refusal_is_waited_out_then_admitted(generator, clock, monkeypatch):
    orch = FakeOrchestrator(refusals=2)
    _install(monkeypatch, orch)

    assert generator._ensure_vram_for_model("wan22-14b", "VideoGen_2") is None

    assert len(orch.requests) == 3
    assert clock.sleeps == [2.0, 3.0]  # backoff x1.5
    assert generator._vram_booking == "video:comfyui:VideoGen_2"


def test_card_that_stays_short_fails_before_queuing(generator, clock, monkeypatch):
    orch = FakeOrchestrator(refusals=10_000)
    _install(monkeypatch, orch)
    monkeypatch.setenv(VRAM_WAIT_ENV, "10")

    error = generator._ensure_vram_for_model("wan22-14b", "VideoGen_3")

    assert error is not None and "after waiting 10s" in error and "only 1200MB free" in error
    assert generator._vram_booking is None and orch.pinned == []
    assert clock.now - 1000.0 == pytest.approx(10.0)


def test_ledger_failure_never_blocks_a_render(generator, clock, monkeypatch):
    def broken():
        raise OSError("nvidia-smi missing")
    monkeypatch.setattr(gmo, "get_orchestrator", broken)

    assert generator._ensure_vram_for_model("wan22-5b", "VideoGen_4") is None
    assert generator._vram_booking is None


def test_generate_video_does_not_queue_when_vram_cannot_be_freed(monkeypatch, tmp_path):
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    gen._vram_booking = None
    gen.service_available = True
    gen._object_info_cache = None
    gen.comfy_node_available = lambda *_a, **_k: True
    gen._vram_preflight = lambda model: None
    gen._ensure_comfyui_reserve_for = lambda model: None  # the reserve check has its own tests
    gen._ensure_vram_for_model = lambda model, op_id: f"Could not free enough VRAM for {model} after waiting 600s: short"
    queued = []
    gen._queue_prompt = lambda *a, **k: queued.append(a) or "prompt-id"
    released = []
    gen._release_vram_booking = lambda: released.append(True)

    request = VideoGenerationRequest(
        prompt="a cat", model="wan22-14b-i2v", output_dir=tmp_path,
        metadata={"item_id": "VideoGen_5"}, enhance_prompt=False,
    )
    result = gen.generate_video(request)

    assert result.success is False
    assert result.error.startswith("Could not free enough VRAM for wan22-14b-i2v")
    assert queued == []
    assert released == [True], "the booking teardown runs on every exit"
