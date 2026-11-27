"""A start that times out or never answers keeps the tail of what its script printed.

Audio Foundry's first start runs for 10-20 minutes inside start.sh. When it hit
start_timeout or the health wait ran out, the manager reported only "timeout"
and dropped the script's output, which is where the cause is.
"""

import io
import json
import time
from types import SimpleNamespace

import pytest

from backend.plugins import plugin_manager as pm_module
from backend.plugins.plugin_manager import PluginManager
from backend.plugins.plugin_registry import PluginRegistry
from backend.services.plugin_runner import PluginRunnerClient, timeout_output

PLUGIN_ID = "slowstart"


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", raising=False)
    monkeypatch.setenv("GUAARDVARK_SKIP_DEP_RECONCILER", "1")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    import backend.extensions as _ext
    monkeypatch.setattr(_ext, "plugin_dirs", lambda *_a, **_k: [])
    root = tmp_path / "plugins"
    plugin = root / PLUGIN_ID
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "scripts" / "start.sh").write_text("#!/bin/bash\n")
    (plugin / "scripts" / "stop.sh").write_text("#!/bin/bash\n")
    (plugin / "plugin.json").write_text(json.dumps({
        "id": PLUGIN_ID, "name": "Slow Start", "version": "0.1.0", "type": "service", "port": 8299,
        "config": {"default_enabled": True, "service_url": "http://127.0.0.1:8299",
                   "timeout": 5, "start_timeout": 1800},
        "endpoints": {"health": "/health"},
    }))
    return root


@pytest.fixture
def script(monkeypatch):
    state = {"result": None}
    monkeypatch.setattr(PluginManager, "_check_service_running", lambda self, metadata: False)
    monkeypatch.setattr(pm_module, "_run_plugin_script", lambda argv, cwd, timeout: state["result"])
    monkeypatch.setattr(pm_module, "time", SimpleNamespace(sleep=lambda s: None, time=time.time, monotonic=time.monotonic))
    return state


def _manager(plugins_dir):
    return PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))


def test_a_timed_out_start_reports_the_last_lines(plugins_dir, script, tmp_path, caplog):
    script["result"] = {
        "ok": False, "rc": -1, "error": "timeout after 1800s",
        "stdout": "Setting up Audio Foundry's Python environments\n"
                  f"Syncing {tmp_path}/plugins/audio_foundry/venv-music torch...\n",
        "stderr": "ERROR: Could not find a version that satisfies the requirement torch==9.9\n",
    }
    manager = _manager(plugins_dir)

    result = manager.start_plugin(PLUGIN_ID)

    assert result["success"] is False
    error = result["error"]
    assert error.startswith("Start script failed: timeout after 1800s. Last output:\n")
    assert "Could not find a version that satisfies the requirement torch==9.9" in error
    assert "./plugins/audio_foundry/venv-music" in error
    assert str(tmp_path) not in error
    assert "requirement torch==9.9" in caplog.text


def test_a_failed_health_wait_reports_the_script_output(plugins_dir, script, caplog):
    script["result"] = {
        "ok": True, "rc": 0, "stderr": "",
        "stdout": "Starting Audio Foundry...\nWaiting for health endpoint on port 8299...\n"
                  "Warning: Health endpoint not responsive after 30s\n",
    }
    manager = _manager(plugins_dir)

    result = manager.start_plugin(PLUGIN_ID)

    assert result["success"] is False
    assert result["error"].startswith("Plugin started but health check failed (timeout). Start script output:\n")
    assert "Health endpoint not responsive after 30s" in result["error"]
    assert "Health endpoint not responsive" in caplog.text


def test_no_output_keeps_the_plain_messages(plugins_dir, script):
    script["result"] = {"ok": False, "rc": -1, "error": "timeout after 1800s", "stdout": "", "stderr": ""}
    assert _manager(plugins_dir).start_plugin(PLUGIN_ID)["error"] == "Start script failed: timeout after 1800s"


def test_only_the_tail_is_kept():
    stdout = "".join(f"pip line {i}\n" for i in range(40))
    tail = pm_module._script_output_tail(stdout, "")
    assert tail.splitlines() == [f"pip line {i}" for i in range(25, 40)]
    assert len(pm_module._script_output_tail("x" * 5000, "")) == 2000


def test_timeout_output_decodes_the_bytes_subprocess_hands_back():
    assert timeout_output(b"Setting up \xe2\x80\x94 venv\n") == "Setting up — venv\n"
    assert timeout_output("already text") == "already text"
    assert timeout_output(None) == ""


def test_fallback_runner_keeps_what_a_timed_out_script_printed(monkeypatch):
    monkeypatch.setattr(PluginRunnerClient, "get", classmethod(lambda cls: SimpleNamespace(is_alive=lambda: False)))

    result = pm_module._run_plugin_script(
        argv=["bash", "-c", "echo 'building venv-music'; echo 'pip: oops' >&2; sleep 10"],
        cwd=".", timeout=1,
    )

    assert result["ok"] is False
    assert result["error"] == "timeout after 1s"
    assert result["stdout"] == "building venv-music\n"
    assert result["stderr"] == "pip: oops\n"


def test_sidecar_keeps_what_a_timed_out_script_printed(monkeypatch):
    # The sidecar's request loop, driven in-process: under pytest the real
    # sidecar process is replaced by a stand-in that refuses to run commands.
    from backend.services import plugin_runner

    request = {"id": 7, "cmd": "run", "argv": ["bash", "-c", "echo 'building venv-music'; sleep 10"],
               "cwd": ".", "timeout": 1}
    out = io.StringIO()
    monkeypatch.setattr(plugin_runner.sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    monkeypatch.setattr(plugin_runner.sys, "stdout", out)

    plugin_runner._sidecar_main()

    result = json.loads(out.getvalue())
    assert result["ok"] is False
    assert result["error"] == "timeout after 1s"
    assert result["stdout"] == "building venv-music\n"
