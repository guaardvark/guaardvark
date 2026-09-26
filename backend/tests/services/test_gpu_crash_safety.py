"""After a cancel, a crash or a restart, the next GPU job starts cleanly.

The real batch worker, gpu_session, JobOperationGate and the coordinator's
lease file run here. ComfyUI is the FakeComfyUI HTTP double
(tests/fixtures/workflow_contract.py); the other process holding the lease is a
real child process. Replaced: the card's VRAM probe (a 16 GB card, idle), the
RAM/load admission (it reads this machine's memory), and the orchestrator
booking (as the ComfyUI fixture already does).
"""
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from backend.services import gpu_resource_coordinator as grc
from backend.services import gpu_resource_policy as grp
from backend.services import job_operation_gate as jog
from backend.services.job_types import JobKind, batch_failure
from backend.tests.fixtures.workflow_contract import comfy  # noqa: F401 — fixture

REPO = Path(__file__).resolve().parents[3]
MODEL = "wan22-5b"
CARD = {"success": True, "total_mb": 16376, "available_mb": 15352, "free_mb": 15352}


@pytest.fixture
def lease(tmp_path, monkeypatch):
    """The real coordinator on a private lock file, with a fresh in-process gate."""
    coord = grc.get_gpu_coordinator()
    monkeypatch.setattr(coord, "LOCK_FILE", tmp_path / "pids" / "gpu_lock.json")
    coord.LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(coord, "get_available_vram", lambda: dict(CARD))
    monkeypatch.setattr(jog, "_GATE_SINGLETON", jog.JobOperationGate())
    monkeypatch.setattr(grp, "_load_admit_or_busy", lambda *a, **k: None)
    return coord


def _holder(coord):
    info = coord._read_lock_file()
    return info.owner if info else None


def _gate():
    return jog.get_gate().snapshot()


# ── another process holding the lease ─────────────────────────────────────────

_HOLDER = textwrap.dedent("""
    import json, sys, threading, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from backend.services.gpu_resource_coordinator import GPUResourceCoordinator
    c = object.__new__(GPUResourceCoordinator)   # not the singleton: no boot cleanup
    c._internal_lock = threading.RLock()
    c._initialized = True
    c.LOCK_FILE = Path(sys.argv[2])
    print(json.dumps(c.acquire_generic(sys.argv[3], lease_seconds=600)), flush=True)
    time.sleep(600)
""")


@pytest.fixture
def other_process(lease):
    procs = []

    def start(label="video_render:other"):
        proc = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, str(REPO), str(lease.LOCK_FILE), label],
            stdout=subprocess.PIPE, text=True,
        )
        procs.append(proc)
        reply = json.loads(proc.stdout.readline())
        assert reply["success"], reply
        return proc

    yield start
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_second_process_is_refused_and_leaves_no_claim(lease, other_process):
    other_process()
    with pytest.raises(jog.GpuBusyError, match="another process"):
        with grp.gpu_session(JobKind.VIDEO_RENDER, "mine", cross_process=True):
            pytest.fail("entered while another process holds the lease")
    snap = _gate()
    assert snap["gpu_holder"] is None
    assert snap["gpu_cooldown_remaining_s"] == 0, "a refused claim never touched the card"
    assert _holder(lease) == "video_render:other"


def test_the_lease_of_a_killed_process_is_taken_over(lease, other_process):
    proc = other_process()
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    with grp.gpu_session(JobKind.VIDEO_RENDER, "mine", cross_process=True, slot_id="video_render:mine"):
        assert _holder(lease) == "video_render:mine"
    assert _holder(lease) is None


# ── a backend restart with a lease file present ──────────────────────────────

def _write_lock(coord, *, pid, owner="video_render:batch_old", expires_in=3600, started=None):
    from datetime import datetime, timedelta
    meta = {"kind": "generic"}
    if started is not None:
        meta["pid_started_at"] = started
    coord.LOCK_FILE.write_text(json.dumps({
        "owner": owner, "acquired_at": datetime.now().isoformat(), "pid": pid,
        "lease_expires_at": (datetime.now() + timedelta(seconds=expires_in)).isoformat(),
        "metadata": meta,
    }))


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_restart_clears_the_lease_of_a_dead_process(lease):
    _write_lock(lease, pid=_dead_pid())
    lease._cleanup_stale_lock()   # what the coordinator runs when a process builds it
    assert _holder(lease) is None


def test_restart_clears_an_expired_lease_of_a_live_process(lease):
    _write_lock(lease, pid=os.getppid(), expires_in=-5)
    assert lease.acquire_generic("video_render:new")["success"]


@pytest.fixture
def unrelated_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    yield proc
    proc.kill()
    proc.wait()


def test_a_lease_whose_pid_now_belongs_to_another_process_is_stale(lease, unrelated_process):
    # After a reboot the recorded PID can be reused by an unrelated process.
    # The lease recorded when its holder started; a live PID that started at
    # another time is not the holder.
    import psutil
    started = psutil.Process(unrelated_process.pid).create_time()
    _write_lock(lease, pid=unrelated_process.pid, started=started - 3600)
    lease._cleanup_stale_lock()
    assert _holder(lease) is None
    _write_lock(lease, pid=unrelated_process.pid, started=started - 3600)
    assert lease.acquire_generic("video_render:new")["success"]


def test_a_lease_whose_holder_is_still_running_is_kept(lease, unrelated_process):
    import psutil
    started = psutil.Process(unrelated_process.pid).create_time()
    _write_lock(lease, pid=unrelated_process.pid, started=started)
    lease._cleanup_stale_lock()
    assert _holder(lease) == "video_render:batch_old"
    assert not lease.acquire_generic("video_render:new")["success"]


def test_a_new_lease_records_when_its_holder_started(lease):
    import psutil
    assert lease.acquire_generic("video_render:new")["success"]
    recorded = lease._read_lock_file().metadata["pid_started_at"]
    assert recorded == pytest.approx(psutil.Process(os.getpid()).create_time())
    assert lease.release_generic("video_render:new")["success"]


# ── the batch worker: cancel and crash ───────────────────────────────────────

def _generator(comfy, tmp_path, monkeypatch, **params):
    import queue

    from backend.services.batch_video_generator import BatchVideoGenerator, BatchVideoItem

    gen = BatchVideoGenerator.__new__(BatchVideoGenerator)
    gen.base_output_dir = tmp_path / "Videos"
    gen.active_batches, gen.cancel_events, gen.queue_order = {}, {}, []
    gen.batch_lock = threading.Lock()
    gen.batch_queue = queue.Queue()
    gen.video_generator = comfy.gen
    gen._running_batch_id = None
    for key, value in {"model": MODEL, "width": 832, "height": 480, "duration_frames": 49,
                       "interpolation_multiplier": 1, "enhance_prompt": False}.items():
        params.setdefault(key, value)
    status = gen._start_batch(batch_id="b1", items=[BatchVideoItem(id="i1", prompt="a fox")], **params)
    request, _ = gen.batch_queue.get_nowait()
    gen.cancel_events.setdefault("b1", threading.Event())
    monkeypatch.chdir(tmp_path)
    return gen, request, status


def _run(gen, request, status):
    """The queue worker's call, on its own thread like the real worker."""
    from flask import Flask

    def work():
        with Flask(__name__).app_context():
            gen._running_batch_id = request.batch_id
            try:
                gen._run_batch(request, status)
            finally:
                gen._running_batch_id = None

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def _until(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def training_holds_the_gate(lease):
    """Another job in this process holds the gate from a live thread."""
    gate = jog.get_gate()
    claimed, done = threading.Event(), threading.Event()

    def hold():
        assert gate.try_claim_gpu_exclusive(JobKind.TRAINING, "train-1")[0]
        claimed.set()
        done.wait(60)
        gate.release_gpu_exclusive(JobKind.TRAINING, "train-1", cooldown=False)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    claimed.wait(5)
    yield
    done.set()
    thread.join(5)


def test_cancel_while_waiting_for_the_gate_ends_the_wait(comfy, tmp_path, monkeypatch, lease, training_holds_the_gate):
    gen, request, status = _generator(comfy, tmp_path, monkeypatch)
    thread = _run(gen, request, status)
    assert _until(lambda: getattr(status, "stage", None) == "gpu_wait")
    time.sleep(0.3)
    started = time.time()
    assert gen.cancel_batch("b1")
    thread.join(15)
    assert not thread.is_alive(), "the batch kept waiting for the gate after its cancel"
    assert time.time() - started < 10
    assert status.status == "cancelled" and batch_failure(status)["kind"] == "cancelled"
    assert _gate()["gpu_holder"]["native_id"] == "train-1"
    assert _holder(lease) is None
    assert comfy.fake.prompts == []


def test_cancel_while_another_process_holds_the_lease(comfy, tmp_path, monkeypatch, lease, other_process):
    other_process()
    gen, request, status = _generator(comfy, tmp_path, monkeypatch)
    thread = _run(gen, request, status)
    assert _until(lambda: getattr(status, "stage", None) == "gpu_wait")
    time.sleep(0.3)
    assert gen.cancel_batch("b1")
    thread.join(15)
    assert not thread.is_alive()
    assert status.status == "cancelled" and batch_failure(status)["kind"] == "cancelled"
    assert _gate()["gpu_holder"] is None
    assert _holder(lease) == "video_render:other"


def test_cancel_during_the_render(comfy, tmp_path, monkeypatch, lease):
    comfy.fake.history_entry = {"status": {"status_str": "running", "completed": False, "messages": []}}
    comfy.fake.running = True
    gen, request, status = _generator(comfy, tmp_path, monkeypatch)
    thread = _run(gen, request, status)
    assert _until(lambda: comfy.fake.prompts, timeout=20)
    assert _holder(lease) == "video_render:batch_b1"
    assert gen.cancel_batch("b1")
    thread.join(30)
    assert not thread.is_alive()
    assert comfy.fake.interrupts, "ComfyUI was not told to stop"
    assert status.status == "cancelled" and batch_failure(status)["kind"] == "cancelled"
    assert _holder(lease) is None
    assert _gate()["gpu_holder"] is None
    saved = json.loads((tmp_path / "Videos" / "b1" / "batch_metadata.json").read_text())
    assert saved["status"] == "cancelled"


def test_comfyui_exits_mid_render(comfy, tmp_path, monkeypatch, lease):
    from backend.services import comfyui_video_generator as cvg
    monkeypatch.setattr(cvg, "DEAD_PROBE_LIMIT", 1)
    comfy.fake.die_after_prompt = True
    gen, request, status = _generator(comfy, tmp_path, monkeypatch)
    thread = _run(gen, request, status)
    thread.join(60)
    assert not thread.is_alive()
    assert status.status == "error"
    assert status.results[0].error_kind == "comfyui_down"
    assert batch_failure(status)["kind"] == "comfyui_down"
    assert _holder(lease) is None
    assert _gate()["gpu_holder"] is None


def test_cancel_all_leaves_another_jobs_claim_alone(lease):
    """A shutdown cancel of video batches must not release the gate held by a
    different job (a Studio image batch also claims VIDEO_RENDER)."""
    from backend.services.batch_video_generator import BatchVideoGenerator

    gen = BatchVideoGenerator.__new__(BatchVideoGenerator)
    gen.active_batches, gen.cancel_events, gen.queue_order = {}, {}, []
    gen.batch_lock = threading.Lock()
    gen.video_generator = SimpleNamespace(interrupt=lambda *a, **k: True)
    gen._running_batch_id = None
    gate = jog.get_gate()
    claimed, done = threading.Event(), threading.Event()

    def hold():
        gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "image_batch_7")
        claimed.set()
        done.wait(10)

    threading.Thread(target=hold, daemon=True).start()
    claimed.wait(5)
    try:
        assert gen.cancel_all_active() == []
        assert _gate()["gpu_holder"]["native_id"] == "image_batch_7"
    finally:
        done.set()


# ── the plugin manager's view after ComfyUI dies ─────────────────────────────

@pytest.fixture
def comfy_plugin(tmp_path, monkeypatch):
    from backend.plugins import plugin_manager as pm_module
    from backend.plugins.plugin_manager import PluginManager
    from backend.plugins.plugin_registry import PluginRegistry
    import backend.extensions as _ext

    monkeypatch.delenv("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", raising=False)
    monkeypatch.setattr(_ext, "plugin_dirs", lambda *_a, **_k: [])
    root = tmp_path / "plugins"
    (root / "fakecomfy" / "scripts").mkdir(parents=True)
    (root / "fakecomfy" / "plugin.json").write_text(json.dumps({
        "id": "fakecomfy", "name": "Fake Comfy", "version": "1.0.0", "type": "service",
        "port": 18188, "config": {"default_enabled": True, "service_url": "http://127.0.0.1:18188", "timeout": 5},
        "endpoints": {"health": "/"},
    }))
    service = SimpleNamespace(up=True)

    def get(url, timeout=None, **kw):
        if not service.up:
            raise requests.exceptions.ConnectionError("refused")
        return SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})

    monkeypatch.setattr(pm_module, "requests", SimpleNamespace(
        get=get, exceptions=requests.exceptions, RequestException=requests.RequestException,
    ))
    manager = PluginManager(registry=PluginRegistry(plugins_dir=root))
    return manager, service


def test_health_check_records_a_crashed_service_as_stopped(comfy_plugin):
    from backend.plugins.plugin_manager import PluginStatus

    manager, service = comfy_plugin
    assert manager.get_status("fakecomfy") == PluginStatus.RUNNING
    service.up = False
    assert manager.health_check("fakecomfy")["status"] == "stopped"
    assert manager.get_status("fakecomfy") == PluginStatus.STOPPED
    service.up = True
    assert manager.health_check("fakecomfy")["status"] == "ok"
    assert manager.get_status("fakecomfy") == PluginStatus.RUNNING
