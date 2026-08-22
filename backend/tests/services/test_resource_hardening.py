"""Resource-layer hardening: sync merge, lease renewal, pre-reclaim capacity test,
restore-on-start gating."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services.gpu_memory_orchestrator import (
    GPUMemoryOrchestrator,
    ModelSlot,
    ModelType,
    SlotState,
)


def _bare_orch() -> GPUMemoryOrchestrator:
    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = threading.RLock()
    orch._registry = {}
    orch._eviction_grace_s = 0
    orch._idle_timeout_s = 300
    return orch


def _slot(slot_id, model_type, *, loaded_at, in_use=0, state=SlotState.LOADED):
    return ModelSlot(slot_id=slot_id, model_type=model_type, vram_mb=1000,
                     loaded_at=loaded_at, last_used=loaded_at, state=state, in_use=in_use)


# ---- orchestrator sync keeps what the probe cannot see -----------------------
def _sync(orch, now):
    no_ollama = MagicMock()
    no_ollama.status_code = 500
    with patch("time.time", return_value=now), \
         patch("backend.services.gpu_memory_orchestrator.requests.get", return_value=no_ollama), \
         patch("backend.services.offline_image_generator._generator_instance", None):
        orch._sync_from_hardware()


def test_sync_keeps_session_bookings_pins_and_loading():
    orch = _bare_orch()
    now = 1_000_000.0
    old = now - 10_000
    orch._registry = {
        "video_render:batch1": _slot("video_render:batch1", ModelType.VIDEO_PIPELINE, loaded_at=old),
        "sd:pipeline": _slot("sd:pipeline", ModelType.SD_PIPELINE, loaded_at=old, in_use=1),
        "ollama:loading": _slot("ollama:loading", ModelType.OLLAMA_LLM, loaded_at=old, state=SlotState.LOADING),
        "sd:fresh": _slot("sd:fresh", ModelType.SD_PIPELINE, loaded_at=now - 10),
        "ollama:gone": _slot("ollama:gone", ModelType.OLLAMA_LLM, loaded_at=old),
        "sd:stale": _slot("sd:stale", ModelType.SD_PIPELINE, loaded_at=old),
    }
    _sync(orch, now)
    kept = set(orch._registry)
    assert {"video_render:batch1", "sd:pipeline", "ollama:loading", "sd:fresh"} <= kept
    assert "ollama:gone" not in kept and "sd:stale" not in kept


def test_sync_registers_resident_sd_under_pinned_id():
    orch = _bare_orch()
    now = 1_000_000.0
    orch._registry = {"sd:pipeline": _slot("sd:pipeline", ModelType.SD_PIPELINE, loaded_at=now - 10_000)}
    gen = MagicMock()
    gen._pipeline = object()
    gen._current_model = "zimage-turbo"
    no_ollama = MagicMock()
    no_ollama.status_code = 500
    with patch("time.time", return_value=now), \
         patch("backend.services.gpu_memory_orchestrator.requests.get", return_value=no_ollama), \
         patch("backend.services.offline_image_generator._generator_instance", gen):
        orch._sync_from_hardware()
    assert "sd:pipeline" in orch._registry
    assert "sd:zimage-turbo" not in orch._registry


def test_drop_booking_forgets_slot_without_unloading():
    orch = _bare_orch()
    orch._registry["video_render:x"] = _slot("video_render:x", ModelType.VIDEO_PIPELINE, loaded_at=1.0)
    with patch.object(orch, "_unload_model") as unload:
        assert orch.drop_booking("video_render:x") is True
        assert orch.drop_booking("video_render:x") is False
        unload.assert_not_called()
    assert "video_render:x" not in orch._registry


# ---- cross-process lease: renew + atomic acquire -----------------------------
@pytest.fixture
def coord(tmp_path):
    """A coordinator on a private lock file. ``__new__`` is the singleton hook,
    so the previous instance is restored afterwards."""
    from backend.services.gpu_resource_coordinator import GPUResourceCoordinator
    previous = GPUResourceCoordinator._instance
    c = object.__new__(GPUResourceCoordinator)
    c._internal_lock = threading.RLock()
    c._initialized = True
    c.LOCK_FILE = tmp_path / "gpu_lock.json"
    try:
        yield c
    finally:
        c._release_lock_file()
        GPUResourceCoordinator._instance = previous


def test_renew_extends_own_lease_only(coord):
    assert coord.acquire_generic("job-a", lease_seconds=60)["success"]
    before = coord._read_lock_file().lease_expires_at
    assert coord.renew_generic("job-a", lease_seconds=3600) is True
    after = coord._read_lock_file().lease_expires_at
    assert after > before
    assert coord.renew_generic("job-b", lease_seconds=3600) is False
    assert coord.release_generic("job-a")["success"]


def test_acquire_refuses_while_held_and_takes_flock(coord):
    assert coord.acquire_generic("job-a", lease_seconds=60)["success"]
    assert coord.acquire_generic("job-b", lease_seconds=60)["success"] is False
    assert (coord.LOCK_FILE.with_suffix(".flock")).exists()


# ---- pre-reclaim capacity test -----------------------------------------------
def _probe(free_mb, total_mb):
    c = MagicMock()
    c.get_available_vram.return_value = {"success": True, "available_mb": free_mb, "total_mb": total_mb}
    return c


def test_reclaim_skipped_when_estimate_already_fits():
    from backend.services import gpu_resource_policy as p
    with patch("backend.services.gpu_resource_coordinator.get_gpu_coordinator", return_value=_probe(15000, 16000)):
        assert p._reclaim_needed(8000) is False
        assert p._reclaim_needed(14500) is True


def test_reclaim_refuses_capacity_overflow_without_evicting():
    from backend.services import gpu_resource_policy as p
    from backend.services.job_operation_gate import GpuBusyError
    with patch("backend.services.gpu_resource_coordinator.get_gpu_coordinator", return_value=_probe(100, 16000)):
        with pytest.raises(GpuBusyError):
            p._reclaim_needed(20000)


def test_reclaim_needed_when_probe_unavailable():
    from backend.services import gpu_resource_policy as p
    bad = MagicMock()
    bad.get_available_vram.return_value = {"success": False}
    with patch("backend.services.gpu_resource_coordinator.get_gpu_coordinator", return_value=bad):
        assert p._reclaim_needed(8000) is True


def test_fit_verdict_capacity_raises_typed_error():
    from backend.services import gpu_resource_policy as p
    from backend.services.job_operation_gate import GpuCapacityError
    with patch("backend.services.gpu_resource_coordinator.get_gpu_coordinator", return_value=_probe(100, 16000)):
        fit = p.fit_verdict(20000)
    assert fit.ok is False and fit.capacity is True
    assert (fit.free_mb, fit.total_mb, fit.need_mb) == (100, 16000, 21024)
    with pytest.raises(
        GpuCapacityError,
        match="Not enough free VRAM for video_render:x: estimate exceeds GPU capacity",
    ):
        p._raise_unless_fits(fit, "video_render:x")


def test_default_lease_by_kind():
    from backend.services import gpu_resource_policy as p
    from backend.services.job_types import JobKind
    assert p._default_lease_seconds(JobKind.LORA_TRAIN) == 4 * 3600
    assert p._default_lease_seconds(JobKind.VIDEO_RENDER) == 3600
    assert p._default_lease_seconds("other") == 900


# ---- restore-on-start only in the API process --------------------------------
def test_restore_allowed_gating(monkeypatch):
    from backend.services.batch_video_generator import BatchVideoGenerator
    for var in ("GUAARDVARK_VIDEO_RESTORE_ON_START", "CELERY_WORKER_MODE", "GUAARDVARK_MCP_PROCESS"):
        monkeypatch.delenv(var, raising=False)
    assert BatchVideoGenerator._restore_allowed() is True
    monkeypatch.setenv("CELERY_WORKER_MODE", "true")
    assert BatchVideoGenerator._restore_allowed() is False
    monkeypatch.delenv("CELERY_WORKER_MODE")
    monkeypatch.setenv("GUAARDVARK_MCP_PROCESS", "1")
    assert BatchVideoGenerator._restore_allowed() is False
    monkeypatch.delenv("GUAARDVARK_MCP_PROCESS")
    monkeypatch.setenv("GUAARDVARK_VIDEO_RESTORE_ON_START", "0")
    assert BatchVideoGenerator._restore_allowed() is False
