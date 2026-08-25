"""F-RAG-10: the retrieval cross-encoder must be reclaimable VRAM, not a squatter.

It loaded itself on the first RAG query, took ~2.4GB of a 16GB card in fp32 and had
no unload path at all, so every image batch behind it was refused with "another
model/render may be resident" and eviction freed exactly zero. These guards cover
the three things that were missing: an unload, an orchestrator that knows the model
exists, and a reclaim that actually reaches it.
"""
from __future__ import annotations

import threading
from unittest.mock import patch

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


@pytest.fixture(autouse=True)
def _clean_reranker_state():
    """Never let a test leak module state into the next one."""
    from backend.utils import reranker
    saved = (reranker._model, reranker._model_device,
             reranker._model_vram_mb, reranker._inflight,
             reranker._load_failed_reason)
    yield
    (reranker._model, reranker._model_device, reranker._model_vram_mb,
     reranker._inflight, reranker._load_failed_reason) = saved


class _FakeModel:
    """Stands in for the CrossEncoder; predict() ranks by text length."""
    def predict(self, pairs, show_progress_bar=False):
        return [float(len(p[1])) for p in pairs]


# --------------------------------------------------------------------------
# reranker.unload()
# --------------------------------------------------------------------------
def test_unload_when_nothing_loaded_is_success_not_failure():
    """A caller dropping a stale registry slot must be able to trust the answer."""
    from backend.utils import reranker
    reranker._model = None

    info = reranker.unload()
    assert info["unloaded"] is True
    assert info["freed_mb"] == 0


def test_unload_releases_the_model_and_reports_what_it_freed():
    from backend.utils import reranker
    reranker._model = _FakeModel()
    reranker._model_device = "cuda"
    reranker._model_vram_mb = 1334

    info = reranker.unload()
    assert info["unloaded"] is True
    assert info["freed_mb"] == 1334
    assert reranker._model is None
    assert reranker._model_device is None
    assert reranker._model_vram_mb == 0


def test_unload_is_refused_while_a_rerank_is_in_flight():
    """Dropping the reference mid-predict frees nothing — the running call still
    holds it — so reporting freed VRAM would be a lie."""
    from backend.utils import reranker
    reranker._model = _FakeModel()
    reranker._model_device = "cuda"
    reranker._model_vram_mb = 1334
    reranker._inflight = 1

    info = reranker.unload()
    assert info["unloaded"] is False
    assert info["freed_mb"] == 0
    assert "in use" in info["reason"]
    assert reranker._model is not None


def test_unload_does_not_poison_the_next_load():
    """An unload is not a load failure; the next query must be free to reload."""
    from backend.utils import reranker
    reranker._model = _FakeModel()
    reranker._model_device = "cuda"
    reranker._model_vram_mb = 1334
    reranker._load_failed_reason = None

    reranker.unload()
    assert reranker._load_failed_reason is None


def test_rerank_pins_the_model_so_eviction_cannot_race_it():
    """in_use must be visible to the orchestrator for the duration of predict()."""
    from backend.utils import reranker

    seen = {}

    class _Watching(_FakeModel):
        def predict(self, pairs, show_progress_bar=False):
            seen["inflight"] = reranker._inflight
            seen["unload_refused"] = reranker.unload()["unloaded"] is False
            return super().predict(pairs, show_progress_bar=show_progress_bar)

    reranker._model = _Watching()
    reranker._model_device = "cuda"
    reranker._model_vram_mb = 1334
    reranker._inflight = 0
    reranker._load_failed_reason = None

    with patch.object(reranker, "_get_model", return_value=reranker._model):
        out, info = reranker.rerank("q", [{"text": "short"}, {"text": "much longer text"}])

    assert info["applied"] is True
    assert seen["inflight"] == 1
    assert seen["unload_refused"] is True
    assert reranker._inflight == 0, "pin must be released even on the happy path"
    assert out[0]["text"] == "much longer text"


def test_rerank_releases_the_pin_when_predict_raises():
    """A crashed rerank must not wedge the model on the card forever."""
    from backend.utils import reranker

    class _Exploding:
        def predict(self, pairs, show_progress_bar=False):
            raise RuntimeError("boom")

    reranker._model = _Exploding()
    reranker._model_device = "cuda"
    reranker._inflight = 0

    with patch.object(reranker, "_get_model", return_value=reranker._model):
        _, info = reranker.rerank("q", [{"text": "a"}, {"text": "b"}])

    assert info["applied"] is False
    assert reranker._inflight == 0
    assert reranker.unload()["unloaded"] is True


def test_status_reports_residency_for_the_orchestrator_probe():
    from backend.utils import reranker
    reranker._model = _FakeModel()
    reranker._model_device = "cuda"
    reranker._model_vram_mb = 1334
    reranker._inflight = 2

    st = reranker.status()
    assert st["loaded"] is True
    assert st["device"] == "cuda"
    assert st["vram_mb"] == 1334
    assert st["in_use"] == 2


# --------------------------------------------------------------------------
# Orchestrator wiring
# --------------------------------------------------------------------------
def test_slot_id_maps_to_the_reranker_type():
    """Unknown prefixes default to OLLAMA_LLM, which would send the unload to the
    wrong backend and silently free nothing."""
    orch = _bare_orch()
    assert orch._infer_model_type("rerank:cross_encoder") == ModelType.RERANKER


def test_unloading_a_reranker_slot_calls_the_module_and_drops_the_slot():
    orch = _bare_orch()
    orch._registry["rerank:cross_encoder"] = ModelSlot(
        slot_id="rerank:cross_encoder",
        model_type=ModelType.RERANKER,
        vram_mb=1334,
        loaded_at=0.0,
        last_used=0.0,
        priority=30,
        state=SlotState.LOADED,
    )

    with patch("backend.utils.reranker.unload",
               return_value={"unloaded": True, "freed_mb": 1334, "reason": None}) as m:
        assert orch._unload_model(orch._registry["rerank:cross_encoder"]) is True

    m.assert_called_once()
    assert "rerank:cross_encoder" not in orch._registry


def test_a_refused_unload_keeps_the_slot_tracked():
    """If the module says it is busy, the orchestrator must not pretend it is gone."""
    orch = _bare_orch()
    slot = ModelSlot(
        slot_id="rerank:cross_encoder",
        model_type=ModelType.RERANKER,
        vram_mb=1334,
        loaded_at=0.0,
        last_used=0.0,
        priority=30,
        state=SlotState.LOADED,
    )
    orch._registry["rerank:cross_encoder"] = slot

    with patch("backend.utils.reranker.unload",
               return_value={"unloaded": False, "freed_mb": 0, "reason": "in use (1 in flight)"}):
        assert orch._unload_model(slot) is False

    assert orch._registry["rerank:cross_encoder"] is slot
    assert slot.state == SlotState.LOADED


def test_hardware_sync_discovers_a_gpu_resident_reranker():
    """Nothing registers this slot; the probe is the only way it becomes visible."""
    orch = _bare_orch()

    with patch("backend.utils.reranker.status",
               return_value={"loaded": True, "device": "cuda", "vram_mb": 1334,
                             "in_use": 0, "model": "BAAI/bge-reranker-v2-m3"}):
        orch._sync_from_hardware()

    slot = orch._registry.get("rerank:cross_encoder")
    assert slot is not None
    assert slot.model_type == ModelType.RERANKER
    assert slot.vram_mb == 1334
    assert slot.priority == 30, "must be cheap to evict — reload is ~1.1s"


def test_hardware_sync_ignores_a_cpu_resident_reranker():
    """On CPU it costs no VRAM, so tracking it would inflate tracked_vram_mb."""
    orch = _bare_orch()

    with patch("backend.utils.reranker.status",
               return_value={"loaded": True, "device": "cpu", "vram_mb": 0,
                             "in_use": 0, "model": "BAAI/bge-reranker-v2-m3"}):
        orch._sync_from_hardware()

    assert "rerank:cross_encoder" not in orch._registry


def test_hardware_sync_drops_the_slot_once_the_model_is_gone():
    """The probe is authoritative — a stale slot would keep phantom VRAM booked."""
    orch = _bare_orch()
    orch._registry["rerank:cross_encoder"] = ModelSlot(
        slot_id="rerank:cross_encoder",
        model_type=ModelType.RERANKER,
        vram_mb=1334,
        loaded_at=1.0,          # older than SYNC_GRACE_SECONDS
        last_used=1.0,
        priority=30,
        state=SlotState.LOADED,
    )

    with patch("backend.utils.reranker.status",
               return_value={"loaded": False, "device": None, "vram_mb": 0,
                             "in_use": 0, "model": "BAAI/bge-reranker-v2-m3"}):
        orch._sync_from_hardware()

    assert "rerank:cross_encoder" not in orch._registry


def test_physical_reclaim_reaches_the_reranker_without_waiting_for_a_sync():
    """The batch that starts 5s after the first RAG query is the whole bug — the
    reclaim cannot wait 30s for the registry to notice."""
    orch = _bare_orch()

    with patch("backend.utils.reranker.unload",
               return_value={"unloaded": True, "freed_mb": 1334, "reason": None}) as m, \
         patch.object(orch, "_unload_sd_pipeline", return_value=False), \
         patch("backend.services.gpu_resource_policy.evict_ollama_models", return_value=True):
        freed = orch._physical_reclaim_untracked(12024)

    m.assert_called_once()
    assert freed >= 1334
