"""The swarm reaches the backend on the port the backend listens on: FLASK_PORT
when the swarm inherits it, else the one this checkout's .env records, else the
backend's own default of 5000."""

import pytest


@pytest.fixture
def no_flask_port(monkeypatch, tmp_path):
    """FLASK_PORT unset and a checkout .env that does not exist yet."""
    import service.backend_url as backend_url

    monkeypatch.delenv("FLASK_PORT", raising=False)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(backend_url, "CHECKOUT_ENV", env_file)
    return env_file


class _Response:
    status_code = 200

    def json(self):
        return {}


def test_unset_means_the_backends_default_port(no_flask_port):
    from service.backend_url import backend_api_url

    assert backend_api_url() == "http://localhost:5000/api"


def test_the_port_start_sh_recorded_in_env_is_used(no_flask_port):
    from service.backend_url import backend_api_url

    no_flask_port.write_text("OTHER=1\nFLASK_PORT=5055\n")
    assert backend_api_url() == "http://localhost:5055/api"


def test_an_inherited_flask_port_wins(no_flask_port, monkeypatch):
    from service.backend_url import backend_api_url

    no_flask_port.write_text("FLASK_PORT=5055\n")
    monkeypatch.setenv("FLASK_PORT", "5123")
    assert backend_api_url() == "http://localhost:5123/api"


def test_gpu_holds_reach_the_backend_port_with_flask_port_unset(no_flask_port, monkeypatch, tmp_path):
    import service.gpu_hold as gpu_hold
    from service.config import SwarmConfig
    from service.orchestrator import SwarmOrchestrator

    monkeypatch.delenv("SWARM_DISABLE_GPU_HOLD", raising=False)
    sent = []
    monkeypatch.setattr(gpu_hold.requests, "post",
                        lambda url, json=None, timeout=None, headers=None: sent.append(url) or _Response())

    orch = SwarmOrchestrator(tmp_path, SwarmConfig())
    slot = orch._gpu_client().hold("gemma4:e4b")
    orch._gpu_client().release(slot)

    assert slot == "ollama:gemma4:e4b"
    assert len(sent) == 5
    assert all(url.startswith("http://localhost:5000/api/gpu/memory/") for url in sent)


def test_vram_reads_reach_the_backend_port_with_flask_port_unset(no_flask_port, monkeypatch):
    import service.resource_monitor as rm

    asked = []
    monkeypatch.setattr(rm.requests, "get", lambda url, timeout=None: asked.append(url) or _Response())

    rm.ResourceMonitor()._get_vram_from_backend()

    assert asked == ["http://localhost:5000/api/gpu/memory/status"]


def test_events_and_agent_bus_urls_use_the_backend_port(no_flask_port, monkeypatch, tmp_path):
    import service.orchestrator as orchestrator
    from service.agent_backends.claude_backend import ClaudeBackend
    from service.config import SwarmConfig
    from service.models import SwarmTask

    posted = []
    monkeypatch.setattr(orchestrator.requests, "post",
                        lambda url, json=None, timeout=None, headers=None: posted.append(url) or _Response())

    orchestrator.SwarmOrchestrator(tmp_path, SwarmConfig())._emit_event("task_spawned", "t1", {})
    prompt = ClaudeBackend()._build_prompt(SwarmTask(id="t1", title="T1", description="do it"))

    assert posted == ["http://localhost:5000/api/swarm/event"]
    assert "http://localhost:5000/api/swarm/" in prompt
    assert ":5002" not in prompt
