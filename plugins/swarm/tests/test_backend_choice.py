"""A task that names a backend runs on that backend or fails; it is never
moved to another. A task that names none runs locally: the hosted CLI only
runs a task that names it."""

import pytest


INSTALLED = {"claude": "claude-on-path"}


def _config(flight_mode=False):
    from service.config import BackendConfig, SwarmConfig

    cfg = SwarmConfig(flight_mode=flight_mode)
    cfg.backends = {
        "claude": BackendConfig(name="claude", command="claude", requires_internet=True, priority=1),
        "cline": BackendConfig(name="cline", command="cline", model="ollama/local-model",
                               requires_internet=False, priority=2),
    }
    return cfg


@pytest.fixture
def only_claude_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd, *a, **k: INSTALLED.get(cmd))


@pytest.fixture
def both_installed(monkeypatch):
    installed = {**INSTALLED, "cline": "cline-on-path"}
    monkeypatch.setattr("shutil.which", lambda cmd, *a, **k: installed.get(cmd))


class _StopAtWorktree(Exception):
    """Raised when a launch gets past backend selection."""


class _WorktreeTripwire:
    def create(self, task_id):
        raise _StopAtWorktree(task_id)


def _orchestrator(tmp_path, cfg):
    from service.orchestrator import SwarmOrchestrator

    orch = SwarmOrchestrator(tmp_path, cfg)
    orch.worktree_mgr = _WorktreeTripwire()
    return orch


def _task(preferred=None, tags=None):
    from service.models import SwarmTask

    return SwarmTask(id="t1", title="T1", description="do it",
                     preferred_backend=preferred, tags=dict(tags or {}))


def test_missing_preferred_backend_gives_none_and_reason(only_claude_installed):
    backend, reason = _config().select_backend("cline", online=True)
    assert backend is None
    assert "cline" in reason


def test_unknown_preferred_backend_gives_none(only_claude_installed):
    backend, reason = _config().select_backend("nonexistent", online=True)
    assert backend is None
    assert "unknown backend" in reason


def test_online_backend_refused_offline(only_claude_installed):
    backend, reason = _config().select_backend("claude", online=False)
    assert backend is None
    assert "internet" in reason


def test_online_backend_refused_in_flight_mode(only_claude_installed):
    backend, reason = _config(flight_mode=True).select_backend("claude", online=True)
    assert backend is None
    assert "internet" in reason


def test_installed_preferred_backend_is_used(only_claude_installed):
    backend, reason = _config().select_backend("claude", online=True)
    assert backend.name == "claude"
    assert reason == ""


def test_no_preference_runs_on_the_local_backend(both_installed):
    backend, reason = _config().select_backend(None, online=True)
    assert backend.name == "cline" and reason == ""


def test_no_preference_never_reaches_the_hosted_backend(only_claude_installed):
    backend, reason = _config().select_backend(None, online=True)
    assert backend is None
    assert "[Backend: claude]" in reason


def test_an_unnamed_task_with_only_the_hosted_cli_says_how_to_use_it(tmp_path, only_claude_installed):
    orch = _orchestrator(tmp_path, _config())
    with pytest.raises(RuntimeError, match=r"no local agent can run \(cline: 'cline' is not installed\).*\[Backend: claude\]"):
        orch._launch_task(_task(), online=True)


def test_launch_error_names_the_missing_backend(tmp_path, only_claude_installed):
    orch = _orchestrator(tmp_path, _config())
    with pytest.raises(RuntimeError, match="requested backend cline not available"):
        orch._launch_task(_task(preferred="cline"), online=True)


@pytest.mark.parametrize("key", ["Backend", "backend", "BACKEND"])
def test_backend_tag_read_in_any_case(tmp_path, only_claude_installed, key):
    orch = _orchestrator(tmp_path, _config())
    with pytest.raises(RuntimeError, match="requested backend nonexistent not available"):
        orch._launch_task(_task(tags={key: "Nonexistent"}), online=True)


def test_model_tag_read_in_any_case(tmp_path, only_claude_installed):
    orch = _orchestrator(tmp_path, _config())
    with pytest.raises(RuntimeError, match="requested model CLINE not available: 'cline' is not installed"):
        orch._launch_task(_task(tags={"model": "CLINE"}), online=True)


def test_model_tag_matching_no_backend_fails(tmp_path, only_claude_installed):
    orch = _orchestrator(tmp_path, _config())
    with pytest.raises(RuntimeError,
                       match="requested model big-cloud-model not available: no configured backend uses it"):
        orch._launch_task(_task(tags={"Model": "big-cloud-model"}), online=True)


def test_model_tag_whose_backend_is_missing_fails(tmp_path, only_claude_installed):
    orch = _orchestrator(tmp_path, _config())
    with pytest.raises(RuntimeError, match="requested model local-model not available: cline: "):
        orch._launch_task(_task(tags={"MODEL": "local-model"}), online=True)


def test_model_tag_picks_the_backend_configured_with_it(both_installed):
    backend, reason = _config().select_backend_for_model("LOCAL-MODEL", online=True)
    assert backend.name == "cline"
    assert reason == ""


def test_model_tag_names_a_backend(both_installed):
    backend, _ = _config().select_backend_for_model("Claude", online=True)
    assert backend.name == "claude"


def test_model_tag_offline_does_not_reach_an_online_backend(both_installed):
    backend, reason = _config().select_backend_for_model("claude", online=False)
    assert backend is None
    assert "internet" in reason


@pytest.mark.parametrize("value", ["any", "Auto", "none"])
def test_model_tag_no_preference_keeps_the_default_choice(tmp_path, both_installed, value):
    orch = _orchestrator(tmp_path, _config())
    with pytest.raises(_StopAtWorktree):
        orch._launch_task(_task(tags={"Model": value}), online=True)


def test_model_tag_no_preference_leaves_backend_tag_in_force(tmp_path, only_claude_installed):
    orch = _orchestrator(tmp_path, _config())
    with pytest.raises(RuntimeError, match="requested backend nonexistent not available"):
        orch._launch_task(_task(tags={"Model": "any", "Backend": "nonexistent"}), online=True)


def test_backend_tag_any_keeps_the_default_choice(tmp_path, both_installed):
    orch = _orchestrator(tmp_path, _config())
    with pytest.raises(_StopAtWorktree):
        orch._launch_task(_task(tags={"Backend": "any"}), online=True)
