"""An agent running a local Ollama model holds that model on the backend's GPU
orchestrator while it runs, and the hold is released when the agent exits."""

import subprocess

import pytest


def _config():
    from service.config import BackendConfig, SwarmConfig

    cfg = SwarmConfig()
    cfg.backends = {
        "claude": BackendConfig(name="claude", command="claude", requires_internet=True, priority=1),
        "cline": BackendConfig(name="cline", command="cline", model="ollama/gemma4:e4b",
                               requires_internet=False, priority=2),
    }
    return cfg


class FakeGpu:
    """Stands in for GpuHoldClient and records the calls."""

    def __init__(self, grant=True):
        self.grant = grant
        self.calls = []

    def hold(self, model):
        self.calls.append(("hold", model))
        return f"ollama:{model}" if self.grant else None

    def release(self, slot_id):
        self.calls.append(("release", slot_id))


class FakeBackend:
    """An agent backend whose agent finishes or crashes when told to."""

    def __init__(self, name, fail_spawn=False):
        self.name = name
        self.fail_spawn = fail_spawn
        self.next_status = None
        self.killed = []

    def spawn(self, worktree_path, task, config):
        from service.agent_backends.base_backend import AgentProcess
        from service.models import AgentStatus

        if self.fail_spawn:
            raise RuntimeError("cline would not start")
        return AgentProcess(task_id=task.id, backend_name=self.name, pid=None,
                            worktree_path=worktree_path, status=AgentStatus.RUNNING)

    def check_status(self, process):
        from service.models import AgentStatus

        process.status = self.next_status or AgentStatus.RUNNING
        return process.status

    def estimate_cost(self, process):
        return 0, 0.0

    def get_logs(self, process, lines=50):
        return ""

    def kill(self, process):
        self.killed.append(process.task_id)
        return True


class FakeWorktrees:
    def __init__(self, root):
        self.root = root

    def create(self, task_id):
        from service.worktree_manager import WorktreeInfo

        path = self.root / task_id
        path.mkdir(exist_ok=True)
        return WorktreeInfo(task_id=task_id, swarm_id="s1", branch_name=f"swarm/s1/{task_id}",
                            worktree_path=str(path), created=True)


@pytest.fixture
def no_git(monkeypatch):
    """The orchestrator's git probes fail fast instead of running git."""
    def refuse(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0] if args else "git")

    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.fixture
def both_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd, *a, **k: f"/usr/bin/{cmd}")


def _orchestrator(tmp_path, gpu, backend_name="cline", fail_spawn=False):
    from service.models import SwarmResult, SwarmTask
    from service.orchestrator import SwarmOrchestrator

    orch = SwarmOrchestrator(tmp_path, _config())
    orch._emit_event = lambda *a, **k: None
    orch._gpu = gpu
    orch.worktree_mgr = FakeWorktrees(tmp_path)
    backend = FakeBackend(backend_name, fail_spawn=fail_spawn)
    orch._backends[backend_name] = backend
    task = SwarmTask(id="t1", title="T1", description="do it", preferred_backend=backend_name)
    orch.result = SwarmResult(swarm_id="s1", plan_path="plan.md", tasks=[task], started_at=0.0)
    return orch, task, backend


# ---- the orchestrator -----------------------------------------------------------

def test_cline_task_holds_its_model_once_and_releases_it_when_it_finishes(
        tmp_path, no_git, both_installed):
    from service.models import AgentStatus, SwarmStatus

    gpu = FakeGpu()
    orch, task, backend = _orchestrator(tmp_path, gpu)

    orch._launch_task(task, online=True)
    assert gpu.calls == [("hold", "gemma4:e4b")]
    assert task.status == SwarmStatus.RUNNING

    orch._poll_running_agents()
    assert gpu.calls == [("hold", "gemma4:e4b")], "still running: the hold stays"

    backend.next_status = AgentStatus.FINISHED
    orch._poll_running_agents()
    orch._poll_running_agents()
    assert gpu.calls == [("hold", "gemma4:e4b"), ("release", "ollama:gemma4:e4b")]
    assert orch._gpu_holds == {}


def test_crashed_agent_releases_its_hold_and_the_retry_takes_a_new_one(
        tmp_path, no_git, both_installed):
    from service.models import AgentStatus, SwarmStatus

    gpu = FakeGpu()
    orch, task, backend = _orchestrator(tmp_path, gpu)

    orch._launch_task(task, online=True)
    backend.next_status = AgentStatus.CRASHED
    orch._poll_running_agents()
    assert task.status == SwarmStatus.PENDING
    assert gpu.calls == [("hold", "gemma4:e4b"), ("release", "ollama:gemma4:e4b")]

    backend.next_status = None
    orch._launch_task(task, online=True)
    assert gpu.calls[-1] == ("hold", "gemma4:e4b")
    assert orch._gpu_holds == {"t1": "ollama:gemma4:e4b"}


def test_a_spawn_that_fails_gives_the_hold_back(tmp_path, no_git, both_installed):
    gpu = FakeGpu()
    orch, task, _ = _orchestrator(tmp_path, gpu, fail_spawn=True)

    with pytest.raises(RuntimeError):
        orch._launch_task(task, online=True)
    assert gpu.calls == [("hold", "gemma4:e4b"), ("release", "ollama:gemma4:e4b")]
    assert orch._gpu_holds == {}


def test_cancel_releases_the_hold(tmp_path, no_git, both_installed):
    gpu = FakeGpu()
    orch, task, backend = _orchestrator(tmp_path, gpu)

    orch._launch_task(task, online=True)
    orch.cancel()
    assert backend.killed == ["t1"]
    assert gpu.calls == [("hold", "gemma4:e4b"), ("release", "ollama:gemma4:e4b")]


def test_a_refused_hold_still_runs_the_agent_and_releases_nothing(
        tmp_path, no_git, both_installed):
    from service.models import AgentStatus, SwarmStatus

    gpu = FakeGpu(grant=False)
    orch, task, backend = _orchestrator(tmp_path, gpu)

    orch._launch_task(task, online=True)
    assert task.status == SwarmStatus.RUNNING
    backend.next_status = AgentStatus.FINISHED
    orch._poll_running_agents()
    assert gpu.calls == [("hold", "gemma4:e4b")]


def test_a_cloud_backend_task_takes_no_hold(tmp_path, no_git, both_installed):
    gpu = FakeGpu()
    orch, task, _ = _orchestrator(tmp_path, gpu, backend_name="claude")

    orch._launch_task(task, online=True)
    assert gpu.calls == []


# ---- the client -----------------------------------------------------------------

def test_ollama_model_of_reads_the_backend_model_setting():
    from service.gpu_hold import ollama_model_of

    assert ollama_model_of("ollama/gemma4:e4b") == "gemma4:e4b"
    assert ollama_model_of("Ollama/llama3") == "llama3:latest"
    assert ollama_model_of("ollama/") is None
    assert ollama_model_of("anthropic/claude") is None
    assert ollama_model_of(None) is None


class _Response:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def test_client_holds_with_preload_mark_loaded_and_begin_use(monkeypatch):
    import service.gpu_hold as gpu_hold

    monkeypatch.delenv("SWARM_DISABLE_GPU_HOLD", raising=False)
    sent = []
    monkeypatch.setattr(gpu_hold.requests, "post",
                        lambda url, json=None, timeout=None, headers=None: sent.append((url, json)) or _Response())

    client = gpu_hold.GpuHoldClient("http://localhost:5000/api")
    assert client.hold("gemma4:e4b") == "ollama:gemma4:e4b"
    client.release("ollama:gemma4:e4b")

    assert [url for url, _ in sent] == [
        "http://localhost:5000/api/gpu/memory/preload",
        "http://localhost:5000/api/gpu/memory/mark-loaded",
        "http://localhost:5000/api/gpu/memory/begin-use",
        "http://localhost:5000/api/gpu/memory/end-use",
        "http://localhost:5000/api/gpu/memory/release",
    ]
    assert all(body["slot_id"] == "ollama:gemma4:e4b" for _, body in sent)
    assert "vram_mb" not in sent[0][1], "the backend sizes an Ollama model itself"


def test_client_refused_preload_pins_nothing(monkeypatch):
    import service.gpu_hold as gpu_hold

    monkeypatch.delenv("SWARM_DISABLE_GPU_HOLD", raising=False)
    sent = []

    def post(url, json=None, timeout=None, headers=None):
        sent.append(url)
        return _Response(500 if url.endswith("/preload") else 200, {"error": "GPU short"})

    monkeypatch.setattr(gpu_hold.requests, "post", post)

    assert gpu_hold.GpuHoldClient("http://localhost:5000/api").hold("gemma4:e4b") is None
    assert sent == ["http://localhost:5000/api/gpu/memory/preload"]


def test_client_unreachable_backend_is_not_fatal(monkeypatch):
    import service.gpu_hold as gpu_hold

    monkeypatch.delenv("SWARM_DISABLE_GPU_HOLD", raising=False)

    def down(url, json=None, timeout=None, headers=None):
        raise gpu_hold.requests.ConnectionError("refused")

    monkeypatch.setattr(gpu_hold.requests, "post", down)
    client = gpu_hold.GpuHoldClient("http://localhost:5000/api")
    assert client.hold("gemma4:e4b") is None
    client.release("ollama:gemma4:e4b")


def test_client_switched_off_sends_nothing(monkeypatch):
    import service.gpu_hold as gpu_hold

    monkeypatch.setenv("SWARM_DISABLE_GPU_HOLD", "1")
    monkeypatch.setattr(gpu_hold.requests, "post",
                        lambda *a, **k: pytest.fail("no request while switched off"))
    client = gpu_hold.GpuHoldClient("http://localhost:5000/api")
    assert client.hold("gemma4:e4b") is None
    client.release("ollama:gemma4:e4b")
