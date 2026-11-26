"""E6: static dependency healthcheck.

collect_dependency_status() must report missing binaries without any network
or GPU work. When the agent CLIs (claude/openclaw/cline) are absent, the
result must flag them missing and mark them installed:false.
"""

import json
import subprocess
import sys

import service.deps_check as deps_check
from service.deps_check import collect_dependency_status


def _no_agent_clis(monkeypatch):
    real_which = deps_check.shutil.which

    def fake_which(cmd):
        if cmd in ("claude", "cline", "openclaw", "cline-cli"):
            return None
        return real_which(cmd)  # leave git etc. as-is

    monkeypatch.setattr(deps_check.shutil, "which", fake_which)


def test_no_agent_cli_is_launch_ready_on_the_builtin_agent(monkeypatch):
    """No claude / cline / openclaw on PATH: the built-in agent still launches."""
    _no_agent_clis(monkeypatch)

    status = collect_dependency_status()
    by_name = {d["name"]: d for d in status["dependencies"]}

    assert by_name["claude"]["installed"] is False
    assert by_name["cline"]["installed"] is False
    assert by_name["guaardvark"]["installed"] is True
    assert "agent-cli" not in status["missing"]


def test_no_agent_at_all_flagged(monkeypatch):
    """Neither an agent CLI nor the built-in agent -> not launch-ready."""
    _no_agent_clis(monkeypatch)
    monkeypatch.setattr(deps_check, "_builtin_agent_present", lambda: False)

    status = collect_dependency_status()

    assert "agent-cli" in status["missing"]
    assert status["ok"] is False


def test_all_binaries_present(monkeypatch):
    """When everything resolves on PATH, no launch deps are missing."""
    monkeypatch.setattr(deps_check.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    status = collect_dependency_status()
    assert "agent-cli" not in status["missing"]
    # git present too
    by_name = {d["name"]: d for d in status["dependencies"]}
    assert by_name["git"]["installed"] is True


def test_shape_is_static_and_serializable():
    """Pure-static contract: returns the documented keys and is JSON-able."""
    status = collect_dependency_status()
    assert set(status.keys()) == {"dependencies", "missing", "ok"}
    for d in status["dependencies"]:
        assert set(d.keys()) == {"name", "kind", "required_for", "installed", "detail"}
    json.dumps(status)  # must not raise


def test_runnable_as_module():
    """`python -m plugins.swarm.service.deps_check` prints JSON."""
    repo_root = deps_check.Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "plugins.swarm.service.deps_check"],
        capture_output=True, text=True, timeout=15, cwd=str(repo_root),
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert "dependencies" in parsed and "missing" in parsed and "ok" in parsed
