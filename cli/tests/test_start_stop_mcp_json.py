"""launch's health wait, the headless todo file's cap, and the JSON envelopes of
start, stop, mcp config and mcp list-tools. Everything stays in tmp_path: a
scratch HOME, a stub health server on a free loopback port, and stubbed
start.sh/stop.sh and backend.mcp runs."""

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

CLI_DIR = Path(__file__).resolve().parents[1]
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from llx.main import app  # noqa: E402

runner = CliRunner()


@pytest.fixture(autouse=True)
def _text_mode():
    """JSON mode is process-wide; a --json run must not leak into the next invoke."""
    from llx import output

    output.set_json_mode(False)
    yield
    output.set_json_mode(False)


def _envelope(result):
    assert "\x1b" not in result.stdout
    return json.loads(result.stdout)


# ── launch: health wait ──────────────────────────────────────────────


@pytest.fixture
def health_server():
    """A loopback /api/health that answers the queued statuses in order, then 200.
    Queued uptimes go into the replies' uptime_seconds; with none it is left out."""
    statuses: list[int] = []
    hits: list[int] = []
    uptimes: list[float] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code = statuses.pop(0) if statuses else 200
            hits.append(code)
            body = {"status": "ok"}
            if uptimes:
                body["uptime_seconds"] = uptimes.pop(0)
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", statuses, hits, uptimes
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def sleeps(monkeypatch):
    """Record the launch module's sleeps instead of waiting. Only that module's
    view of `time` is replaced: patching `time.sleep` itself would also take the
    wait out of every background thread in the process."""
    slept: list[float] = []
    from llx.commands import launch

    class _Time:
        sleep = staticmethod(slept.append)

        def __getattr__(self, name):
            return getattr(time, name)

    monkeypatch.setattr(launch, "time", _Time())
    return slept


def test_health_wait_sleeps_after_an_error_status(health_server, sleeps):
    from llx.commands.launch import _wait_for_health

    url, statuses, hits, uptimes = health_server
    statuses.extend([500, 500])
    assert _wait_for_health(url, tries=60, interval=1, timeout=2) is True
    assert hits == [500, 500, 200]
    assert sleeps == [1, 1]


def test_health_wait_spends_every_try_on_a_failing_server(health_server, sleeps):
    from llx.commands.launch import _wait_for_health

    url, statuses, hits, uptimes = health_server
    statuses.extend([500] * 5)
    assert _wait_for_health(url, tries=5, interval=0.25, timeout=1) is False
    assert hits == [500] * 5
    assert sleeps == [0.25] * 4


def test_full_stack_waits_through_500_then_reports_ready(health_server, sleeps, tmp_path, monkeypatch):
    from llx.commands import launch

    url, statuses, hits, uptimes = health_server
    statuses.extend([500, 503, 500])
    root = tmp_path / "install"
    root.mkdir()
    (root / "start.sh").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(launch, "resolve_guaardvark_root", lambda: root)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda cmd, **k: None)
    monkeypatch.setattr(launch, "save_launch_config", lambda cfg: None)
    started = launch._start_full_stack(None, {"server_url": url}, quiet=True)
    assert started == {"root": str(root), "server_url": url, "ready": True}
    assert hits == [500, 503, 500, 200]
    assert sleeps == [1, 1, 1]


# ── headless todo file cap ───────────────────────────────────────────


@pytest.fixture
def todos_dir(tmp_path, monkeypatch):
    d = tmp_path / "todos"
    d.mkdir()
    monkeypatch.setattr("llx.todo._todos_dir", lambda: d)
    return d


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_todo_file_is_trimmed_to_the_newest_entries(todos_dir):
    from llx.todo import KEEP_LINES, MAX_LINES, TodoStore

    store = TodoStore("headless")
    for i in range(MAX_LINES + 1):
        store.add(f"todo {i}")
    kept = _lines(todos_dir / "headless.jsonl")
    assert len(kept) == KEEP_LINES
    assert kept[-1]["text"] == f"todo {MAX_LINES}"
    assert kept[0]["text"] == f"todo {MAX_LINES + 1 - KEEP_LINES}"


def test_repeated_headless_runs_stay_bounded(todos_dir):
    from llx.todo import MAX_LINES, TodoStore

    for run in range(300):
        store = TodoStore("headless")
        for n in range(3):
            store.add(f"run {run} todo {n}")
    lines = _lines(todos_dir / "headless.jsonl")
    assert len(lines) <= MAX_LINES
    assert lines[-1]["text"] == "run 299 todo 2"
    assert len(TodoStore("headless").all()) == len(lines)


def test_an_oversized_file_is_trimmed_when_loaded(todos_dir):
    from llx.todo import KEEP_LINES, MAX_LINES, TodoStore

    path = todos_dir / "headless.jsonl"
    path.write_text("".join(json.dumps({"id": f"t{i}", "text": f"old {i}", "done": False}) + "\n"
                            for i in range(MAX_LINES * 3)))
    store = TodoStore("headless")
    assert len(_lines(path)) == KEEP_LINES
    assert len(store.all()) == KEEP_LINES


def test_done_markers_fold_into_their_todo_on_reload(todos_dir):
    from llx.todo import TodoStore

    store = TodoStore("s1")
    first = store.add("first")
    store.add("second")
    assert store.done(first["id"])
    reloaded = TodoStore("s1")
    assert [t["text"] for t in reloaded.all()] == ["first", "second"]
    assert [t["text"] for t in reloaded.list()] == ["second"]


# ── start / stop --json ──────────────────────────────────────────────


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "install"
    (root / "scripts" / "system-manager").mkdir(parents=True)
    for name in ("start.sh", "stop.sh"):
        (root / name).write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(root))
    monkeypatch.setenv("FLASK_PORT", "5999")
    return root


def _fake_script(monkeypatch, returncode=0, healthy=False):
    runs = []

    def run(cmd, cwd=None, stdout=None, **kwargs):
        runs.append({"cmd": cmd, "cwd": cwd, "stdout": stdout})
        return subprocess.CompletedProcess(cmd, returncode)

    monkeypatch.setattr("llx.commands.system.subprocess.run", run)
    monkeypatch.setattr("llx.backend_bootstrap.is_backend_healthy", lambda url=None, **k: healthy)
    return runs


@pytest.mark.parametrize("action,healthy", [("start", True), ("stop", False)])
def test_script_json_reports_what_ran(install, monkeypatch, action, healthy):
    runs = _fake_script(monkeypatch, healthy=healthy)
    result = runner.invoke(app, [action, "--json"])
    assert result.exit_code == 0, result.output
    assert _envelope(result) == {"status": "success", "data": {
        "action": action, "root": str(install), "script": str(install / f"{action}.sh"),
        "returncode": 0, "server_url": "http://127.0.0.1:5999", "backend_responding": healthy}}
    assert runs == [{"cmd": [str(install / f"{action}.sh")], "cwd": str(install), "stdout": 2}]


def test_global_json_flag_covers_stop(install, monkeypatch):
    _fake_script(monkeypatch)
    result = runner.invoke(app, ["--json", "stop"])
    assert result.exit_code == 0, result.output
    assert _envelope(result)["data"]["action"] == "stop"


def test_failed_script_is_a_json_error_with_its_exit_code(install, monkeypatch):
    _fake_script(monkeypatch, returncode=3)
    result = runner.invoke(app, ["start", "--json"])
    assert result.exit_code == 3
    report = _envelope(result)
    assert report["error"]["code"] == "START_FAILED"
    assert report["data"]["returncode"] == 3


def test_missing_script_is_a_json_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    # The walk up from tmp_path could reach a real checkout's start.sh.
    monkeypatch.setattr("llx.commands.system._find_project_root", lambda path: path)
    monkeypatch.setattr("llx.commands.system.subprocess.run", lambda *a, **k: pytest.fail("ran a script"))
    result = runner.invoke(app, ["stop", "--json"])
    assert result.exit_code == 1
    assert _envelope(result)["error"]["code"] == "SCRIPT_NOT_FOUND"


def _no_start(monkeypatch):
    monkeypatch.setattr("llx.backend_bootstrap.ensure_backend_running",
                        lambda *a, **k: pytest.fail("started the backend"))
    monkeypatch.setattr("llx.commands.system.subprocess.run", lambda *a, **k: pytest.fail("ran a script"))


def test_start_backend_only_json_is_refused(monkeypatch):
    # start.sh has no backend-only mode; the flag would otherwise start the whole stack.
    _no_start(monkeypatch)
    result = runner.invoke(app, ["start", "--backend-only", "--json"])
    assert result.exit_code == 2
    report = _envelope(result)
    assert report["error"]["code"] == "BACKEND_ONLY_UNAVAILABLE"
    assert "guaardvark start" in report["error"]["message"]


def test_start_backend_only_is_refused(monkeypatch):
    _no_start(monkeypatch)
    result = runner.invoke(app, ["start", "--backend-only"])
    assert result.exit_code == 2
    assert "no backend-only mode" in result.output


# ── mcp config / list-tools --json ───────────────────────────────────


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


TOOLS = [{"name": "web_search", "description": "Search the web", "read_only": True, "toolsets": ["research"]}]


@pytest.mark.parametrize("argv", [["mcp", "list-tools", "--json"], ["--json", "mcp", "list-tools"]])
def test_list_tools_json_is_an_envelope(checkout, monkeypatch, argv):
    seen = _fake_backend(monkeypatch, json.dumps(TOOLS), stderr="\n(1 tools exposed, toolset all)\n")
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, result.output
    assert _envelope(result) == {"status": "success", "data": {"toolset": "all", "count": 1, "tools": TOOLS}}
    assert seen["cmd"][1:] == ["-m", "backend.mcp", "list-tools", "--json"]


def test_list_tools_json_unknown_toolset_is_an_error(checkout, monkeypatch):
    _fake_backend(monkeypatch, "", returncode=1, stderr="guaardvark mcp: unknown toolset 'nope'\n")
    result = runner.invoke(app, ["mcp", "list-tools", "--toolset", "nope", "--json"])
    assert result.exit_code == 1
    report = _envelope(result)
    assert report["error"] == {"code": "MCP_ERROR", "message": "guaardvark mcp: unknown toolset 'nope'"}


def test_list_tools_without_json_passes_the_text_through(checkout, monkeypatch):
    seen = {}

    def run(cmd, cwd=None):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("llx.commands.mcp.subprocess.run", run)
    result = runner.invoke(app, ["mcp", "list-tools"])
    assert result.exit_code == 0
    assert seen["cmd"][1:] == ["-m", "backend.mcp", "list-tools"]


def test_mcp_config_json_relays_the_report(checkout, monkeypatch):
    report = {"status": "success", "data": {"client": "cursor", "where": "~/.cursor/mcp.json",
                                            "snippet": {"mcpServers": {"guaardvark": {"command": "sh"}}}}}
    seen = _fake_backend(monkeypatch, json.dumps(report))
    result = runner.invoke(app, ["mcp", "config", "--client", "cursor", "--json"])
    assert result.exit_code == 0, result.output
    assert _envelope(result) == report
    assert seen["cmd"][1:] == ["-m", "backend.mcp", "config", "--client", "cursor", "--json"]


def test_mcp_config_json_without_a_report(checkout, monkeypatch):
    _fake_backend(monkeypatch, "", returncode=2, stderr="invalid choice: 'vim'")
    result = runner.invoke(app, ["mcp", "config", "--client", "vim", "--json"])
    assert result.exit_code == 2
    assert _envelope(result)["error"]["code"] == "MCP_ERROR"


# ── launch --full: the old backend is not "ready"; GUAARDVARK_ROOT is binding ──


def test_full_stack_ignores_the_backend_start_sh_replaces(health_server, sleeps, tmp_path, monkeypatch):
    """The first answers come from a backend up for an hour, the one start.sh
    stops; ready waits for one that started after the spawn."""
    from llx.commands import launch

    url, statuses, hits, uptimes = health_server
    uptimes.extend([3600.0, 3600.0, 0.5])
    root = tmp_path / "install"
    root.mkdir()
    (root / "start.sh").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(launch, "resolve_guaardvark_root", lambda: root)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda cmd, **k: None)
    monkeypatch.setattr(launch, "save_launch_config", lambda cfg: None)
    started = launch._start_full_stack(None, {"server_url": url}, quiet=True)
    assert started["ready"] is True
    assert hits == [200, 200, 200]
    assert sleeps == [1, 1]


def test_full_stack_with_only_the_old_backend_is_not_ready(health_server, sleeps, tmp_path, monkeypatch):
    from llx.commands import launch

    url, statuses, hits, uptimes = health_server
    uptimes.extend([3600.0] * 60)
    root = tmp_path / "install"
    root.mkdir()
    (root / "start.sh").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(launch, "resolve_guaardvark_root", lambda: root)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda cmd, **k: None)
    monkeypatch.setattr(launch, "save_launch_config", lambda cfg: None)
    assert launch._start_full_stack(None, {"server_url": url}, quiet=True)["ready"] is False
    assert len(hits) == 60 and len(sleeps) == 59


def test_a_reply_without_uptime_counts_as_new(health_server, sleeps):
    import time

    from llx.commands.launch import _wait_for_health

    url, statuses, hits, uptimes = health_server
    assert _wait_for_health(url, tries=3, interval=1, timeout=2, started_after=time.time()) is True
    assert hits == [200]


def test_guaardvark_root_set_to_a_missing_folder_finds_nothing(tmp_path, monkeypatch):
    from llx.launch_config import resolve_guaardvark_root

    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path / "missing"))
    assert resolve_guaardvark_root() is None
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    assert resolve_guaardvark_root() == tmp_path


def test_launch_full_json_names_a_bad_guaardvark_root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".guaardvark").mkdir(parents=True)
    (home / ".guaardvark" / "config.json").write_text(json.dumps({"onboarded": True}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path / "missing"))
    monkeypatch.setattr("llx.commands.launch.subprocess.Popen", lambda *a, **k: pytest.fail("spawned start.sh"))
    result = runner.invoke(app, ["launch", "--full", "--yes", "--json"])
    assert result.exit_code == 1
    error = _envelope(result)["error"]
    assert error["code"] == "INSTALL_NOT_FOUND"
    assert "not a folder" in error["message"]


def test_launch_full_refuses_when_another_install_holds_the_port(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".guaardvark").mkdir(parents=True)
    (home / ".guaardvark" / "config.json").write_text(json.dumps({"onboarded": True}))
    root = tmp_path / "install"
    root.mkdir()
    (root / "start.sh").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GUAARDVARK_ROOT", str(root))
    monkeypatch.setattr("llx.port_owner.other_install_holders",
                        lambda root, ports: [{"port": 5000, "pid": 4242, "install": "/srv/other"}])
    monkeypatch.setattr("llx.commands.launch.subprocess.Popen", lambda *a, **k: pytest.fail("spawned start.sh"))
    result = runner.invoke(app, ["launch", "--full", "--yes", "--json"])
    assert result.exit_code == 1
    error = _envelope(result)["error"]
    assert error["code"] == "PORT_HELD_BY_OTHER_INSTALL"
    assert "/srv/other" in error["message"] and "4242" in error["message"]


def test_a_port_lookup_that_fails_reports_no_listeners(monkeypatch, caplog):
    from llx import port_owner

    def broken(*args, **kwargs):
        raise OSError("lsof: cannot execute")

    monkeypatch.setattr(port_owner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(port_owner.subprocess, "run", broken)
    with caplog.at_level("WARNING", logger="llx.port_owner"):
        assert port_owner.port_listener_pids(5000) == []
    assert "port 5000" in caplog.text
