"""`doctor --json` and `install --json`: one {status, data} document on stdout.

Client configs live in a scratch home; the server build and stdio round-trip
are stubbed so no tool registry is loaded and no server is spawned."""
import json

import pytest

from backend.mcp import doctor, installer


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "checkout"
    home.mkdir()
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(installer, "_project_root", lambda: root)
    monkeypatch.setattr(doctor, "_project_root", lambda: root)
    monkeypatch.setattr(doctor, "_check_build", lambda: doctor._report(doctor._PASS, "build_server()", "stub"))
    monkeypatch.setattr(doctor, "_check_stdio", lambda: doctor._report(doctor._PASS, "stdio round-trip", "stub"))
    return home


def test_doctor_report_collects_checks_instead_of_printing(scratch, capsys):
    code, report = doctor.doctor_report()
    assert capsys.readouterr().out == ""
    assert code == 0 and report["status"] == "success"
    sections = {(c["section"], c["name"]) for c in report["data"]["checks"]}
    assert ("server", "build_server()") in sections
    assert ("clients", "client configs") in sections
    assert report["data"]["failed"] == []


def test_doctor_report_names_a_dead_client_entry(scratch):
    cursor = scratch / ".cursor" / "mcp.json"
    cursor.parent.mkdir()
    cursor.write_text(json.dumps({"mcpServers": {"guaardvark": {"command": "/gone/python", "args": []}}}))
    code, report = doctor.doctor_report()
    assert code == 1
    assert report["error"]["code"] == "CHECKS_FAILED"
    assert report["data"]["failed"] == ["client: cursor (user)"]
    failed = [c for c in report["data"]["checks"] if not c["ok"]]
    assert failed[0]["status"] == "fail" and "missing path" in failed[0]["detail"]


def test_doctor_text_output_is_unchanged(scratch, capsys):
    assert doctor.run_doctor() == 0
    out = capsys.readouterr().out
    assert out.startswith("Guaardvark MCP doctor\n")
    assert "[PASS] build_server() — stub" in out


def test_install_report_dry_run_writes_nothing(scratch):
    (scratch / ".cursor").mkdir()
    code, report = installer.install_report(["cursor"], dry_run=True)
    assert code == 0 and report["status"] == "success"
    row = report["data"]["results"][0]
    assert row["client"] == "cursor" and row["status"] == "dry-run"
    assert report["data"]["restart_needed"] is False
    assert not (scratch / ".cursor" / "mcp.json").exists()


def test_install_report_says_what_it_wrote(scratch):
    (scratch / ".cursor").mkdir()
    code, report = installer.install_report(["cursor"])
    assert code == 0
    assert report["data"]["results"][0]["status"] == "installed"
    assert report["data"]["restart_needed"] is True
    written = json.loads((scratch / ".cursor" / "mcp.json").read_text())
    assert "guaardvark" in written["mcpServers"]

    code, again = installer.install_report(["cursor"])
    assert again["data"]["results"][0]["status"] == "unchanged"


def test_install_report_with_no_client_is_an_error(scratch, monkeypatch):
    monkeypatch.setattr(installer, "_detect", lambda client: False)
    code, report = installer.install_report(None)
    assert code == 1
    assert report["error"]["code"] == "NO_CLIENTS"


def test_install_report_failed_client(scratch):
    cursor = scratch / ".cursor" / "mcp.json"
    cursor.parent.mkdir()
    cursor.write_text("// not json\n")
    code, report = installer.install_report(["cursor"])
    assert code == 1
    assert report["error"]["code"] == "INSTALL_FAILED"
    assert report["data"]["failed"] == ["cursor"]
    assert cursor.read_text() == "// not json\n"


def test_main_prints_only_the_report_on_stdout(monkeypatch, capfd):
    import logging

    monkeypatch.setenv("GUAARDVARK_MCP_PROCESS", "1")
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", root.handlers[:])
    monkeypatch.setattr(root, "level", root.level)
    from backend.mcp import __main__ as entry

    def noisy_report(call=False):
        print("tool registry banner")
        return 0, {"status": "success", "data": {"checks": [], "failed": []}}

    monkeypatch.setattr(doctor, "doctor_report", noisy_report)
    assert entry.main(["doctor", "--json"]) == 0
    out, err = capfd.readouterr()
    assert json.loads(out)["status"] == "success"
    assert "tool registry banner" in err
