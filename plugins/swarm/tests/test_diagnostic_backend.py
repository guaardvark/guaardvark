"""The diagnostic pass is on by default and runs on the backend that ran
the failed task — never the cloud CLI in Flight Mode."""

import pytest


def _config(enabled=None, flight_mode=False):
    """Two backends; enabled=None keeps the stock enable_diagnostic_agent default."""
    from service.config import BackendConfig, SwarmConfig

    cfg = SwarmConfig(flight_mode=flight_mode)
    if enabled is not None:
        cfg.enable_diagnostic_agent = enabled
    cfg.backends = {
        "claude": BackendConfig(name="claude", command="claude",
                                args=["--print", "--bare", "--dangerously-skip-permissions"],
                                requires_internet=True, priority=1),
        "cline": BackendConfig(name="cline", command="cline", model="ollama/local-model",
                               requires_internet=False, priority=2),
    }
    return cfg


class _Recorder:
    """Stands in for DiagnosticAgent and records how it was built."""
    built = []

    def __init__(self, backend_url, command):
        self.command = list(command)
        _Recorder.built.append(self.command)

    def run_diagnosis(self, *args, **kwargs):
        return False


@pytest.fixture
def recorder(monkeypatch):
    import service.agent_backends.cline_backend as cline_backend

    _Recorder.built = []
    monkeypatch.setattr("service.diagnostic_agent.DiagnosticAgent", _Recorder)
    monkeypatch.setattr(cline_backend, "resolve_cli_command", lambda config: config.get("command"))
    monkeypatch.setattr(cline_backend, "probe_cli", lambda command: {
        "ok": True, "message_flag": "--message", "model_flag": "--model",
    })
    return _Recorder


def _crashed_task_orchestrator(tmp_path, cfg, backend_name, flight_mode):
    """An orchestrator whose one task has crashed with its retries used up."""
    from service.models import AgentStatus, SwarmResult, SwarmStatus, SwarmTask
    from service.agent_backends.base_backend import AgentProcess
    from service.orchestrator import SwarmOrchestrator

    orch = SwarmOrchestrator(tmp_path, cfg)
    events = []
    orch._emit_event = lambda event_type, task_id, data: events.append(event_type)

    task = SwarmTask(id="t1", title="T1", description="make the test pass",
                     status=SwarmStatus.RUNNING, backend_name=backend_name,
                     worktree_path=str(tmp_path))
    orch.result = SwarmResult(swarm_id="s1", plan_path="plan.md", tasks=[task],
                              started_at=0.0, flight_mode=flight_mode)
    orch._retries[task.id] = orch.max_retries

    real = orch._backends[backend_name]

    class Crashed(type(real)):
        def check_status(self, process):
            return AgentStatus.CRASHED

        def get_logs(self, process, lines=50):
            return "test failed"

    orch._backends[backend_name] = Crashed()
    orch._processes[task.id] = AgentProcess(task_id=task.id, backend_name=backend_name,
                                            worktree_path=str(tmp_path),
                                            status=AgentStatus.RUNNING)
    return orch, task, events


def test_diagnostic_agent_is_on_by_default(tmp_path):
    from service.config import DEFAULT_CONFIG_PATH, SwarmConfig, load_config

    assert SwarmConfig().enable_diagnostic_agent is True
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("defaults:\n  max_concurrent_agents: 3\n")
    assert load_config(cfg_path).enable_diagnostic_agent is True
    assert load_config(DEFAULT_CONFIG_PATH).enable_diagnostic_agent is True


def test_flight_mode_cline_failure_is_diagnosed_with_cline(tmp_path, recorder):
    from service.models import SwarmStatus

    orch, task, events = _crashed_task_orchestrator(
        tmp_path, _config(flight_mode=True), "cline", flight_mode=True)

    orch._poll_running_agents()

    assert len(recorder.built) == 1
    command = recorder.built[0]
    assert command[0] == "cline"
    assert "claude" not in command
    assert "task_diagnostic_start" in events
    assert task.status == SwarmStatus.FAILED


def test_cline_failure_online_still_uses_cline(tmp_path, recorder):
    orch, _, _ = _crashed_task_orchestrator(tmp_path, _config(), "cline", flight_mode=False)

    orch._poll_running_agents()

    assert recorder.built and recorder.built[0][0] == "cline"
    assert "claude" not in recorder.built[0]


def test_claude_failure_online_uses_claude(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    orch, _, _ = _crashed_task_orchestrator(tmp_path, _config(), "claude", flight_mode=False)

    orch._poll_running_agents()

    assert recorder.built == [["claude", "--print", "--bare", "--dangerously-skip-permissions"]]


def test_claude_without_an_api_key_runs_the_signed_in_cli(monkeypatch):
    """--bare signs in with ANTHROPIC_API_KEY only; without one the CLI's own
    sign-in is used, with the user's settings, hooks and MCP servers left out."""
    from service.agent_backends.claude_backend import ClaudeBackend

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    command = ClaudeBackend().command_prefix(
        {"command": "claude", "args": ["--print", "--bare", "--dangerously-skip-permissions"]})

    assert "--bare" not in command
    assert command[:3] == ["claude", "--print", "--dangerously-skip-permissions"]
    assert command[command.index("--setting-sources") + 1] == "project,local"
    assert "--strict-mcp-config" in command


def test_agents_do_not_inherit_secrets(monkeypatch):
    from service.agent_backends.base_backend import agent_env

    for name in ("DATABASE_URL", "SECRET_KEY", "GUAARDVARK_API_KEY", "DISCORD_BOT_TOKEN", "SSH_AUTH_SOCK",
                 "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "value")
    monkeypatch.setenv("KEYBOARD_LAYOUT", "us")

    env = agent_env(keep=("ANTHROPIC_API_KEY",))

    assert env["ANTHROPIC_API_KEY"] == "value" and env["KEYBOARD_LAYOUT"] == "us" and "PATH" in env
    for name in ("DATABASE_URL", "SECRET_KEY", "GUAARDVARK_API_KEY", "DISCORD_BOT_TOKEN", "SSH_AUTH_SOCK"):
        assert name not in env


def test_no_cloud_diagnosis_in_flight_mode(tmp_path, recorder):
    from service.models import SwarmStatus

    orch, task, events = _crashed_task_orchestrator(
        tmp_path, _config(), "claude", flight_mode=True)

    orch._poll_running_agents()

    assert recorder.built == []
    assert "task_diagnostic_start" not in events
    assert task.status == SwarmStatus.FAILED


def test_disabled_diagnostic_agent_builds_nothing(tmp_path, recorder):
    orch, _, events = _crashed_task_orchestrator(
        tmp_path, _config(enabled=False), "cline", flight_mode=False)

    orch._poll_running_agents()

    assert recorder.built == []
    assert "task_diagnostic_start" not in events
