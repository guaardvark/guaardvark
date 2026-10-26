"""Guaardvark's edits to its own code: restore point, checks, undo, revert,
and the start guard that puts back a change the backend could not start with.

Each test works in a small fake install under tmp_path (GUAARDVARK_ROOT and
GUAARDVARK_BACKUP_DIR point there); tests are not run by the checks here.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[2] / "scripts" / "self_edit_guard.py"


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "install"
    (root / "backend" / "services").mkdir(parents=True)
    (root / "backend" / "__init__.py").write_text("")
    (root / "backend" / "services" / "__init__.py").write_text("")
    (root / "backend" / "services" / "knobs.py").write_text("LIMIT = 8\nNAME = 'x'\n")
    (root / "frontend").mkdir()
    (root / "frontend" / "data.json").write_text('{"a": 1}\n')
    monkeypatch.setenv("GUAARDVARK_ROOT", str(root))
    backups = tmp_path / "backups"
    import backend.config as config
    monkeypatch.setattr(config, "BACKUP_DIR", str(backups), raising=False)
    from backend.services import self_edit
    monkeypatch.setattr(self_edit, "tests_enabled", lambda: False)
    monkeypatch.setattr(self_edit, "repo_root", lambda: root.resolve())
    monkeypatch.setattr("backend.services.guarded_code_service.default_repo_root", lambda: root)
    monkeypatch.setattr("backend.services.guarded_code_service.is_codebase_locked", lambda: False)
    monkeypatch.setattr("backend.services.guarded_code_service._inbound_gate", lambda *a, **k: None)
    return root, backups, self_edit


def test_a_passing_change_is_kept_and_can_be_reverted(install, monkeypatch):
    root, _backups, se = install
    monkeypatch.setattr(se, "_import_check", lambda modules: None)
    target = root / "backend" / "services" / "knobs.py"
    record = se.apply_change(str(target), "LIMIT = 8", "LIMIT = 9", group="g1")
    assert record["status"] == "applied" and record["restart_needed"]
    assert "LIMIT = 9" in target.read_text()
    assert json.loads(se.pending_path().read_text())["edits"] == [record["id"]]
    result = se.revert("g1")
    assert result["reverted"] == [record["id"]]
    assert target.read_text() == "LIMIT = 8\nNAME = 'x'\n"


def test_a_change_that_fails_a_check_is_undone(install, monkeypatch):
    root, _backups, se = install
    monkeypatch.setattr(se, "_import_check", lambda modules: "ImportError: no module named nope")
    target = root / "backend" / "services" / "knobs.py"
    record = se.apply_change(str(target), "LIMIT = 8", "LIMIT = nope.value", group="g2")
    assert record["status"] == "failed_checks"
    assert target.read_text() == "LIMIT = 8\nNAME = 'x'\n"
    assert not se.pending_path().exists()


def test_no_near_match_for_own_code(install):
    root, _backups, se = install
    from backend.services.guarded_code_service import GuardedCodeError
    with pytest.raises(GuardedCodeError):
        se.apply_change(str(root / "backend" / "services" / "knobs.py"), "LIMIT  =  8 ", "LIMIT = 9", group="g3")
    assert not any((se.store_dir()).glob("*/manifest.json")) if se.store_dir().exists() else True


def test_revert_asks_first_when_the_file_changed_again(install, monkeypatch):
    root, _backups, se = install
    monkeypatch.setattr(se, "_import_check", lambda modules: None)
    target = root / "backend" / "services" / "knobs.py"
    se.apply_change(str(target), "LIMIT = 8", "LIMIT = 9", group="g4")
    target.write_text(target.read_text().replace("LIMIT = 9", "LIMIT = 10"))
    assert se.revert("g4")["conflicts"] == ["backend/services/knobs.py"]
    assert "LIMIT = 10" in target.read_text()
    assert se.revert("g4", force=True)["reverted"]
    assert "LIMIT = 8" in target.read_text()


def test_a_frontend_change_never_waits_for_a_start(install):
    root, _backups, se = install
    record = se.apply_change(str(root / "frontend" / "data.json"), '"a": 1', '"a": 2', group="g5")
    assert record["status"] == "applied" and not record["restart_needed"]
    assert not se.pending_path().exists()


def test_boot_confirms_pending_changes(install, monkeypatch):
    root, _backups, se = install
    monkeypatch.setattr(se, "_import_check", lambda modules: None)
    record = se.apply_change(str(root / "backend" / "services" / "knobs.py"), "LIMIT = 8", "LIMIT = 9", group="g6")
    assert se.confirm_boot() == 1
    assert se.load(record["id"])["status"] == "live"
    assert not se.pending_path().exists()


def test_the_start_guard_puts_back_a_pending_change(install, monkeypatch, tmp_path):
    root, backups, se = install
    monkeypatch.setattr(se, "_import_check", lambda modules: None)
    target = root / "backend" / "services" / "knobs.py"
    record = se.apply_change(str(target), "LIMIT = 8", "LIMIT = 9", group="g7")
    (root / "scripts").mkdir()
    guard = root / "scripts" / "self_edit_guard.py"
    guard.write_text(GUARD.read_text())
    out = subprocess.run([sys.executable, str(guard), "restore-pending", "--reason", "test"],
                         env={"GUAARDVARK_BACKUP_DIR": str(backups), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert target.read_text() == "LIMIT = 8\nNAME = 'x'\n"
    assert se.load(record["id"])["status"] == "auto_reverted"
    again = subprocess.run([sys.executable, str(guard), "restore-pending"],
                           env={"GUAARDVARK_BACKUP_DIR": str(backups), "PATH": "/usr/bin:/bin"})
    assert again.returncode == 1


def test_the_start_guard_leaves_another_install_alone(install, monkeypatch, tmp_path):
    root, backups, se = install
    monkeypatch.setattr(se, "_import_check", lambda modules: None)
    se.apply_change(str(root / "backend" / "services" / "knobs.py"), "LIMIT = 8", "LIMIT = 9", group="g8")
    other = tmp_path / "other" / "scripts"
    other.mkdir(parents=True)
    (other / "self_edit_guard.py").write_text(GUARD.read_text())
    out = subprocess.run([sys.executable, str(other / "self_edit_guard.py"), "restore-pending"],
                         env={"GUAARDVARK_BACKUP_DIR": str(backups), "PATH": "/usr/bin:/bin"})
    assert out.returncode == 1
    assert "LIMIT = 9" in (root / "backend" / "services" / "knobs.py").read_text()
