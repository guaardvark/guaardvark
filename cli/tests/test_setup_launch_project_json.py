"""JSON envelopes for the commands that change things: mcp install and doctor,
analyze, init, setup and launch. Every run here stays inside tmp_path: a scratch
HOME, a scratch project, no real backend and no real start.sh."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

CLI_DIR = Path(__file__).resolve().parents[1]
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from llx.client import LlxConnectionError  # noqa: E402
from llx.main import app  # noqa: E402

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    import llx.config as config
    import llx.commands.system as system
    cli_json = home / ".guaardvark" / "cli.json"
    monkeypatch.setattr(config, "CONFIG_DIR", cli_json.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", cli_json)
    monkeypatch.setattr(config, "LEGACY_CONFIG_FILE", home / ".llx" / "config.json")
    monkeypatch.setattr(system, "CONFIG_FILE", cli_json)
    monkeypatch.setattr("llx.todo._todos_dir", lambda: home)
    return home


def _envelope(result):
    assert "\x1b" not in result.stdout
    return json.loads(result.stdout)


# ── mcp install / doctor ─────────────────────────────────────────────


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "backend" / "mcp").mkdir(parents=True)
    (root / "start.sh").touch()
    (root / "backend" / "mcp" / "__main__.py").touch()
    monkeypatch.setenv("GUAARDVARK_ROOT", str(root))
    return root


def _fake_backend(monkeypatch, stdout, returncode=0, stderr=""):
    seen = {}

    def run(cmd, cwd=None, capture_output=False, text=False):
        seen["cmd"], seen["cwd"] = cmd, cwd
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr("llx.commands.mcp.subprocess.run", run)
    return seen


def test_mcp_install_json_relays_the_report(checkout, monkeypatch):
    report = {"status": "success", "data": {"dry_run": True, "results": [
        {"client": "cursor", "status": "dry-run", "detail": "would write guaardvark entry"}],
        "failed": [], "restart_needed": False}}
    seen = _fake_backend(monkeypatch, json.dumps(report))
    result = runner.invoke(app, ["mcp", "install", "--client", "cursor", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    assert _envelope(result) == report
    assert seen["cmd"][1:] == ["-m", "backend.mcp", "install", "--dry-run", "--client", "cursor", "--json"]
    assert seen["cwd"] == str(checkout)


def test_mcp_doctor_json_keeps_the_exit_code(checkout, monkeypatch):
    report = {"status": "error", "data": {"checks": [], "failed": ["client: cursor (user)"]},
              "error": {"code": "CHECKS_FAILED", "message": "Failed: client: cursor (user)"}}
    seen = _fake_backend(monkeypatch, json.dumps(report), returncode=1)
    result = runner.invoke(app, ["--json", "mcp", "doctor", "--call"])
    assert result.exit_code == 1
    assert _envelope(result)["error"]["code"] == "CHECKS_FAILED"
    assert seen["cmd"][-3:] == ["doctor", "--call", "--json"]


def test_mcp_doctor_json_without_a_report_is_an_error(checkout, monkeypatch):
    _fake_backend(monkeypatch, "", returncode=1, stderr="\x1b[31mTraceback: ImportError\x1b[0m")
    result = runner.invoke(app, ["mcp", "doctor", "--json"])
    assert result.exit_code == 1
    payload = _envelope(result)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "MCP_ERROR"
    assert payload["data"]["errors"] == "Traceback: ImportError"


def test_mcp_install_json_without_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path / "nowhere"))
    monkeypatch.setattr("llx.commands.mcp.subprocess.run",
                        lambda *a, **k: pytest.fail("ran the backend without a checkout"))
    result = runner.invoke(app, ["mcp", "install", "--json"])
    assert result.exit_code == 1
    assert _envelope(result)["error"]["code"] == "CHECKOUT_NOT_FOUND"


# ── analyze / init ───────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "site"
    proj.mkdir()
    (proj / "package.json").write_text("{}")
    (proj / "style.css").write_text("body { color: red; }")
    monkeypatch.chdir(proj)

    class Offline:
        def execute_tool(self, *a, **k):
            raise LlxConnectionError("offline")

    monkeypatch.setattr("llx.client.get_client", lambda server=None: Offline())
    return proj


def test_analyze_json_reports_the_written_instructions(home, project):
    result = runner.invoke(app, ["analyze", str(project), "--json"])
    assert result.exit_code == 0, result.output
    data = _envelope(result)["data"]
    assert data["root"] == str(project)
    assert data["guaardvark_md"] == {"status": "created", "path": str(project / "GUAARDVARK.md")}
    assert data["css_files"] == ["style.css"]
    assert data["used_backend_tools"] is False
    assert data["todos_added"]
    assert (project / "GUAARDVARK.md").is_file()


def test_analyze_json_leaves_existing_instructions(home, project):
    (project / "GUAARDVARK.md").write_text("# mine\n")
    result = runner.invoke(app, ["--json", "analyze", str(project)])
    assert _envelope(result)["data"]["guaardvark_md"] == {"status": "present"}
    assert (project / "GUAARDVARK.md").read_text() == "# mine\n"


def test_analyze_json_missing_folder_is_an_error(home, tmp_path):
    result = runner.invoke(app, ["analyze", str(tmp_path / "missing"), "--json"])
    assert result.exit_code == 1
    assert _envelope(result)["error"]["code"] == "PATH_NOT_FOUND"


def test_analyze_without_json_still_prints_text(home, project):
    result = runner.invoke(app, ["analyze", str(project)])
    assert result.exit_code == 0
    assert "Analyzed project at" in result.stdout
    assert not result.stdout.lstrip().startswith("{")


def test_init_json_writes_once_then_reports_exists(home, project):
    first = runner.invoke(app, ["init", "--json"])
    assert first.exit_code == 0, first.output
    data = _envelope(first)["data"]
    assert data["written"] is True and data["status"] == "created"
    assert data["path"] == str(project / "GUAARDVARK.md")

    second = runner.invoke(app, ["init", "--json"])
    data = _envelope(second)["data"]
    assert data["written"] is False and data["status"] == "exists"

    forced = runner.invoke(app, ["init", "--force", "--json"])
    assert _envelope(forced)["data"]["written"] is True


# ── setup ────────────────────────────────────────────────────────────


class _Server:
    def __init__(self, server=None, up=True):
        self.server_url, self.up = server, up

    def get(self, endpoint, **params):
        if not self.up:
            raise LlxConnectionError(f"Cannot connect to {self.server_url}")
        if endpoint == "/api/health":
            return {"version": "9.9.9"}
        return {"data": {"text_model": "qwen3:8b"}}


def test_setup_json_saves_a_reachable_server(home, monkeypatch):
    monkeypatch.setattr("llx.commands.system.get_client", lambda server=None: _Server(server))
    result = runner.invoke(app, ["setup", "--server", "http://127.0.0.1:5999", "--api-key", "k1", "--json"])
    assert result.exit_code == 0, result.output
    data = _envelope(result)["data"]
    assert data["saved"] is True and data["version"] == "9.9.9"
    assert data["active_model"] == "qwen3:8b"
    assert data["api_key"] == "set" and "k1" not in result.stdout
    saved = json.loads((home / ".guaardvark" / "cli.json").read_text())
    assert saved["server"] == "http://127.0.0.1:5999" and saved["api_key"] == "k1"


def test_setup_json_keeps_the_key_unless_given(home, monkeypatch):
    monkeypatch.setattr("llx.commands.system.get_client", lambda server=None: _Server(server))
    runner.invoke(app, ["setup", "-s", "http://127.0.0.1:5999", "--api-key", "k1", "--json"])
    result = runner.invoke(app, ["--json", "setup", "-s", "http://127.0.0.1:5998"])
    data = _envelope(result)["data"]
    assert data["api_key"] == "set" and data["api_key_changed"] is False
    assert json.loads((home / ".guaardvark" / "cli.json").read_text())["api_key"] == "k1"


def test_setup_json_unreachable_server_saves_nothing(home, monkeypatch):
    monkeypatch.setattr("llx.commands.system.get_client", lambda server=None: _Server(server, up=False))
    result = runner.invoke(app, ["setup", "--server", "http://127.0.0.1:5999", "--json"])
    assert result.exit_code == 1
    payload = _envelope(result)
    assert payload["error"]["code"] == "CONNECTION_ERROR"
    assert payload["data"]["saved"] is False
    assert not (home / ".guaardvark" / "cli.json").exists()


# ── launch ───────────────────────────────────────────────────────────


def test_launch_json_first_run_needs_yes(home):
    result = runner.invoke(app, ["launch", "--config", "--json"])
    assert result.exit_code == 1
    assert _envelope(result)["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert not (home / ".guaardvark" / "config.json").exists()


def test_launch_json_config_only(home, monkeypatch):
    monkeypatch.setattr("llx.onboarding.fetch_ollama_models", lambda url: [])
    result = runner.invoke(app, ["launch", "--config", "--yes", "--model", "qwen3:8b", "--json"])
    assert result.exit_code == 0, result.output
    data = _envelope(result)["data"]
    assert data["onboarded"] is True and data["started"] is None
    assert data["model"] == "qwen3:8b" and data["mode"] == "lite"
    assert json.loads((home / ".guaardvark" / "config.json").read_text())["onboarded"] is True


def test_launch_json_lite_is_refused_without_starting(home, monkeypatch):
    (home / ".guaardvark").mkdir()
    (home / ".guaardvark" / "config.json").write_text(json.dumps({"onboarded": True, "mode": "lite"}))
    monkeypatch.setattr("llx.commands.launch._start_lite_mode",
                        lambda *a, **k: pytest.fail("started the lite server"))
    monkeypatch.setattr("llx.repl.launch_repl", lambda: pytest.fail("opened the REPL"))
    result = runner.invoke(app, ["launch", "--json"])
    assert result.exit_code == 1
    assert _envelope(result)["error"]["code"] == "LITE_NEEDS_REPL"


def test_launch_json_full_reports_the_started_stack(home, tmp_path, monkeypatch):
    (home / ".guaardvark").mkdir()
    (home / ".guaardvark" / "config.json").write_text(json.dumps(
        {"onboarded": True, "mode": "lite", "server_url": "http://127.0.0.1:5999"}))
    root = tmp_path / "install"
    root.mkdir()
    (root / "start.sh").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr("llx.commands.launch.resolve_guaardvark_root", lambda: root)
    started = []
    monkeypatch.setattr("llx.commands.launch.subprocess.Popen", lambda cmd, **k: started.append(cmd))

    class Health:
        status_code = 200

    monkeypatch.setattr("httpx.get", lambda url, timeout=None: Health())
    monkeypatch.setattr("llx.repl.launch_repl", lambda: pytest.fail("opened the REPL"))
    result = runner.invoke(app, ["launch", "--full", "--json"])
    assert result.exit_code == 0, result.output
    data = _envelope(result)["data"]
    assert data["started"] == {"root": str(root), "server_url": "http://127.0.0.1:5999", "ready": True}
    assert data["mode"] == "full"
    assert started == [["bash", str(root / "start.sh")]]


def test_launch_json_full_without_an_install(home, monkeypatch):
    (home / ".guaardvark").mkdir()
    (home / ".guaardvark" / "config.json").write_text(json.dumps({"onboarded": True}))
    monkeypatch.setattr("llx.commands.launch.resolve_guaardvark_root", lambda: None)
    result = runner.invoke(app, ["launch", "--full", "--json"])
    assert result.exit_code == 1
    assert _envelope(result)["error"]["code"] == "INSTALL_NOT_FOUND"
