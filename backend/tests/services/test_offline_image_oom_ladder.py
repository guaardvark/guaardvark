"""OOM ladder + CPU-DiT guard tests (2026-08-04 client box 2048² desktop crash).

The old ladder held the failed forward's traceback alive (pinning the pipeline,
latents, and decode activations) while loading 2-3 more host-resident pipelines
on top — 50-100GB of system RAM, desktop dead. These tests pin:
- _detach_exception genuinely frees the failed frame's locals,
- the reload ladder is DISABLED above 1MP (clean error, no reload),
- the ladder still runs at ≤1MP (the legit krea2 ~14.3GB offload case),
- zimage/krea2 refuse to run on a non-CUDA device (the silent CPU fallback
  reproduces identical symptoms via fp32 CPU inference).
"""
import contextlib
import gc
import weakref
from types import SimpleNamespace

import pytest

import backend.services.offline_image_generator as oig
import backend.services.gpu_resource_policy as grp
import backend.services.gpu_memory_orchestrator as gmo
from backend.services.offline_image_generator import ImageGenerationRequest


GB = 1024 * 1024 * 1024


@pytest.fixture
def gen():
    g = oig.OfflineImageGenerator()
    g.service_available = True
    return g


class _Sentinel:
    pass


def test_detach_exception_frees_frame_locals():
    holder = {}

    def boom():
        sentinel = _Sentinel()
        holder["ref"] = weakref.ref(sentinel)
        raise RuntimeError("synthetic failure")

    err = None
    try:
        boom()
    except RuntimeError as e:
        err = e

    gc.collect()
    assert holder["ref"]() is not None, "traceback should pin boom()'s locals"

    oig._detach_exception(err)
    gc.collect()
    assert holder["ref"]() is None, "detaching the traceback must free the frame"


class _OomPipeline:
    """Fake DiT pipeline: vae-level tiling available, forward raises CUDA OOM."""

    def __init__(self):
        self.vae = SimpleNamespace(
            enable_tiling=lambda: None,
            disable_tiling=lambda: None,
            enable_slicing=lambda: None,
        )
        self.call_count = 0

    def __call__(self, **kw):
        self.call_count += 1
        raise oig.torch.cuda.OutOfMemoryError("CUDA out of memory (synthetic)")


class _NoTilingPipeline:
    """Fake DiT pipeline with tiling available at NEITHER level."""

    def __init__(self):
        self.vae = object()
        self.call_count = 0

    def __call__(self, **kw):
        self.call_count += 1
        raise AssertionError("forward must not run when the tiling gate refuses")


@pytest.fixture
def harness(gen, monkeypatch):
    """Mock the coordination layer so generate_image can be driven for real."""
    state = {"loads": [], "unloads": 0, "pipeline_factory": _OomPipeline}

    @contextlib.contextmanager
    def _fake_session(*a, **kw):
        yield

    monkeypatch.setattr(grp, "gpu_session", _fake_session)

    class _Orch:
        def begin_use(self, *a, **kw):
            pass

        def end_use(self, *a, **kw):
            pass

        def request_model(self, *a, **kw):
            pass

        def release(self, *a, **kw):
            pass

    monkeypatch.setattr(gmo, "get_orchestrator", lambda: _Orch())
    monkeypatch.setattr(gen, "_notify_vision_pipeline", lambda *a, **kw: None)
    monkeypatch.setattr(gen, "_ensure_vram_for_pipeline", lambda *a, **kw: None)
    monkeypatch.setattr(gen, "_ensure_flow_scheduler", lambda *a, **kw: None)
    monkeypatch.setattr(gen, "_unload_pipeline", lambda: state.__setitem__("unloads", state["unloads"] + 1))
    monkeypatch.setattr(
        oig.torch.cuda, "mem_get_info", lambda: (8 * GB, 16 * GB), raising=False
    )
    gen._device = "cuda"
    gen._pipeline_offload_mode = "model"

    def _fake_load(model_id, *, force_sequential=False):
        state["loads"].append(force_sequential)
        if len(state["loads"]) > 1:
            return False  # ladder retries stop here — we only assert they ran
        gen._pipeline = state["pipeline_factory"]()
        state["pipeline"] = gen._pipeline
        return True

    monkeypatch.setattr(gen, "_load_pipeline", _fake_load)
    return state


def _request(width, height):
    return ImageGenerationRequest(
        prompt="a scenic mountain valley at dawn",
        model="zimage-turbo",
        width=width,
        height=height,
        auto_enhance=False,
        seed=None,
    )


def test_oom_above_1mp_fails_cleanly_without_reload(gen, harness):
    result = gen.generate_image(_request(2048, 2048))
    assert result.success is False
    assert result.error and "disabled by design" in result.error
    assert "2048x2048" in result.error
    assert harness["loads"] == [False], "no ladder reload above 1MP"
    assert harness["unloads"] >= 1, "failed pipeline must still be unloaded"


def test_oom_at_1mp_still_runs_ladder(gen, harness, monkeypatch):
    # 1024² is the calibrated regime — the sequential-offload retry must remain.
    monkeypatch.setattr(gen, "_oom_fallback_catalog_key", lambda *_: None)
    result = gen.generate_image(_request(1024, 1024))
    assert result.success is False
    assert harness["loads"] == [False, True], (
        "≤1MP OOM must still attempt the sequential-offload reload"
    )


def test_tiling_gate_refuses_2k_without_tiling(gen, harness):
    harness["pipeline_factory"] = _NoTilingPipeline
    result = gen.generate_image(_request(2048, 2048))
    assert result.success is False
    assert result.error and "requires VAE tiling" in result.error
    assert harness["pipeline"].call_count == 0, "forward must never start"


def test_cpu_device_refuses_dit_families(gen, harness):
    gen._device = "cpu"
    result = gen.generate_image(_request(1024, 1024))
    assert result.success is False
    assert result.error and "refusing to run zimage on CPU" in result.error
    assert harness["loads"] == [], "no pipeline load on a CPU box for DiT"


def test_oom_gate_reached_when_cuda_rng_cannot_init(gen, harness, monkeypatch):
    """A CUDA-less process (CI runner, driver mismatch) must still reach the
    large-canvas OOM gate instead of dying while building the seed generator."""
    real_generator = oig.torch.Generator

    class _DriverlessGenerator:
        def __init__(self, device="cpu"):
            if str(device).startswith("cuda"):
                raise RuntimeError(
                    "CUDA error: CUDA driver version is insufficient for CUDA runtime version"
                )
            self._g = real_generator(device=device)

        def manual_seed(self, seed):
            self._g.manual_seed(seed)
            return self._g

    monkeypatch.setattr(oig.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(oig.torch, "Generator", _DriverlessGenerator)

    result = gen.generate_image(_request(2048, 2048))
    assert result.success is False
    assert result.error and "disabled by design" in result.error
    assert harness["loads"] == [False]
