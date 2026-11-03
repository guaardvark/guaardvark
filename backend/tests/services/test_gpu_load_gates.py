"""Tests for the GPU/load admission gates (D1/D2/D3/F2).

NO real GPU, NO model loads, NO rendering — everything uses a fresh in-memory
JobOperationGate, a fresh GlobalLoadGate, and monkeypatched psutil/nvidia-smi.
"""
from __future__ import annotations

import pytest


# ----------------------------------------------------------------------------
# D2 — gpu_exclusive contextmanager + GPU_EXCLUSIVE_KINDS
# ----------------------------------------------------------------------------

def _fresh_gate():
    from backend.services.job_operation_gate import JobOperationGate
    return JobOperationGate()


def test_lora_train_in_gpu_exclusive_kinds():
    from backend.services.job_operation_gate import GPU_EXCLUSIVE_KINDS
    from backend.services.job_types import JobKind
    assert JobKind.LORA_TRAIN in GPU_EXCLUSIVE_KINDS
    assert JobKind.TRAINING in GPU_EXCLUSIVE_KINDS
    assert JobKind.VIDEO_RENDER in GPU_EXCLUSIVE_KINDS


def test_gpu_exclusive_acquires_and_releases():
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "r1") as acquired:
        assert acquired is True
        assert gate.snapshot()["gpu_busy"] is True
    assert gate.snapshot()["gpu_busy"] is False


def test_gpu_exclusive_second_claim_raises_gpu_busy():
    from backend.services.job_operation_gate import GpuBusyError
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "r1"):
        with pytest.raises(GpuBusyError):
            with gate.gpu_exclusive(JobKind.TRAINING, "t1"):
                pass  # pragma: no cover — must never enter


def test_gpu_exclusive_releases_on_exception():
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with pytest.raises(RuntimeError, match="boom"):
        with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "r1"):
            raise RuntimeError("boom")
    # Slot freed despite the exception.
    assert gate.snapshot()["gpu_busy"] is False


def test_gpu_exclusive_on_busy_register_degrades():
    """on_busy='register' runs WITHOUT real exclusivity (degraded visibility)."""
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "holder"):
        with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "second", on_busy="register") as acq:
            assert acq is False  # degraded — did not get exclusivity
            snap = gate.snapshot()
            # The original holder still owns the exclusive slot.
            assert snap["gpu_holder"]["native_id"] == "holder"
            # But the degraded job is visible in in_progress.
            assert "second" in snap["in_progress"].get("video_render", [])


def test_gpu_exclusive_idempotent_release_after_register():
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "holder"):
        with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "second", on_busy="register"):
            pass
        # second's register flag dropped on exit; holder still held.
        snap = gate.snapshot()
        assert "second" not in snap["in_progress"].get("video_render", [])
        assert snap["gpu_holder"]["native_id"] == "holder"


def test_gpu_exclusive_same_holder_idempotent():
    """Re-claiming the exact same kind+id is idempotent (already-holding)."""
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    ok1, _ = gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "r1")
    ok2, reason = gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "r1")
    assert ok1 and ok2
    assert "Already holding" in reason
    # A DIFFERENT id of the same kind must be refused (regression: the old
    # worktree bug compared native_id to itself and wrongly granted it).
    ok3, reason3 = gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "r2")
    assert ok3 is False
    assert "held by" in reason3


# ----------------------------------------------------------------------------
# D2 — a representative surface: run_storyboard_artist acquires + releases
# (storyboard image-gen is the simplest GPU surface to drive — it takes an
# injectable image_generator, so no model load / ComfyUI / DB writes needed).
# ----------------------------------------------------------------------------

def test_run_storyboard_artist_uses_gpu_gate(monkeypatch):
    """run_storyboard_artist must claim the GPU exclusive slot around the
    generate loop and release it afterwards. The injected image_generator
    asserts the gate is held mid-generation; we assert it's free after."""
    import backend.tasks.production_swarm_tasks as pst
    from backend.services.job_operation_gate import get_gate
    from backend.services.job_types import JobKind

    gate = get_gate()
    gate.release_gpu_exclusive(JobKind.VIDEO_RENDER, "storyboard_555")
    assert gate.snapshot()["gpu_busy"] is False

    seen = {}

    class _Ctx:
        class production:
            id = 555
            name = "SB Prod"

    from contextlib import contextmanager

    @contextmanager
    def _fake_agent_run(prod_id, *, agent_name, expected_stage, next_agent):
        yield _Ctx()

    monkeypatch.setattr(pst, "_agent_run", _fake_agent_run)

    class _Shot:
        scene_number = 1
        shot_number = 1
        description = "a shot"
        shot_subjects = []
        storyboard_image_path = None

    class _Query:
        def filter_by(self, **kw):
            return self
        def all(self):
            return [_Shot()]

    # Replace the whole ProductionShot NAME in the module with a fake class
    # carrying a plain `query` attr — avoids touching SQLAlchemy's real query
    # descriptor (whose read requires a Flask app context).
    class _FakeProductionShot:
        query = _Query()

    monkeypatch.setattr(pst, "ProductionShot", _FakeProductionShot)
    # Fake `db` so db.session.commit() doesn't require a Flask app context.
    class _FakeSession:
        def commit(self):
            pass
    monkeypatch.setattr(pst, "db", type("DB", (), {"session": _FakeSession()})())
    monkeypatch.setattr(pst, "_storyboard_path", lambda *a, **k: "/tmp/sb.png")

    class _Gen:
        def generate_image(self, **kw):
            seen["held_during_gen"] = gate.snapshot()["gpu_busy"]
            return "/tmp/sb.png"

    pst.run_storyboard_artist(555, image_generator=_Gen())

    assert seen.get("held_during_gen") is True, "gate must be held during generation"
    assert gate.snapshot()["gpu_busy"] is False, "gate must be released after generation"


# ----------------------------------------------------------------------------
# D3 — GlobalLoadGate
# ----------------------------------------------------------------------------

class _VM:
    def __init__(self, avail_gb):
        self.available = int(avail_gb * (1024 ** 3))


class _SW:
    def __init__(self, used_gb):
        self.used = int(used_gb * (1024 ** 3))


def _patch_load(monkeypatch, *, ram_avail_gb, swap_used_gb=0.0, load1=1.0, vram_free_gb=None,
                cpu_pressure=None, threads=16):
    import backend.services.system_load_gate as slg
    monkeypatch.setattr(slg.psutil, "virtual_memory", lambda: _VM(ram_avail_gb))
    monkeypatch.setattr(slg.psutil, "swap_memory", lambda: _SW(swap_used_gb))
    monkeypatch.setattr(slg.os, "getloadavg", lambda: (load1, load1, load1))
    monkeypatch.setattr(slg, "_read_vram_free_gb", lambda: vram_free_gb)
    # None = no PSI on this "machine", so the load-average check applies.
    monkeypatch.setattr(slg, "_read_cpu_pressure", lambda: cpu_pressure)
    monkeypatch.setattr(slg.os, "sched_getaffinity", lambda _pid: set(range(threads)), raising=False)


def test_load_gate_admits_when_healthy(monkeypatch):
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight
    _patch_load(monkeypatch, ram_avail_gb=40.0)
    g = GlobalLoadGate()
    g.admit(JobWeight(ram_gb=4.0), timeout=0.0)  # must not raise
    g.release(JobWeight(ram_gb=4.0))


def test_load_gate_blocks_when_ram_low(monkeypatch):
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight, LoadGateTimeout
    _patch_load(monkeypatch, ram_avail_gb=5.0)  # below 6 GB hard floor
    g = GlobalLoadGate()
    with pytest.raises(LoadGateTimeout):
        g.admit(JobWeight(ram_gb=1.0), timeout=0.0)


def test_load_gate_blocks_on_swap(monkeypatch):
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight, LoadGateTimeout
    _patch_load(monkeypatch, ram_avail_gb=40.0, swap_used_gb=10.0)  # >8 GB swap
    g = GlobalLoadGate()
    with pytest.raises(LoadGateTimeout):
        g.admit(JobWeight(ram_gb=1.0), timeout=0.0)


def _blocked(monkeypatch, **load):
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight
    _patch_load(monkeypatch, ram_avail_gb=40.0, **load)
    g = GlobalLoadGate()
    return g._blocking_reason(JobWeight(ram_gb=4.0), g.read())


def test_load_gate_disk_waits_do_not_block_with_psi(monkeypatch):
    # Tasks waiting on a disk raise the load average, not CPU pressure.
    assert _blocked(monkeypatch, load1=30.0, cpu_pressure=(0.0, 0.0)) is None


def test_load_gate_blocks_on_sustained_cpu_pressure(monkeypatch):
    reason = _blocked(monkeypatch, load1=20.0, cpu_pressure=(17.0, 16.0))
    assert reason and "CPU busy" in reason


def test_load_gate_cpu_pressure_alone_never_blocks_under_the_load_limit(monkeypatch):
    # PSI is per-thread; on its own it would refuse small machines the old
    # load-average limit admitted. It only narrows that limit, never adds to it.
    assert _blocked(monkeypatch, load1=10.0, cpu_pressure=(60.0, 50.0), threads=8) is None


def test_load_gate_short_cpu_burst_does_not_block(monkeypatch):
    # Contended now but not over the last minute: a burst, not an overload.
    assert _blocked(monkeypatch, load1=20.0, cpu_pressure=(60.0, 5.0)) is None


def test_load_gate_clears_once_contention_ends(monkeypatch):
    # The load average and the minute figure still remember it; nothing waits now.
    assert _blocked(monkeypatch, load1=20.0, cpu_pressure=(3.0, 20.0)) is None


def test_load_gate_without_psi_uses_loadavg_scaled_by_threads(monkeypatch):
    assert "loadavg high" in (_blocked(monkeypatch, load1=20.0, threads=16) or "")
    assert _blocked(monkeypatch, load1=20.0, threads=32) is None       # limit 36
    assert "loadavg high" in (_blocked(monkeypatch, load1=19.0, threads=4) or "")  # never below 18


def test_read_cpu_pressure_parses_psi(tmp_path, monkeypatch):
    import backend.services.system_load_gate as slg
    psi = tmp_path / "cpu"
    psi.write_text(
        "some avg10=16.75 avg60=14.05 avg300=5.00 total=123\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    )
    monkeypatch.setattr(slg, "_CPU_PRESSURE_PATH", str(psi))
    assert slg._read_cpu_pressure() == (16.75, 14.05)
    monkeypatch.setattr(slg, "_CPU_PRESSURE_PATH", str(tmp_path / "missing"))
    assert slg._read_cpu_pressure() is None


def test_load_refusal_names_the_system_not_vram(monkeypatch):
    # A load-gate refusal is a SystemLoadBusyError (still a GpuBusyError, so every
    # retry loop keeps working) and the queue panel says what it waits for.
    from backend.services import gpu_resource_policy as grp
    from backend.services.job_operation_gate import (
        GpuBusyError, SystemLoadBusyError, gpu_wait_message,
    )
    _patch_load(monkeypatch, ram_avail_gb=40.0, load1=25.0, cpu_pressure=(50.0, 40.0))
    import backend.services.system_load_gate as slg
    monkeypatch.setattr(slg, "_LOAD_GATE_SINGLETON", None)
    with pytest.raises(SystemLoadBusyError) as caught:
        grp._load_admit_or_busy("image_batch:test", ram_gb=2.0)
    assert isinstance(caught.value, GpuBusyError)
    assert caught.value.detail.startswith("CPU busy")
    assert gpu_wait_message(caught.value, 14000, 12000).startswith("Waiting for the system: CPU busy")
    assert gpu_wait_message(GpuBusyError("held"), 4096, 12288) == "Waiting for VRAM — 4.0GB free, need ~12.0GB"


def test_wait_message_names_the_job_holding_the_gpu():
    from backend.services.job_operation_gate import (
        GpuBusyError, JobOperationGate, gpu_holder_label, gpu_wait_message,
    )
    from backend.services.job_types import JobKind

    gate = JobOperationGate()
    assert gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "VideoBatch_10-07-2026_001")[0]
    ok, reason = gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "ImageBatch_10-07-2026_002")
    assert not ok
    # The gate's own refusal text is what the batch loops catch and pass on.
    assert gpu_wait_message(GpuBusyError(reason), 2662, 11981) == (
        "Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free"
    )
    assert gpu_holder_label(GpuBusyError("GPU is held by video_render:ImageBatch_x — wait")) == "Image Gen"
    assert gpu_holder_label(GpuBusyError("GPU is held by video_render:editor_42 — wait")) == "a video render"
    assert gpu_holder_label(GpuBusyError("GPU is held by lora_train:7 — wait")) == "LoRA training"
    # video_render is the general GPU kind: the id says what is rendering, and an
    # id with no known prefix is not called a video.
    assert gpu_holder_label(GpuBusyError("GPU is held by video_render:stills_chat_ab12cd34 — wait")) == "a picture"
    assert gpu_holder_label(GpuBusyError("GPU is held by video_render:video:music3:9 — wait")) == "a song"
    assert gpu_holder_label(GpuBusyError("GPU is held by video_render:chat_edit_ab12cd34 — wait")) == "a picture edit"
    assert gpu_holder_label(GpuBusyError("GPU is held by video_render:whatever — wait")) == "another job"
    assert gpu_holder_label(GpuBusyError("GPU is held by another process (busy).")) == "another process"
    assert gpu_holder_label(None) is None
    assert gpu_wait_message(None, 1024, 2048) == "Waiting for VRAM — 1.0GB free, need ~2.0GB"


def test_load_gate_reserved_ram_accounting(monkeypatch):
    """Admitting one job reserves its RAM; a second admit sees less headroom."""
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight, LoadGateTimeout
    # 10 GB available; floor is 6 GB. First 3 GB job admits (10-3=7 >= 6).
    _patch_load(monkeypatch, ram_avail_gb=10.0)
    g = GlobalLoadGate()
    g.admit(JobWeight(ram_gb=3.0), timeout=0.0)
    reading = g.read()
    assert reading.reserved_ram_gb == pytest.approx(3.0)
    # A second 3 GB job: effective avail = 10 - 3(reserved) = 7; 7 - 3 = 4 < 6 floor -> blocked.
    with pytest.raises(LoadGateTimeout):
        g.admit(JobWeight(ram_gb=3.0), timeout=0.0)
    # After releasing the first, the second fits again.
    g.release(JobWeight(ram_gb=3.0))
    g.admit(JobWeight(ram_gb=3.0), timeout=0.0)
    g.release(JobWeight(ram_gb=3.0))


def test_load_gate_vram_unknown_does_not_block(monkeypatch):
    """nvidia-smi missing -> VRAM unknown -> must NOT block on VRAM."""
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight
    _patch_load(monkeypatch, ram_avail_gb=40.0, vram_free_gb=None)
    g = GlobalLoadGate()
    g.admit(JobWeight(ram_gb=1.0, vram_gb=12.0), timeout=0.0)  # would block if VRAM known-low
    g.release(JobWeight(ram_gb=1.0, vram_gb=12.0))


def test_load_gate_vram_known_low_blocks(monkeypatch):
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight, LoadGateTimeout
    _patch_load(monkeypatch, ram_avail_gb=40.0, vram_free_gb=1.0)  # below 1.5 floor
    g = GlobalLoadGate()
    with pytest.raises(LoadGateTimeout):
        g.admit(JobWeight(ram_gb=1.0, vram_gb=0.0), timeout=0.0)


def test_read_vram_free_gb_missing_nvidia_smi(monkeypatch):
    """Direct test that a missing nvidia-smi degrades to None, not an exception."""
    import backend.services.system_load_gate as slg

    def _raise(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(slg.subprocess, "run", _raise)
    assert slg._read_vram_free_gb() is None


def test_system_load_admit_contextmanager_releases(monkeypatch):
    from backend.services.system_load_gate import system_load_admit, JobWeight, get_load_gate
    _patch_load(monkeypatch, ram_avail_gb=40.0)
    g = get_load_gate()
    before = g.read().reserved_ram_gb
    with system_load_admit(JobWeight(ram_gb=2.0), timeout=0.0):
        assert g.read().reserved_ram_gb == pytest.approx(before + 2.0)
    assert g.read().reserved_ram_gb == pytest.approx(before)


# ----------------------------------------------------------------------------
# D3 — swarm orchestrator freeze-guard
# ----------------------------------------------------------------------------

def test_swarm_freeze_guard_blocks_on_low_ram(monkeypatch):
    import plugins.swarm.service.orchestrator as orch

    class _VMlow:
        available = int(5.0 * (1024 ** 3))  # below 6 GB floor

    class _SWok:
        used = 0

    monkeypatch.setattr(orch.psutil, "virtual_memory", lambda: _VMlow())
    monkeypatch.setattr(orch.psutil, "swap_memory", lambda: _SWok())
    reason = orch._spawn_freeze_guard_block_reason()
    assert reason is not None and "RAM" in reason


def test_swarm_freeze_guard_blocks_on_swap(monkeypatch):
    import plugins.swarm.service.orchestrator as orch

    class _VMok:
        available = int(40.0 * (1024 ** 3))

    class _SWhigh:
        used = int(2.0 * (1024 ** 3))

    monkeypatch.setattr(orch.psutil, "virtual_memory", lambda: _VMok())
    monkeypatch.setattr(orch.psutil, "swap_memory", lambda: _SWhigh())
    reason = orch._spawn_freeze_guard_block_reason()
    assert reason is not None and "swap" in reason


def test_swarm_freeze_guard_allows_when_healthy(monkeypatch):
    import plugins.swarm.service.orchestrator as orch

    class _VMok:
        available = int(40.0 * (1024 ** 3))

    class _SWok:
        used = 0

    monkeypatch.setattr(orch.psutil, "virtual_memory", lambda: _VMok())
    monkeypatch.setattr(orch.psutil, "swap_memory", lambda: _SWok())
    assert orch._spawn_freeze_guard_block_reason() is None


# ----------------------------------------------------------------------------
# F2 — casting orphan: run_casting_director is never dispatched; casting still
# advances through production_api.confirm_casting.
# ----------------------------------------------------------------------------

def test_casting_stage_never_auto_dispatched():
    """STAGE_TO_AGENT['casting'] is None and the screenwriter does not chain to
    casting — so run_casting_director never fires automatically (no unprompted
    GPU training)."""
    from backend.services.production_service import STAGE_TO_AGENT
    assert STAGE_TO_AGENT["casting"] is None


def test_casting_director_action_is_advisory_only():
    """The CastingAction.action field exists for the recommendation message but
    run_casting_director must NOT apply it (only voice_id). Guard against a
    regression that starts wiring `action`."""
    import inspect
    import backend.tasks.production_swarm_tasks as pst
    src = inspect.getsource(pst.run_casting_director)
    # voice_id IS applied:
    assert "subj.voice_id = action.voice_id" in src
    # action is NOT used to dispatch GPU training from within the casting
    # director (only the cheap voice_id assignment is applied).
    assert "train_lora" not in src
    assert "_dispatch_lora_train" not in src
    # The applied lines only touch voice_id / ProductionSubject linkage — never
    # action.action or existing_lora_id.
    assert "action.action" not in src
    assert "existing_lora_id" not in src


def test_no_casting_director_send_task_anywhere():
    """No code path enqueues production.run_casting_director."""
    import inspect
    import backend.tasks.production_swarm_tasks as pst
    full = inspect.getsource(pst)
    assert 'send_task(f"production.run_{next_agent}"' in full  # generic chain exists
    # but casting is never a next_agent (screenwriter -> next_agent=None).
    assert 'next_agent="casting' not in full
    assert "next_agent='casting" not in full


# ----------------------------------------------------------------------------
# VRAM probe: nvidia-smi first, rocm-smi as the fallback
# ----------------------------------------------------------------------------

# Integrated GPU listed first, and a warning line before the JSON, as rocm-smi prints it.
_ROCM_TWO_CARDS = (
    "WARNING: AMD GPU device(s) is/are in a low-power state.\n\n"
    '{"card0": {"VRAM Total Memory (B)": "3221225472", "VRAM Total Used Memory (B)": "778117120"}, '
    '"card1": {"VRAM Total Memory (B)": "25753026560", "VRAM Total Used Memory (B)": "839303168"}}\n'
)


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _fake_run(tools):
    """subprocess.run stand-in: a tool that is not in `tools` raises FileNotFoundError."""
    def run(cmd, *args, **kwargs):
        if cmd[0] not in tools:
            raise FileNotFoundError(cmd[0])
        return tools[cmd[0]]
    return run


def test_read_vram_free_gb_prefers_nvidia_smi(monkeypatch):
    import backend.services.system_load_gate as slg
    monkeypatch.setattr(slg.subprocess, "run", _fake_run({
        "nvidia-smi": _Proc("8192\n"),
        "rocm-smi": _Proc(_ROCM_TWO_CARDS),
    }))
    assert slg._read_vram_free_gb() == pytest.approx(8.0)


def test_read_vram_free_gb_falls_back_to_rocm_smi_largest_card(monkeypatch):
    import backend.services.system_load_gate as slg
    monkeypatch.setattr(slg.subprocess, "run", _fake_run({"rocm-smi": _Proc(_ROCM_TWO_CARDS)}))
    assert slg._read_vram_free_gb() == pytest.approx((25753026560 - 839303168) / slg.GB)


def test_read_vram_free_gb_uses_rocm_smi_after_failing_nvidia_smi(monkeypatch):
    import backend.services.system_load_gate as slg
    monkeypatch.setattr(slg.subprocess, "run", _fake_run({
        "nvidia-smi": _Proc("", returncode=9),
        "rocm-smi": _Proc(_ROCM_TWO_CARDS),
    }))
    assert slg._read_vram_free_gb() == pytest.approx((25753026560 - 839303168) / slg.GB)


def test_read_vram_free_gb_none_when_neither_tool_answers(monkeypatch):
    import backend.services.system_load_gate as slg
    monkeypatch.setattr(slg.subprocess, "run", _fake_run({}))
    assert slg._read_vram_free_gb() is None


@pytest.mark.parametrize("out", ["", "no json here", "{broken", '{"card0": {"foo": "bar"}}'])
def test_read_vram_free_gb_rocm_smi_garbage_is_unknown(monkeypatch, out):
    import backend.services.system_load_gate as slg
    monkeypatch.setattr(slg.subprocess, "run", _fake_run({"rocm-smi": _Proc(out)}))
    assert slg._read_vram_free_gb() is None
