"""GPU orchestrator hard_fit: refuse admit when free VRAM stays short."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_request_model_hard_fit_refuses_when_short():
    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator

    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = __import__("threading").RLock()
    orch._registry = {}
    orch._eviction_grace_s = 30
    orch._idle_timeout_s = 600

    def fake_vram():
        return {"success": True, "available_mb": 400, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                with pytest.raises(RuntimeError, match="GPU short|refusing|only"):
                    orch.request_model(
                        "sd:pipeline",
                        vram_estimate_mb=11000,
                        priority=85,
                        hard_fit=True,
                    )


def test_request_model_soft_fit_admits_when_short():
    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator, SlotState

    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = __import__("threading").RLock()
    orch._registry = {}
    orch._eviction_grace_s = 30
    orch._idle_timeout_s = 600

    def fake_vram():
        return {"success": True, "available_mb": 400, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                slot = orch.request_model(
                    "sd:pipeline",
                    vram_estimate_mb=11000,
                    priority=85,
                    hard_fit=False,
                )
    assert slot.slot_id == "sd:pipeline"
    assert slot.state == SlotState.LOADING


def test_request_model_reclaims_when_registry_eviction_frees_nothing_physical():
    """A registry-only slot (a plugin's) counts as freed but leaves its memory on the
    card; the admission measures again and reclaims Ollama instead of refusing."""
    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator, SlotState

    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = __import__("threading").RLock()
    orch._registry = {}
    orch._eviction_grace_s = 30
    orch._idle_timeout_s = 600
    card = {"free": 5700}

    def fake_vram():
        return {"success": True, "available_mb": card["free"], "total_mb": 16376}

    def reclaim(needed):
        card["free"] = 14000
        return 8000

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=14400):
            with patch.object(orch, "_physical_reclaim_untracked", side_effect=reclaim) as rec:
                slot = orch.request_model("audio_foundry:fx", vram_estimate_mb=6000, priority=50, hard_fit=True)
    assert rec.call_count == 1
    assert slot.state == SlotState.LOADING
    assert slot.cleared_chat is True
    assert orch.chat_cleared_for() == "audio_foundry:fx"


def test_keep_ready_steps_aside_for_a_slot_given_the_chat_models_memory(monkeypatch):
    from backend.services import chat_keep_ready

    class Orch:
        def exclusive_holder(self):
            return None

        def chat_cleared_for(self):
            return "audio_foundry:fx"

    monkeypatch.setattr("backend.services.gpu_memory_orchestrator.get_orchestrator_if_created", lambda: Orch())
    reason = chat_keep_ready._gpu_busy_reason()
    assert reason and "audio_foundry:fx" in reason



def test_keep_ready_steps_aside_while_a_job_waits_for_the_gpu(monkeypatch):
    import backend.services.job_operation_gate as jog
    from backend.services import chat_keep_ready
    from backend.services.job_types import JobKind

    class Coord:
        def get_gpu_status(self):
            return {"available": True}

    gate = jog.JobOperationGate()
    monkeypatch.setattr(jog, "get_gate", lambda: gate)
    monkeypatch.setattr("backend.services.gpu_memory_orchestrator.get_orchestrator_if_created", lambda: None)
    monkeypatch.setattr("backend.services.gpu_resource_coordinator.get_gpu_coordinator", lambda: Coord())
    monkeypatch.setattr(chat_keep_ready.requests, "get", MagicMock(side_effect=OSError("no ComfyUI")))
    assert chat_keep_ready._gpu_busy_reason() is None
    with gate.waiting_for_gpu(JobKind.VIDEO_RENDER, "edit"):
        assert chat_keep_ready._gpu_busy_reason() == "a job is waiting for the GPU"
    assert chat_keep_ready._gpu_busy_reason() is None


def test_a_claim_waits_for_a_keep_ready_load_already_under_way(monkeypatch):
    import threading
    from backend.services import chat_keep_ready

    landed = threading.Event()
    load = threading.Thread(target=lambda: landed.wait(5), daemon=True)
    load.start()
    monkeypatch.setattr(chat_keep_ready, "_load_thread", load)
    threading.Timer(0.2, landed.set).start()
    chat_keep_ready.wait_for_load(timeout_s=5)
    assert not load.is_alive()

# --- Compositor VRAM reserve (2026-08-04 client box 2048² incident) --------------
# Opt-in per caller: reserved MB are treated as not-free so the desktop
# compositor's share of the card survives the job. Default 0 keeps existing
# (incl. near-full-card video) admissions byte-identical.

def _bare_orch():
    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator

    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = __import__("threading").RLock()
    orch._registry = {}
    orch._eviction_grace_s = 30
    orch._idle_timeout_s = 600
    return orch


def test_request_model_reserve_refuses_within_reserve_band():
    orch = _bare_orch()

    def fake_vram():
        return {"success": True, "available_mb": 15600, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                with pytest.raises(RuntimeError, match="GPU short"):
                    orch.request_model(
                        "sd:pipeline",
                        vram_estimate_mb=15400,
                        priority=85,
                        hard_fit=True,
                        vram_reserve_mb=800,
                    )


def test_request_model_reserve_zero_keeps_video_admission():
    # The same near-full-card estimate admits with reserve=0 (mostly-free bypass)
    # — the video path must not regress.
    orch = _bare_orch()

    def fake_vram():
        return {"success": True, "available_mb": 15600, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                slot = orch.request_model(
                    "video_render:ltx",
                    vram_estimate_mb=15400,
                    priority=85,
                    hard_fit=True,
                )
    assert slot.slot_id == "video_render:ltx"


def test_request_model_reserve_admits_when_it_fits_beside_reserve():
    orch = _bare_orch()

    def fake_vram():
        return {"success": True, "available_mb": 15600, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                slot = orch.request_model(
                    "sd:pipeline",
                    vram_estimate_mb=14000,
                    priority=85,
                    hard_fit=True,
                    vram_reserve_mb=800,
                )
    assert slot.slot_id == "sd:pipeline"
