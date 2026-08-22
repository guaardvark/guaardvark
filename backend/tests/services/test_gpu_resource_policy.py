"""P0.3a — the GpuResourcePolicy front door + canonical VRAM reclaim.

Locks: gpu_session delegates to the real JobOperationGate (preserving fail-fast
GpuBusyError + slot semantics), runs reclaim ONLY when the slot was actually won,
debits/releases the orchestrator budget when asked, and releases on exception. The
reclaim primitives are best-effort (never raise) and route by flag.
"""
import pytest

try:
    import backend.services.gpu_resource_policy as grp
    import backend.services.job_operation_gate as jog
    from backend.services.job_operation_gate import (
        GpuBusyError,
        GpuCapacityError,
        JobOperationGate,
    )
    from backend.services.job_types import JobKind
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def fresh_gate(monkeypatch):
    # A clean gate per test → no 8s-cooldown bleed across tests.
    gate = JobOperationGate()
    monkeypatch.setattr(jog, "get_gate", lambda: gate)
    return gate


# --- canonical reclaim primitives -------------------------------------------

def test_free_comfyui_vram_posts_free(monkeypatch):
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        return object()
    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    assert grp.free_comfyui_vram() is True
    assert calls["url"].endswith("/free")
    assert calls["json"] == {"unload_models": True, "free_memory": True}


def test_free_comfyui_vram_swallows_errors(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert grp.free_comfyui_vram() is False   # non-fatal


def test_evict_ollama_delegates_to_coordinator(monkeypatch):
    import backend.services.gpu_resource_coordinator as coord
    called = {}
    monkeypatch.setattr(coord, "unload_ollama_models", lambda *a, **k: called.setdefault("hit", True))
    assert grp.evict_ollama_models() is True
    assert called.get("hit") is True


def test_reclaim_routes_flags(monkeypatch):
    seen = []
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda: seen.append("comfy"))
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: seen.append("ollama"))
    grp.reclaim_gpu(evict_ollama=True, free_comfyui=True)
    assert set(seen) == {"comfy", "ollama"}
    seen.clear()
    grp.reclaim_gpu()  # defaults: reclaim nothing
    assert seen == []


# --- gpu_session front door --------------------------------------------------

def test_gpu_session_default_is_gate_passthrough(fresh_gate):
    with grp.gpu_session(JobKind.VIDEO_RENDER, "op1") as acquired:
        assert acquired is True
        # slot is genuinely held: a different id is refused
        ok, _ = fresh_gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "other")
        assert ok is False
    assert fresh_gate._gpu_holder is None      # released after the block


def test_gpu_session_busy_raises(fresh_gate):
    fresh_gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "held")
    with pytest.raises(GpuBusyError):
        with grp.gpu_session(JobKind.VIDEO_RENDER, "loser"):
            pass


def test_gpu_session_runs_reclaim_only_when_acquired(fresh_gate, monkeypatch):
    seen = []
    monkeypatch.setattr(grp, "reclaim_gpu", lambda **kw: seen.append(kw))
    with grp.gpu_session(JobKind.VIDEO_RENDER, "op", evict_ollama=True, free_comfyui=True):
        pass
    assert seen == [{"evict_ollama": True, "free_comfyui": True}]


def test_gpu_session_register_degrade_skips_reclaim(fresh_gate, monkeypatch):
    # Pre-hold the slot; on_busy='register' yields False (degraded) → never evict for
    # a job that didn't actually win the card.
    fresh_gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "held")
    seen = []
    monkeypatch.setattr(grp, "reclaim_gpu", lambda **kw: seen.append(kw))
    with grp.gpu_session(JobKind.VIDEO_RENDER, "deg", on_busy="register", evict_ollama=True) as acquired:
        assert acquired is False
    assert seen == []


def test_gpu_session_vram_budget_requests_and_releases(fresh_gate, monkeypatch):
    events = []
    monkeypatch.setattr(grp, "_orchestrator_request", lambda slot, mb, **kw: events.append(("req", slot, mb)))
    monkeypatch.setattr(grp, "_orchestrator_release", lambda slot: events.append(("rel", slot)))
    with grp.gpu_session(JobKind.VIDEO_RENDER, "op", vram_estimate_mb=8000, slot_id="video:mv"):
        pass
    assert events == [("req", "video:mv", 8000), ("rel", "video:mv")]


def test_gpu_session_releases_on_exception(fresh_gate, monkeypatch):
    released = []
    monkeypatch.setattr(grp, "_orchestrator_request", lambda slot, mb, **kw: None)
    monkeypatch.setattr(grp, "_orchestrator_release", lambda slot: released.append(slot))
    with pytest.raises(RuntimeError):
        with grp.gpu_session(JobKind.VIDEO_RENDER, "op", vram_estimate_mb=8000, slot_id="s"):
            raise RuntimeError("boom")
    assert fresh_gate._gpu_holder is None       # gate released despite the raise
    assert released == ["s"]                     # orchestrator released too


def test_gpu_session_ram_admit_uses_custom_weight(fresh_gate, monkeypatch):
    admitted = []
    monkeypatch.setattr(grp, "_load_admit_or_busy", lambda slot, ram_gb=2.0: admitted.append(ram_gb) or object())
    monkeypatch.setattr(grp, "_orchestrator_request", lambda slot, mb, **kw: None)
    monkeypatch.setattr(grp, "_orchestrator_release", lambda slot: None)
    with grp.gpu_session(
        JobKind.VIDEO_RENDER,
        "zimg",
        vram_estimate_mb=11000,
        ram_estimate_gb=32.0,
        slot_id="image_batch:test",
    ):
        pass
    assert admitted == [32.0]


# --- _ensure_fits_or_busy (LTX / 16GB margin overflow) -----------------------

def _patch_vram(monkeypatch, *, free_mb: int, total_mb: int = 16376):
    """Stub the coordinator probe used by _ensure_fits_or_busy (no real GPU)."""
    import backend.services.gpu_resource_coordinator as coord

    class _Fake:
        def get_available_vram(self):
            return {
                "success": True,
                "available_mb": free_mb,
                "total_mb": total_mb,
            }

    monkeypatch.setattr(coord, "get_gpu_coordinator", lambda: _Fake())


def test_ensure_fits_admits_ltx_14000_on_16gb(monkeypatch):
    """estimate 14000 + margin; free ~14500 (mostly idle, Comfy base resident) → admit."""
    _patch_vram(monkeypatch, free_mb=14500, total_mb=16376)
    grp._ensure_fits_or_busy(14000, "video:ltx", margin_mb=1024)


def test_ensure_fits_admits_margin_overflow_when_mostly_free(monkeypatch):
    """estimate 16000 fits card but +margin spills past total; mostly free → admit."""
    _patch_vram(monkeypatch, free_mb=14500, total_mb=16376)
    grp._ensure_fits_or_busy(16000, "video:cog", margin_mb=1024)


def test_ensure_fits_rejects_estimate_above_card_total(monkeypatch):
    """estimate alone above card total → capacity reject (margin rule does not apply)."""
    _patch_vram(monkeypatch, free_mb=14500, total_mb=16376)
    with pytest.raises(GpuBusyError, match="estimate exceeds GPU capacity"):
        grp._ensure_fits_or_busy(18000, "video:too-big", margin_mb=1024)


def test_ensure_fits_rejects_when_another_consumer_resident(monkeypatch):
    """estimate fits card but free is low (not mostly idle) → busy/resident refuse."""
    _patch_vram(monkeypatch, free_mb=5000, total_mb=16376)
    with pytest.raises(GpuBusyError, match="another model/render may be resident"):
        grp._ensure_fits_or_busy(14000, "video:ltx", margin_mb=1024)


# --- release order + typed refusals ------------------------------------------

def test_gpu_session_release_order_free_then_lease_then_gate(fresh_gate, monkeypatch):
    """Teardown of a cross-process video slot: ComfyUI /free, then the lease
    release, and only then the in-PID gate holder."""
    import requests
    import backend.services.gpu_resource_coordinator as coord

    events = []

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/free"):
            events.append(("free", fresh_gate.snapshot()["gpu_busy"]))
        return object()

    class _FakeCoord:
        def acquire_generic(self, label, lease_seconds=None):
            return {"success": True}

        def renew_generic(self, label, lease_seconds=None):
            return True

        def release_generic(self, label):
            events.append(("release_generic", fresh_gate.snapshot()["gpu_busy"]))
            return {"success": True}

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(coord, "get_gpu_coordinator", lambda: _FakeCoord())
    monkeypatch.setattr(grp, "_orchestrator_request", lambda slot, mb, **kw: None)
    monkeypatch.setattr(
        grp, "_orchestrator_release",
        lambda slot: events.append(("orch_release", fresh_gate.snapshot()["gpu_busy"])),
    )
    monkeypatch.setattr(grp, "_load_admit_or_busy", lambda slot, ram_gb=2.0: None)

    with grp.gpu_session(
        JobKind.VIDEO_RENDER, "rel",
        cross_process=True, vram_estimate_mb=8000,
        slot_id="video_render:rel", lease_seconds=60,
    ):
        assert fresh_gate.snapshot()["gpu_busy"] is True

    names = [name for name, _ in events]
    assert names.index("orch_release") < names.index("free") < names.index("release_generic")
    assert all(busy for _, busy in events), "gate must still be held during teardown"
    assert fresh_gate.snapshot()["gpu_busy"] is False


def test_gpu_session_fit_refusal_after_claim_skips_cooldown(fresh_gate, monkeypatch):
    """A require_fit refusal never touched the card: the gate is released with
    no post-release cooldown, while a completed session still starts one."""
    _patch_vram(monkeypatch, free_mb=5000, total_mb=16376)
    with pytest.raises(GpuBusyError, match="another model/render may be resident"):
        with grp.gpu_session(JobKind.VIDEO_RENDER, "fit", vram_estimate_mb=14000, require_fit=True):
            pass
    snap = fresh_gate.snapshot()
    assert snap["gpu_busy"] is False
    assert snap["gpu_cooldown_remaining_s"] == 0

    with grp.gpu_session(JobKind.VIDEO_RENDER, "ok"):
        pass
    assert fresh_gate.snapshot()["gpu_cooldown_remaining_s"] > 0


def test_gpu_session_capacity_refuses_before_reclaim(fresh_gate, monkeypatch):
    """estimate > card total → GpuCapacityError before any resident is evicted."""
    _patch_vram(monkeypatch, free_mb=100, total_mb=16376)
    seen = []
    monkeypatch.setattr(grp, "reclaim_gpu", lambda **kw: seen.append(kw))
    with pytest.raises(GpuCapacityError, match="estimate exceeds GPU capacity"):
        with grp.gpu_session(
            JobKind.VIDEO_RENDER, "big",
            evict_ollama=True, free_comfyui=True, vram_estimate_mb=20000, require_fit=True,
        ):
            pass
    assert seen == []
    assert fresh_gate.snapshot()["gpu_busy"] is False
    assert fresh_gate.snapshot()["gpu_cooldown_remaining_s"] == 0


def test_gpu_session_evicts_ollama_at_most_once(fresh_gate, monkeypatch):
    """One reclaim per session, followed by one settle before the fit probe."""
    _patch_vram(monkeypatch, free_mb=5000, total_mb=16376)
    evictions = []
    sleeps = []
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: evictions.append(1) or True)
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda: True)
    monkeypatch.setattr(grp.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(GpuBusyError, match="another model/render may be resident"):
        with grp.gpu_session(
            JobKind.VIDEO_RENDER, "once",
            evict_ollama=True, free_comfyui=True, vram_estimate_mb=14000, require_fit=True,
        ):
            pass
    assert evictions == [1]
    assert sleeps == [grp._RECLAIM_SETTLE_S]
