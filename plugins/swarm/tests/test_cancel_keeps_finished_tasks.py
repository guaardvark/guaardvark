"""Cancelling a swarm marks only the tasks still running as cancelled."""

import time

import service.orchestrator as orch
from service.agent_backends.base_backend import AgentProcess
from service.config import SwarmConfig
from service.models import SwarmResult, SwarmStatus, SwarmTask


class _Backend:
    def __init__(self):
        self.killed = []

    def kill(self, process):
        self.killed.append(process.task_id)


def test_cancel_leaves_finished_tasks_as_they_are(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    o = orch.SwarmOrchestrator(repo, SwarmConfig(enable_diagnostic_agent=False))
    monkeypatch.setattr(orch.SwarmOrchestrator, "_emit_event", lambda self, *a, **k: None)
    monkeypatch.setattr(o, "_release_gpu_hold", lambda task_id: None)
    backend = _Backend()
    o._backends = {"fake": backend}

    states = {
        "running": SwarmStatus.RUNNING,
        "done": SwarmStatus.DONE,
        "review": SwarmStatus.NEEDS_REVIEW,
    }
    tasks = [SwarmTask(id=k, title=k, description="", status=v) for k, v in states.items()]
    o.swarm_id = "swarm-x"
    o.result = SwarmResult(swarm_id="swarm-x", plan_path="plan.md", tasks=tasks, started_at=time.time())
    o._processes = {k: AgentProcess(task_id=k, backend_name="fake") for k in states}

    o.cancel()

    got = {t.id: t.status for t in o.result.tasks}
    assert got == {
        "running": SwarmStatus.CANCELLED,
        "done": SwarmStatus.DONE,
        "review": SwarmStatus.NEEDS_REVIEW,
    }
