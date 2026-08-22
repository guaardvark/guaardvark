"""Activity-aware Comfy wait + VRAM admit retry (no cascade fail)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError


# ---------------------------------------------------------------------------
# Activity-aware wait helpers
# ---------------------------------------------------------------------------

def test_card_looks_busy_high_util():
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

    gen = object.__new__(ComfyUIVideoGenerator)
    gen._gpu_activity_snapshot = lambda: {
        "free_mb": 3000, "used_mb": 13000, "total_mb": 16000, "util_pct": 81.0
    }
    assert gen._card_looks_busy() is True


def test_card_looks_busy_idle():
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

    gen = object.__new__(ComfyUIVideoGenerator)
    gen._gpu_activity_snapshot = lambda: {
        "free_mb": 14000, "used_mb": 2000, "total_mb": 16000, "util_pct": 12.5
    }
    assert gen._card_looks_busy() is False


def test_wait_for_completion_extends_while_active(monkeypatch):
    """Past soft budget, still in queue → keep waiting until history appears."""
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

    gen = object.__new__(ComfyUIVideoGenerator)
    gen.comfy_url = "http://127.0.0.1:8188"

    ticks = {"n": 0}
    history_payloads = [
        {},  # soft budget not done yet
        {},  # still active after soft budget
        {"pid": {"status": {"completed": True, "status_str": "success"}, "outputs": {"1": {"gifs": []}}}},
    ]

    monkeypatch.setattr(gen, "_comfyui_alive", lambda: True)
    monkeypatch.setattr(gen, "_prompt_in_queue", lambda pid: True)
    monkeypatch.setattr(gen, "_card_looks_busy", lambda: True)
    monkeypatch.setattr(gen, "_gpu_activity_snapshot", lambda: {"util_pct": 90, "free_mb": 2000})

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            idx = min(ticks["n"], len(history_payloads) - 1)
            data = history_payloads[idx]
            # rewrite key to actual prompt id on last payload
            if "pid" in data:
                return {"prompt-1": data["pid"]}
            return data

    def fake_get(url, timeout=5):
        return _Resp()

    monkeypatch.setattr(
        "backend.services.comfyui_video_generator.requests.get", fake_get
    )

    real_sleep = __import__("time").sleep

    def fake_sleep(s):
        ticks["n"] += 1
        # Don't actually sleep

    monkeypatch.setattr("backend.services.comfyui_video_generator.time.sleep", fake_sleep)

    # Force soft budget tiny so first loops expire it quickly
    import time as _t
    start = _t.time()
    monkeypatch.setattr(
        "backend.services.comfyui_video_generator.time.time",
        lambda: start + ticks["n"] * 50,  # each poll advances 50s
    )

    out = gen._wait_for_completion("prompt-1", timeout=80, hard_ceiling_s=600)
    assert out is not None
    assert ticks["n"] >= 2  # extended past soft budget


def test_wait_for_completion_idle_after_soft_times_out(monkeypatch):
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

    gen = object.__new__(ComfyUIVideoGenerator)
    gen.comfy_url = "http://127.0.0.1:8188"
    ticks = {"n": 0}

    monkeypatch.setattr(gen, "_comfyui_alive", lambda: True)
    monkeypatch.setattr(gen, "_prompt_in_queue", lambda pid: False)
    monkeypatch.setattr(gen, "_card_looks_busy", lambda: False)
    monkeypatch.setattr(gen, "_gpu_activity_snapshot", lambda: {"util_pct": 10, "free_mb": 14000})

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {}

    monkeypatch.setattr(
        "backend.services.comfyui_video_generator.requests.get",
        lambda *a, **k: _Resp(),
    )
    monkeypatch.setattr(
        "backend.services.comfyui_video_generator.time.sleep",
        lambda s: ticks.__setitem__("n", ticks["n"] + 1),
    )
    import time as _t
    start = _t.time()
    # Advance enough to pass soft budget + idle_kill (90s)
    monkeypatch.setattr(
        "backend.services.comfyui_video_generator.time.time",
        lambda: start + 40 + ticks["n"] * 40,
    )

    out = gen._wait_for_completion("prompt-gone", timeout=60, hard_ceiling_s=600)
    assert out is None


# ---------------------------------------------------------------------------
# Batch VRAM admit retry
# ---------------------------------------------------------------------------

@dataclass
class _Status:
    batch_id: str
    status: str = "queued"
    total_videos: int = 1
    completed_videos: int = 0
    failed_videos: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    results: List = field(default_factory=list)
    error: Optional[str] = None
    output_dir: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    retry_data: Optional[Dict] = None
    stage: str = "queued"
    current_item: Optional[str] = None
    progress_pct: Optional[int] = 0


@dataclass
class _Req:
    batch_id: str = "VideoBatch_test"
    model: str = "wan22-5b"
    items: List = field(default_factory=list)


def test_run_batch_retries_resident_vram_then_succeeds(monkeypatch):
    from backend.services.batch_video_generator import BatchVideoGenerator

    gen = object.__new__(BatchVideoGenerator)
    gen.cancel_events = {}
    saves = []
    stages = []
    inner_calls = {"n": 0}

    def _set_stage(status, stage, current_item=None, save=True):
        status.stage = stage
        stages.append(stage)
        if save:
            saves.append(dict(status.metadata or {}))

    def _save_metadata(status):
        saves.append(dict(status.metadata or {}))

    def _inner(batch_request, status, *, parallel_comfyui=False):
        inner_calls["n"] += 1
        status.status = "completed"

    gen._set_stage = _set_stage
    gen._save_metadata = _save_metadata
    gen._run_batch_inner = _inner

    attempts = {"n": 0}
    resident = GpuBusyError(
        "Not enough free VRAM for video_render:x: need ~12024MB "
        "(est 11000 + 1024 headroom), only 2899MB usable after eviction — "
        "another model/render may be resident. Try again shortly."
    )

    @contextmanager
    def fake_session(*a, **k):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise resident
        yield True

    # Patch the symbols where _run_batch imports them from.
    import backend.services.gpu_resource_policy as grp

    reclaims = []
    monkeypatch.setattr(grp, "gpu_session", fake_session)
    monkeypatch.setattr(grp, "reclaim_gpu", lambda **k: reclaims.append(k))
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: reclaims.append("ollama"))
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda **k: reclaims.append("comfy"))
    monkeypatch.setenv("GUAARDVARK_VIDEO_VRAM_WAIT_S", "120")
    monkeypatch.setattr(
        "backend.services.video_model_registry.vram_mb_for_model",
        lambda m: 11000,
    )
    monkeypatch.setattr(
        grp, "vram_probe_snapshot", lambda **k: {"free_mb": 3000, "success": True}
    )
    monkeypatch.setattr("time.sleep", lambda s: None)

    status = _Status(batch_id="VideoBatch_retry")
    req = _Req(batch_id="VideoBatch_retry")
    gen._run_batch(req, status)

    assert inner_calls["n"] == 1
    assert attempts["n"] == 3
    assert status.status == "completed"
    assert "gpu_wait" in stages
    assert status.status != "error"
    assert reclaims == [], "the wait loop must not evict; gpu_session reclaims after it wins"


def test_run_batch_capacity_overflow_no_retry(monkeypatch):
    from backend.services.batch_video_generator import BatchVideoGenerator

    gen = object.__new__(BatchVideoGenerator)
    gen.cancel_events = {}
    gen._set_stage = lambda status, stage, current_item=None, save=True: setattr(status, "stage", stage)
    gen._save_metadata = lambda status: None
    gen._run_batch_inner = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run"))

    overflow = GpuCapacityError(
        "Not enough free VRAM for video_render:x: estimate exceeds GPU capacity "
        "(~20000MB needed, card total ~16376MB)"
    )
    attempts = {"n": 0}

    @contextmanager
    def fake_session(*a, **k):
        attempts["n"] += 1
        raise overflow
        yield  # pragma: no cover

    import backend.services.gpu_resource_policy as grp
    monkeypatch.setattr(grp, "gpu_session", fake_session)
    monkeypatch.setattr(grp, "vram_probe_snapshot", lambda **k: {"free_mb": 16000, "success": True})
    monkeypatch.setattr(
        "backend.services.video_model_registry.vram_mb_for_model", lambda m: 20000
    )
    monkeypatch.setattr("time.sleep", lambda s: None)

    status = _Status(batch_id="VideoBatch_cap")
    gen._run_batch(_Req(batch_id="VideoBatch_cap", model="huge"), status)
    assert attempts["n"] == 1, "a capacity refusal is terminal"
    assert status.status == "error"
    assert status.stage == "done"
    assert "estimate exceeds GPU capacity" in (status.error or "") or "Could not acquire GPU" in (status.error or "")


def test_run_batch_busy_until_deadline_fails_without_evicting(monkeypatch):
    """A plain GpuBusyError is retried with backoff until the deadline, and the
    loop itself never evicts anything."""
    from backend.services.batch_video_generator import BatchVideoGenerator

    gen = object.__new__(BatchVideoGenerator)
    gen.cancel_events = {}
    gen._set_stage = lambda status, stage, current_item=None, save=True: setattr(status, "stage", stage)
    gen._save_metadata = lambda status: None
    gen._run_batch_inner = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run"))

    attempts = {"n": 0}

    @contextmanager
    def fake_session(*a, **k):
        attempts["n"] += 1
        raise GpuBusyError("GPU is held by training:job-7 — wait for completion")
        yield  # pragma: no cover

    import backend.services.gpu_resource_policy as grp
    reclaims = []
    monkeypatch.setattr(grp, "gpu_session", fake_session)
    monkeypatch.setattr(grp, "reclaim_gpu", lambda **k: reclaims.append(k))
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: reclaims.append("ollama"))
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda **k: reclaims.append("comfy"))
    monkeypatch.setattr(grp, "vram_probe_snapshot", lambda **k: {"free_mb": 2000, "success": True})
    monkeypatch.setattr(
        "backend.services.video_model_registry.vram_mb_for_model", lambda m: 11000
    )
    monkeypatch.setenv("GUAARDVARK_VIDEO_VRAM_WAIT_S", "60")

    clock = {"t": 1_000_000.0}

    def fake_time():
        clock["t"] += 2.0
        return clock["t"]

    monkeypatch.setattr("time.time", fake_time)
    monkeypatch.setattr("time.sleep", lambda s: None)

    status = _Status(batch_id="VideoBatch_deadline")
    gen._run_batch(_Req(batch_id="VideoBatch_deadline"), status)

    assert status.status == "error"
    assert status.stage == "done"
    assert "Could not acquire enough free VRAM after waiting 60s" in (status.error or "")
    assert attempts["n"] > 1
    assert reclaims == []


def test_run_batch_cancel_during_vram_wait(monkeypatch):
    from backend.services.batch_video_generator import BatchVideoGenerator

    gen = object.__new__(BatchVideoGenerator)
    ev = threading.Event()
    gen.cancel_events = {"VideoBatch_cancel": ev}
    gen._set_stage = lambda status, stage, current_item=None, save=True: setattr(status, "stage", stage)
    gen._save_metadata = lambda status: None

    attempts = {"n": 0}
    resident = GpuBusyError(
        "need ~12024MB, only 2899MB usable after eviction — another model/render may be resident"
    )

    @contextmanager
    def fake_session(*a, **k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise resident
        # After first fail, cancel before retry sleep finishes
        ev.set()
        raise resident
        yield  # pragma: no cover

    import backend.services.gpu_resource_policy as grp
    monkeypatch.setattr(grp, "gpu_session", fake_session)
    monkeypatch.setattr(grp, "vram_probe_snapshot", lambda **k: {"free_mb": 2000, "success": True})
    monkeypatch.setattr(
        "backend.services.video_model_registry.vram_mb_for_model", lambda m: 11000
    )
    monkeypatch.setenv("GUAARDVARK_VIDEO_VRAM_WAIT_S", "120")
    monkeypatch.setattr("time.sleep", lambda s: None)

    status = _Status(batch_id="VideoBatch_cancel")
    gen._run_batch(_Req(batch_id="VideoBatch_cancel"), status)
    assert status.status == "cancelled"
    assert status.stage == "done"
