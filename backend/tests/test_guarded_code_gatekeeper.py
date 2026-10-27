"""Tests for the Mutability Gatekeeper layered onto guarded_code_service.

Mirrors the style of test_guarded_code_service.py: per-test tmp repo, sets
GUAARDVARK_ROOT + GUAARDVARK_MODE=test, no DB / app context required for the
apply path. Every guard exercises its negative case.
"""
from pathlib import Path

import pytest


def _setup_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")
    return repo


# --- Tier 1: backup-artifact paths are hard-blocked -----------------------

@pytest.mark.parametrize(
    "rel_path",
    [
        "backend/services/thing.py.BACK",
        "backend/api/backs/old_api.py",
        "backend/api/_archive/dead_api.py",
    ],
)
def test_edit_rejected_on_backup_artifact_paths(tmp_path, monkeypatch, rel_path):
    from backend.services.guarded_code_service import (
        GuardedCodeError,
        apply_exact_replacement,
    )

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n")

    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement(rel_path, "x = 1", "x = 2")

    assert exc.value.code == "READONLY_LIFECYCLE"
    assert exc.value.status_code == 403
    # File must be untouched.
    assert target.read_text() == "x = 1\n"
    # No backup should have been written either (rejection precedes backup).
    assert not (repo / (rel_path + ".backup")).exists()


# --- HAPPY PATH regression: normal active file still edits ----------------

def test_edit_on_normal_active_file_succeeds(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import apply_exact_replacement

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "services" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")

    result = apply_exact_replacement("backend/services/example.py", "x = 1", "x = 2")

    assert target.read_text() == "x = 2\n"
    assert Path(result.backup_path).exists()
    assert result.verification["return_code"] == 0


# --- Dormant / unrouted module is NOT blocked -----------------------------

def _no_map(monkeypatch):
    """Make any system-map build fail the test: the gate is a path check."""
    import backend.services.system_mapper as sm
    import backend.services.system_mapper.core as core

    def boom(*a, **k):
        raise AssertionError("the edit gate must not build a system map")

    monkeypatch.setattr(sm, "codebase_map", boom)
    monkeypatch.setattr(core, "codebase_map", boom)


def test_edit_on_dormant_module_succeeds(tmp_path, monkeypatch):
    """A module nothing imports is 'dormant' to the mapper and must remain
    editable so it can be wired up."""
    from backend.services import guarded_code_service as gcs

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "services" / "lonely_module.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n")
    _no_map(monkeypatch)

    result = gcs.apply_exact_replacement(
        "backend/services/lonely_module.py", "VALUE = 1", "VALUE = 2"
    )

    assert target.read_text() == "VALUE = 2\n"
    assert result.verification["return_code"] == 0


# --- The gate never builds a map --------------------------------------------

def test_normal_edit_does_not_build_a_system_map(tmp_path, monkeypatch):
    from backend.services import guarded_code_service as gcs

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "services" / "active_thing.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")
    _no_map(monkeypatch)

    result = gcs.apply_exact_replacement(
        "backend/services/active_thing.py", "x = 1", "x = 2"
    )

    assert target.read_text() == "x = 2\n"
    assert result.verification["return_code"] == 0


def test_backup_artifact_blocked_without_running_map(tmp_path, monkeypatch):
    from backend.services import guarded_code_service as gcs

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "services" / "thing.py.BACK"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")
    _no_map(monkeypatch)

    with pytest.raises(gcs.GuardedCodeError) as exc:
        gcs.apply_exact_replacement("backend/services/thing.py.BACK", "x = 1", "x = 2")

    assert exc.value.code == "READONLY_LIFECYCLE"


def test_reason_matches_the_mappers_archived_lifecycle():
    """The gate and the map agree on what is archived, by construction."""
    from backend.services.guarded_code_service import readonly_lifecycle_reason
    from backend.services.system_mapper.dependency_graph import _lifecycle_for

    for rel in ("backend/x.py.BACK", "backend/api/_archive/a.py", "backend/backs/b.py",
                "backend/services/ok.py"):
        archived = _lifecycle_for(rel, {"someone"}) == "archived"
        assert bool(readonly_lifecycle_reason(rel)) == archived




# --- Order: PROTECTED_FILE wins over READONLY_LIFECYCLE -------------------

def test_protected_file_beats_readonly_lifecycle(tmp_path, monkeypatch):
    from backend.services import guarded_code_service as gcs
    from backend.services.guarded_code_service import (
        GuardedCodeError,
        apply_exact_replacement,
    )

    repo = _setup_repo(tmp_path, monkeypatch)

    # A real protected file. The protected-file check runs before the
    # lifecycle gate, so the error code must be PROTECTED_FILE (order preserved).
    from backend.config import PROTECTED_FILES

    assert PROTECTED_FILES, "expected some PROTECTED_FILES configured"
    protected_rel = sorted(PROTECTED_FILES)[0].replace("\\", "/").strip("/")
    target = repo / protected_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n")

    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement(protected_rel, "x = 1", "x = 2")

    assert exc.value.code == "PROTECTED_FILE"
    assert target.read_text() == "x = 1\n"


# --- Dry-run reports the rejection (check runs before dry_run branch) ------

def test_dry_run_also_reports_readonly_rejection(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import (
        GuardedCodeError,
        apply_exact_replacement,
    )

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "api" / "backs" / "old_api.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")

    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement(
            "backend/api/backs/old_api.py", "x = 1", "x = 2", dry_run=True
        )

    assert exc.value.code == "READONLY_LIFECYCLE"


# --- stage_pending_fix rejects a backup-artifact path (Tier 1) ------------

def test_stage_pending_fix_rejects_backup_artifact(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import GuardedCodeError, stage_pending_fix

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "services" / "thing.py.BACK"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")

    with pytest.raises(GuardedCodeError) as exc:
        stage_pending_fix(
            "backend/services/thing.py.BACK", "x = 1", "x = 2", "fix it"
        )

    assert exc.value.code == "READONLY_LIFECYCLE"
    assert exc.value.status_code == 403
