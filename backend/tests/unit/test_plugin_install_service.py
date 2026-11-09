"""A plugin's declared Install step: detection, status and the background run."""

import json
import time

import backend.services.plugin_install_service as svc


def _plugin(tmp_path, monkeypatch, script="echo done; mkdir -p bin; touch bin/tool"):
    d = tmp_path / "plugins" / "demo"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "setup.sh").write_text(script + "\n")
    (d / "plugin.json").write_text(json.dumps({
        "id": "demo",
        "install": {"script": "scripts/setup.sh", "installed_when": ["bin/tool"], "downloads": "nothing"},
    }))
    monkeypatch.setattr(svc, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(svc, "_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(svc, "_runs", {})
    return d


def test_no_install_block_means_no_install_step(tmp_path, monkeypatch):
    d = _plugin(tmp_path, monkeypatch)
    (d / "plugin.json").write_text(json.dumps({"id": "demo"}))
    assert svc.status("demo") is None
    assert svc.start("demo") == (None, "This plugin has no Install step")


def test_script_outside_the_plugin_is_refused(tmp_path, monkeypatch):
    d = _plugin(tmp_path, monkeypatch)
    (d / "plugin.json").write_text(json.dumps({"id": "demo", "install": {"script": "../../evil.sh"}}))
    (tmp_path / "evil.sh").write_text("true\n")
    assert svc.install_spec("demo") is None


def test_install_runs_and_reports_installed(tmp_path, monkeypatch):
    _plugin(tmp_path, monkeypatch)
    assert svc.status("demo")["state"] == "not_installed"
    status, refusal = svc.start("demo")
    assert refusal is None and status["state"] in ("installing", "installed")
    for _ in range(50):
        if svc.status("demo")["state"] != "installing":
            break
        time.sleep(0.1)
    final = svc.status("demo")
    assert final["state"] == "installed" and final["exit_code"] == 0
    assert "done" in final["log_tail"]


def test_failed_script_reports_failed(tmp_path, monkeypatch):
    _plugin(tmp_path, monkeypatch, script="echo boom; exit 3")
    svc.start("demo")
    for _ in range(50):
        if svc.status("demo")["state"] != "installing":
            break
        time.sleep(0.1)
    final = svc.status("demo")
    assert final["state"] == "failed" and final["exit_code"] == 3
