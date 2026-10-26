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

def test_edit_on_dormant_module_succeeds(tmp_path, monkeypatch):
    """A module nothing imports classifies as 'dormant', which must remain
    editable so it can be wired up. Only 'archived' is blocked by Tier 2."""
    from backend.services import guarded_code_service as gcs

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "services" / "lonely_module.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n")

    # Force Tier 2 to actually run and report 'dormant' for our target so we
    # prove dormant is allowed, independent of how a real map would classify it.
    def fake_lifecycle(root):
        return {
            "backend.services.lonely_module": {
                "lifecycle": "dormant",
                "importers": 0,
                "path": "backend/services/lonely_module.py",
            }
        }

    monkeypatch.setattr(gcs, "_lifecycle_node_meta", fake_lifecycle)

    result = gcs.apply_exact_replacement(
        "backend/services/lonely_module.py", "VALUE = 1", "VALUE = 2"
    )

    assert target.read_text() == "VALUE = 2\n"
    assert result.verification["return_code"] == 0


# --- Tier 2 'archived' lifecycle is blocked (positive Tier-2 case) --------

def test_edit_on_archived_lifecycle_module_rejected(tmp_path, monkeypatch):
    from backend.services import guarded_code_service as gcs

    repo = _setup_repo(tmp_path, monkeypatch)
    # A path that is NOT a Tier-1 backup artifact, so only Tier 2 can block it.
    target = repo / "backend" / "services" / "retired.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")

    def fake_lifecycle(root):
        return {
            "backend.services.retired": {
                "lifecycle": "archived",
                "importers": 0,
                "path": "backend/services/retired.py",
            }
        }

    monkeypatch.setattr(gcs, "_lifecycle_node_meta", fake_lifecycle)

    with pytest.raises(gcs.GuardedCodeError) as exc:
        gcs.apply_exact_replacement("backend/services/retired.py", "x = 1", "x = 2")

    assert exc.value.code == "READONLY_LIFECYCLE"
    assert target.read_text() == "x = 1\n"


# --- Tier 2 FAILS OPEN when the analyzer raises ---------------------------

def test_tier2_fails_open_when_codebase_map_raises(tmp_path, monkeypatch):
    from backend.services import guarded_code_service as gcs

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "services" / "active_thing.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")
    # Ensure no stale cache lets the build be skipped.
    gcs.invalidate_lifecycle_cache()

    import backend.services.system_mapper as sm

    def boom(*a, **k):
        raise RuntimeError("analyzer exploded")

    monkeypatch.setattr(sm, "codebase_map", boom)

    # Active, non-backup file: Tier 1 passes, Tier 2 raises -> fail open -> edit applies.
    result = gcs.apply_exact_replacement(
        "backend/services/active_thing.py", "x = 1", "x = 2"
    )

    assert target.read_text() == "x = 2\n"
    assert result.verification["return_code"] == 0


# --- Tier 1 needs no map: .BACK rejected even if map would crash ----------

def test_tier1_blocks_without_running_map(tmp_path, monkeypatch):
    from backend.services import guarded_code_service as gcs

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "services" / "thing.py.BACK"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")
    gcs.invalidate_lifecycle_cache()

    import backend.services.system_mapper as sm

    def boom(*a, **k):
        raise AssertionError("codebase_map must not be called for Tier-1 block")

    monkeypatch.setattr(sm, "codebase_map", boom)

    with pytest.raises(gcs.GuardedCodeError) as exc:
        gcs.apply_exact_replacement("backend/services/thing.py.BACK", "x = 1", "x = 2")

    assert exc.value.code == "READONLY_LIFECYCLE"
    assert target.read_text() == "x = 1\n"


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
