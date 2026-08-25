"""The admission path must be able to reclaim VRAM this process is holding.

`gpu_session` runs gate -> lease -> reclaim -> fit -> load gate -> orchestrator.
Reclaim used to mean only "ask Ollama and ComfyUI to leave" — two HTTP calls to
other processes. Models this backend loaded into its own CUDA context answered to
neither, and the orchestrator's reclaim (which does know how to unload them) sits
behind the fit check that has already refused the job. Live consequence: image
batches refused by ~130MB while a 1.1GB cross-encoder sat on the card.

Also covers the image batch's wait-and-retry, which the video batch already had.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from unittest.mock import patch

import pytest

import backend.services.gpu_resource_policy as grp
import backend.services.job_operation_gate as jog
from backend.services.gpu_memory_orchestrator import (
    GPUMemoryOrchestrator,
    ModelSlot,
    ModelType,
    SlotState,
)
from backend.services.job_operation_gate import (
    GpuBusyError,
    GpuCapacityError,
    JobOperationGate,
)
from backend.services.job_types import JobKind


@pytest.fixture
def fresh_gate(monkeypatch):
    gate = JobOperationGate()
    monkeypatch.setattr(jog, "get_gate", lambda: gate)
    return gate


def _patch_vram(monkeypatch, *, free_mb: int, total_mb: int = 16376):
    import backend.services.gpu_resource_coordinator as coord

    class _Fake:
        def get_available_vram(self):
            return {"success": True, "available_mb": free_mb, "total_mb": total_mb}

    monkeypatch.setattr(coord, "get_gpu_coordinator", lambda: _Fake())


def _bare_orch() -> GPUMemoryOrchestrator:
    """A registry-only orchestrator.

    NOTE: `__new__` is a singleton, so this is the SAME object in every test and
    every test file. Reset the instance attributes you depend on (as below) and
    never mutate class-level config like SYNC_GRACE_SECONDS — that leaks.
    """
    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = threading.RLock()
    orch._registry = {}
    orch._eviction_grace_s = 0
    orch._idle_timeout_s = 300
    return orch


# --------------------------------------------------------------------------
# reclaim_gpu routing
# --------------------------------------------------------------------------
def test_reclaim_gpu_defaults_still_reclaim_nothing():
    """The documented invariant: all-defaults is a pure pass-through."""
    seen = []
    with patch.object(grp, "evict_ollama_models", lambda: seen.append("ollama")), \
         patch.object(grp, "free_comfyui_vram", lambda: seen.append("comfy")), \
         patch.object(grp, "reclaim_in_process_vram", lambda n=0: seen.append("in_process") or 0):
        grp.reclaim_gpu()
    assert seen == []


def test_reclaim_gpu_in_process_flag_routes_to_the_orchestrator():
    seen = []
    with patch.object(grp, "evict_ollama_models", lambda: seen.append("ollama")), \
         patch.object(grp, "free_comfyui_vram", lambda: seen.append("comfy")), \
         patch.object(grp, "reclaim_in_process_vram", lambda n=0: seen.append(("in_process", n)) or 0):
        grp.reclaim_gpu(evict_ollama=True, free_comfyui=True, in_process=True, needed_mb=11000)
    assert seen == ["ollama", "comfy", ("in_process", 11000)]


def test_in_process_reclaim_never_constructs_an_orchestrator():
    """Starting a background sync thread just to free memory is not acceptable;
    with no orchestrator the reranker is still reclaimed directly."""
    import backend.services.gpu_memory_orchestrator as gmo

    with patch.object(gmo, "get_orchestrator_if_created", return_value=None) as acc, \
         patch("backend.utils.reranker.unload",
               return_value={"unloaded": True, "freed_mb": 1210, "reason": None}) as rr:
        freed = grp.reclaim_in_process_vram(11000)

    acc.assert_called_once()
    rr.assert_called_once()
    assert freed == 1210


def test_in_process_reclaim_is_best_effort():
    with patch("backend.services.gpu_memory_orchestrator.get_orchestrator_if_created",
               side_effect=RuntimeError("boom")):
        assert grp.reclaim_in_process_vram(11000) == 0   # never raises


# --------------------------------------------------------------------------
# gpu_session: reclaim reaches in-process memory BEFORE the fit check
# --------------------------------------------------------------------------
def test_session_reclaims_in_process_before_the_fit_check(fresh_gate, monkeypatch):
    """The regression. 12694MB free - 800 reserve = 11894 against a need of 12024:
    short 130MB, with a 1210MB cross-encoder resident. Reclaiming it must let the
    job in rather than refusing over memory we ourselves are holding."""
    state = {"free": 12694}
    import backend.services.gpu_resource_coordinator as coord

    class _Fake:
        def get_available_vram(self):
            return {"success": True, "available_mb": state["free"], "total_mb": 16376}

    monkeypatch.setattr(coord, "get_gpu_coordinator", lambda: _Fake())

    order = []

    def fake_in_process(needed_mb=0):
        order.append("reclaim_in_process")
        state["free"] += 1210          # the cross-encoder leaves
        return 1210

    monkeypatch.setattr(grp, "reclaim_in_process_vram", fake_in_process)
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: order.append("ollama") or True)
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda **k: order.append("comfy") or True)
    monkeypatch.setattr(grp.time, "sleep", lambda s: None)
    monkeypatch.setattr(grp, "_orchestrator_request", lambda *a, **k: None)
    monkeypatch.setattr(grp, "_orchestrator_release", lambda *a, **k: None)

    with grp.gpu_session(
        JobKind.VIDEO_RENDER, "batch",
        evict_ollama=True, free_comfyui=True,
        vram_estimate_mb=11000, require_fit=True, vram_reserve_mb=800,
    ):
        order.append("admitted")

    assert "reclaim_in_process" in order
    assert order.index("reclaim_in_process") < order.index("admitted")
    assert "admitted" in order, "the job must be admitted once our own resident leaves"


def test_session_reclaims_in_process_even_without_the_http_evict_flags(fresh_gate, monkeypatch):
    """evict_ollama/free_comfyui name OTHER processes. A caller that asked for
    neither still must not be refused over this process's own memory."""
    _patch_vram(monkeypatch, free_mb=5000)
    seen = []
    monkeypatch.setattr(grp, "reclaim_in_process_vram", lambda n=0: seen.append(n) or 0)
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: seen.append("ollama") or True)
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda **k: seen.append("comfy") or True)

    with pytest.raises(GpuBusyError):
        with grp.gpu_session(
            JobKind.VIDEO_RENDER, "no-flags",
            vram_estimate_mb=11000, require_fit=True,
        ):
            pass

    assert seen == [11000], "in-process reclaim ran; the HTTP evictions were not requested"


def test_session_skips_all_reclaim_when_the_estimate_already_fits(fresh_gate, monkeypatch):
    """A job that fits must not cost anyone their resident model."""
    _patch_vram(monkeypatch, free_mb=15000)
    seen = []
    monkeypatch.setattr(grp, "reclaim_in_process_vram", lambda n=0: seen.append("in_process") or 0)
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: seen.append("ollama") or True)
    monkeypatch.setattr(grp, "_orchestrator_request", lambda *a, **k: None)
    monkeypatch.setattr(grp, "_orchestrator_release", lambda *a, **k: None)

    with grp.gpu_session(
        JobKind.VIDEO_RENDER, "fits",
        evict_ollama=True, vram_estimate_mb=11000, require_fit=True,
    ):
        pass

    assert seen == []


def test_capacity_refusal_still_precedes_any_reclaim(fresh_gate, monkeypatch):
    """An estimate larger than the card is terminal — evicting first would cost
    the user their chat model for a job that can never run."""
    _patch_vram(monkeypatch, free_mb=100)
    seen = []
    monkeypatch.setattr(grp, "reclaim_in_process_vram", lambda n=0: seen.append("in_process") or 0)
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: seen.append("ollama") or True)

    with pytest.raises(GpuCapacityError, match="estimate exceeds GPU capacity"):
        with grp.gpu_session(
            JobKind.VIDEO_RENDER, "too-big",
            evict_ollama=True, vram_estimate_mb=20000, require_fit=True,
        ):
            pass

    assert seen == []


# --------------------------------------------------------------------------
# Orchestrator: public in-process reclaim, and image_batch slot typing
# --------------------------------------------------------------------------
def test_auxiliary_reclaim_leaves_the_render_pipeline_alone():
    """The admission path must not evict what the caller is about to use.

    stills_pipeline opens a per-still session with require_fit=True and reuses a
    resident pipeline (keep_pipeline=True). An in-process reclaim that unloaded
    the diffusers pipeline would make every still reload ~11GB from disk — the
    reclaim would be "fixing" the shortfall by evicting the job's own model.
    """
    orch = _bare_orch()
    with patch.object(orch, "_unload_sd_pipeline") as sd, \
         patch("backend.utils.reranker.unload",
               return_value={"unloaded": True, "freed_mb": 1210, "reason": None}), \
         patch("backend.utils.docling_loader.unload", return_value=False), \
         patch("backend.utils.faster_whisper_utils.unload", return_value=False), \
         patch("backend.services.gpu_resource_policy.evict_ollama_models") as ollama:
        freed = orch.reclaim_auxiliary_models()

    assert freed == 1210
    sd.assert_not_called(), "the render pipeline is not an auxiliary model"
    ollama.assert_not_called(), "the admission path decides about Ollama itself"


def test_auxiliary_reclaim_sweeps_every_squatter_not_just_the_reranker():
    """docling (cuda by default, ~0.5GB) and faster-whisper (device='auto' ->
    CUDA) are the same shape as the cross-encoder: module globals, no lifecycle."""
    orch = _bare_orch()
    called = []
    with patch.object(orch, "_unload_sd_pipeline"), \
         patch("backend.utils.reranker.unload",
               side_effect=lambda: called.append("reranker") or {"unloaded": True, "freed_mb": 1210}), \
         patch("backend.utils.docling_loader.unload",
               side_effect=lambda: called.append("docling") or True), \
         patch("backend.utils.faster_whisper_utils.unload",
               side_effect=lambda: called.append("whisper") or True):
        orch.reclaim_auxiliary_models()

    assert called == ["reranker", "docling", "whisper"]


def test_auxiliary_reclaim_survives_one_module_blowing_up():
    orch = _bare_orch()
    with patch.object(orch, "_unload_sd_pipeline"), \
         patch("backend.utils.reranker.unload", side_effect=RuntimeError("boom")), \
         patch("backend.utils.docling_loader.unload", return_value=True), \
         patch("backend.utils.faster_whisper_utils.unload",
               return_value=False) as fw:
        orch.reclaim_auxiliary_models()   # must not raise
    fw.assert_called_once(), "a failure in one unload must not skip the rest"


def test_whisper_unload_no_longer_claims_a_free_it_never_performed():
    """_unload_whisper returned True unconditionally on the belief that whisper is
    always a subprocess. faster_whisper_utils loads CTranslate2 in-process."""
    orch = _bare_orch()
    with patch("backend.utils.faster_whisper_utils.unload") as fw:
        assert orch._unload_whisper() is True
    fw.assert_called_once()


def test_image_batch_slot_is_not_mistyped_as_an_ollama_model():
    """It fell through to OLLAMA_LLM, so the hardware sync treated a live booking
    as a discoverable Ollama model and purged it after the grace period."""
    orch = _bare_orch()
    assert orch._infer_model_type("image_batch:ImageBatch_08-24-2026_1") == ModelType.IMAGE_BATCH


def test_a_booking_is_never_evicted_out_from_under_its_owner():
    """Registry-only 'unload' frees nothing but would credit the caller its whole
    estimate, admitting a second job against VRAM still in use."""
    orch = _bare_orch()
    slot = ModelSlot(
        slot_id="image_batch:x", model_type=ModelType.IMAGE_BATCH, vram_mb=11000,
        loaded_at=0.0, last_used=0.0, priority=50, state=SlotState.LOADED,
    )
    orch._registry["image_batch:x"] = slot

    assert orch._unload_model(slot) is False
    assert orch._registry["image_batch:x"] is slot
    assert slot.state == SlotState.LOADED


def test_hardware_sync_keeps_a_live_image_batch_booking():
    orch = _bare_orch()
    # loaded_at in 1970 puts it far outside SYNC_GRACE_SECONDS, so surviving the
    # sync proves the slot TYPE is what keeps it, not the grace window. (Do not
    # mutate SYNC_GRACE_SECONDS here: GPUMemoryOrchestrator.__new__ is a
    # singleton, so every _bare_orch() in the suite is the same object and the
    # change would leak into other test files.)
    orch._registry["image_batch:x"] = ModelSlot(
        slot_id="image_batch:x", model_type=ModelType.IMAGE_BATCH, vram_mb=11000,
        loaded_at=1.0, last_used=1.0, priority=50, state=SlotState.LOADED,
    )
    with patch("backend.utils.reranker.status",
               return_value={"loaded": False, "device": None, "vram_mb": 0, "in_use": 0}):
        orch._sync_from_hardware()

    assert "image_batch:x" in orch._registry, "the probe cannot see bookings; it must not drop them"


def test_idle_eviction_does_not_churn_on_bookings():
    orch = _bare_orch()
    orch._idle_timeout_s = 0
    orch._registry["image_batch:x"] = ModelSlot(
        slot_id="image_batch:x", model_type=ModelType.IMAGE_BATCH, vram_mb=11000,
        loaded_at=0.0, last_used=0.0, priority=50, state=SlotState.LOADED,
    )
    with patch.object(orch, "_unload_model") as unload:
        orch._evict_idle_models()
    unload.assert_not_called()


# --------------------------------------------------------------------------
# Refusal classification: terminal vs retryable
# --------------------------------------------------------------------------
def test_margin_overflow_on_a_busy_card_is_retryable_not_terminal(monkeypatch):
    """est 16000 on a 16376MB card: need 17024 > total, so this looked like a
    capacity refusal — terminal, retry deadline discarded, "pick a lighter model".
    But it is only reachable while residents are on the card; once they leave the
    mostly-idle branch admits the identical job."""
    _patch_vram(monkeypatch, free_mb=12000, total_mb=16376)
    fit = grp.fit_verdict(16000, margin_mb=1024)

    assert fit.ok is False
    assert fit.capacity is False, "waiting can fix this; it is not a capacity limit"
    assert "another model/render may be resident" in fit.reason


def test_the_same_job_is_admitted_once_the_card_goes_idle(monkeypatch):
    _patch_vram(monkeypatch, free_mb=14500, total_mb=16376)
    assert grp.fit_verdict(16000, margin_mb=1024).ok is True


def test_an_estimate_larger_than_the_card_is_still_terminal(monkeypatch):
    """The one refusal no eviction and no waiting can fix."""
    _patch_vram(monkeypatch, free_mb=14500, total_mb=16376)
    fit = grp.fit_verdict(18000, margin_mb=1024)
    assert fit.ok is False
    assert fit.capacity is True
    assert "estimate exceeds GPU capacity" in fit.reason


# --------------------------------------------------------------------------
# Teardown: the cross-process lease must not survive its session
# --------------------------------------------------------------------------
def test_teardown_releases_the_lease_even_on_baseexception(fresh_gate, monkeypatch):
    """Every inner guard in _teardown catches Exception only. A KeyboardInterrupt
    or Celery revoke landing mid-teardown used to skip the lease release, and the
    heartbeat renews the lease forever, so nothing ever reclaims it."""
    _patch_vram(monkeypatch, free_mb=15000)
    released = []
    beats = []

    monkeypatch.setattr(grp, "_acquire_cross_process_lease", lambda *a, **k: True)
    monkeypatch.setattr(grp, "_release_cross_process_lease",
                        lambda slot: released.append(slot))

    class _Stop:
        def set(self):
            beats.append("stopped")

    monkeypatch.setattr(grp, "_start_lease_heartbeat", lambda *a, **k: _Stop())
    # The orchestrator release is where the interrupt lands.
    monkeypatch.setattr(grp, "_orchestrator_request", lambda *a, **k: None)
    monkeypatch.setattr(grp, "_orchestrator_release",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        with grp.gpu_session(
            JobKind.VIDEO_RENDER, "interrupted",
            vram_estimate_mb=11000, require_fit=True,
            cross_process=True, lease_seconds=3600,
        ):
            pass

    assert beats == ["stopped"], "the heartbeat must be stopped before anything can throw"
    assert released == ["video_render:interrupted"], "the lease must be released in a finally"
