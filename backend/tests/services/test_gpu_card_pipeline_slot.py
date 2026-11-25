"""The GPU card's sd:pipeline row follows the pipeline, not the admission booking.

The card read "sd:pipeline 10.7G Loaded" with 1.6 GB of the card in use: the
slot kept the 11000 MB admission estimate while the pipeline ran with
sequential CPU offload (measured peak about 2.4 GB), and an unloaded pipeline's
slot stayed listed because the unload only called release_model, which restarts
the idle timer. Now the measured peak replaces the estimate and the owner drops
the slot when it unloads.
"""

import threading
import time

import pytest

from backend.services import batch_image_generator as big
from backend.services import gpu_memory_orchestrator as gmo
from backend.services import offline_image_generator as oig
from backend.services.gpu_memory_orchestrator import (
    GPUMemoryOrchestrator, ModelSlot, ModelType, SlotState,
)


@pytest.fixture
def orch(monkeypatch):
    """A real orchestrator object without its background thread or hardware probe."""
    o = object.__new__(GPUMemoryOrchestrator)
    o._lock = threading.RLock()
    o._registry = {}
    o._quality_tier = "balanced"
    o._idle_timeout_s = 300
    o._eviction_grace_s = 30
    o._get_vram_info = lambda: {
        "success": True, "total_mb": 16376, "used_mb": 1638, "available_mb": 14738,
        "gpu_name": "test card", "utilization_percent": 0,
    }
    now = time.time()
    o._registry["sd:pipeline"] = ModelSlot(
        slot_id="sd:pipeline", model_type=ModelType.SD_PIPELINE, vram_mb=11000,
        loaded_at=now, last_used=now, priority=85, state=SlotState.LOADED,
    )
    monkeypatch.setattr(gmo, "_orchestrator_instance", o)
    return o


def _card_rows(o):
    snap = GPUMemoryOrchestrator.get_registry_snapshot(o)
    return {m["slot_id"]: m["vram_mb"] for m in snap["models"]}, snap["tracked_vram_mb"]


def _generator(released):
    gen = oig.OfflineImageGenerator.__new__(oig.OfflineImageGenerator)
    gen._generation_lock = threading.RLock()
    gen._pipeline = object()
    gen._current_model = "zimage-turbo"

    def _unload():
        released.append("pipeline")
        return True

    gen._unload_pipeline_unlocked = _unload
    return gen


def test_measured_peak_replaces_the_booking_on_the_card(orch):
    assert _card_rows(orch) == ({"sd:pipeline": 11000}, 11000)

    oig.record_pipeline_vram(2447)

    assert _card_rows(orch) == ({"sd:pipeline": 2447}, 2447)


def test_measured_figure_is_ignored_when_missing_or_unregistered(orch):
    assert orch.set_measured_vram("sd:pipeline", 0) is False
    assert orch.set_measured_vram("ollama:none", 900) is False
    assert _card_rows(orch)[0] == {"sd:pipeline": 11000}


def test_unloading_when_done_drops_the_slot(orch):
    released = []
    gen = _generator(released)

    assert gen._unload_pipeline(forget_slot=True) is True

    assert released == ["pipeline"]
    assert _card_rows(orch) == ({}, 0)


def test_unloading_mid_load_keeps_the_jobs_booking(orch):
    gen = _generator([])

    gen._unload_pipeline()

    assert "sd:pipeline" in orch._registry


def test_a_refused_unload_keeps_the_slot(orch):
    gen = _generator([])
    held = threading.Event()
    done = threading.Event()

    def _hold():
        with gen._generation_lock:
            held.set()
            done.wait(5)

    t = threading.Thread(target=_hold)
    t.start()
    held.wait(5)
    try:
        assert gen._unload_pipeline(wait=False, forget_slot=True) is False
    finally:
        done.set()
        t.join(5)
    assert "sd:pipeline" in orch._registry


def test_releasing_a_kept_pipeline_drops_the_slot(orch):
    gen = _generator([])
    gen.kept_model = lambda: "zimage-turbo"

    assert gen.release_kept_pipeline() is True

    assert "sd:pipeline" not in orch._registry


def test_batch_cleanup_drops_the_slot(orch):
    released = []
    batch = big.BatchImageGenerator.__new__(big.BatchImageGenerator)
    batch.image_generator = _generator(released)

    batch._cleanup_gpu_memory()

    assert released == ["pipeline"]
    assert "sd:pipeline" not in orch._registry
