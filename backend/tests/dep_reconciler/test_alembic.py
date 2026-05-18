from unittest.mock import patch

import pytest

from scripts.dep_reconciler.reconcilers.alembic import Alembic


@pytest.fixture
def fake_repo(tmp_path):
    (tmp_path / "backend" / "migrations" / "versions").mkdir(parents=True)
    (tmp_path / "backend" / "migrations" / "versions" / "001_init.py").write_text("# noop\n")
    (tmp_path / "backend" / "migrations" / "alembic.ini").write_text("[alembic]\nscript_location = .\n")
    return tmp_path


def test_id(fake_repo):
    assert Alembic(fake_repo).id == "alembic"


def test_inactive_when_alembic_module_missing(fake_repo):
    r = Alembic(fake_repo)
    with patch.object(r, "_alembic_importable", return_value=False):
        assert not r.is_active()


def test_active_when_alembic_module_present(fake_repo):
    r = Alembic(fake_repo)
    with patch.object(r, "_alembic_importable", return_value=True), \
         patch.object(r, "_db_reachable", return_value=True):
        assert r.is_active()


def test_inactive_when_db_unreachable(fake_repo):
    r = Alembic(fake_repo)
    with patch.object(r, "_alembic_importable", return_value=True), \
         patch.object(r, "_db_reachable", return_value=False):
        assert not r.is_active()


def test_compute_hash_changes_when_versions_change(fake_repo):
    r = Alembic(fake_repo)
    with patch.object(r, "_alembic_importable", return_value=True):
        # _db_reachable doesn't matter for compute_hash; we don't call is_active
        h1 = r.compute_hash()
        (fake_repo / "backend" / "migrations" / "versions" / "002_next.py").write_text("# next\n")
        h2 = r.compute_hash()
    assert h1 != h2


def test_compute_hash_changes_when_models_py_changes(fake_repo):
    """models.py edits MUST trigger drift — under the schema-sync policy,
    models.py is the source of truth and migrations may not be authored.
    """
    (fake_repo / "backend" / "models.py").write_text("# v1\n")
    r = Alembic(fake_repo)
    h1 = r.compute_hash()
    (fake_repo / "backend" / "models.py").write_text("# v1\nclass NewTable: pass\n")
    h2 = r.compute_hash()
    assert h1 != h2


def test_compute_hash_stable_when_nothing_changes(fake_repo):
    """Running compute_hash twice on identical state must return the same value."""
    (fake_repo / "backend" / "models.py").write_text("# v1\n")
    r = Alembic(fake_repo)
    assert r.compute_hash() == r.compute_hash()


def test_extra_state_returns_alembic_current(fake_repo):
    r = Alembic(fake_repo)
    with patch.object(r, "_alembic_current", return_value="abc123"):
        assert r.extra_state()["alembic_head"] == "abc123"


def test_install_invokes_schema_sync(fake_repo, tmp_path):
    """install() must invoke scripts/schema_sync.py, NOT alembic upgrade head.
    
    The codebase uses a single-master-migration policy: schema_sync.py is the
    only tool authorized to mutate the DB schema. alembic upgrade head is
    deprecated.
    """
    # The fake_repo fixture creates backend/migrations/alembic.ini etc.
    # We also need scripts/schema_sync.py to exist for the install path to fire.
    (fake_repo / "scripts").mkdir(exist_ok=True)
    (fake_repo / "scripts" / "schema_sync.py").write_text("#!/usr/bin/env python3\n")
    
    r = Alembic(fake_repo)
    with patch.object(r, "_run_subprocess", return_value=0) as m:
        rc = r.install(tmp_path / "log.txt")
    assert rc == 0
    args = m.call_args_list[0].args[0]
    # Must be invoking schema_sync.py, not alembic
    assert any("schema_sync.py" in str(a) for a in args), f"expected schema_sync.py in args, got {args}"
    # Must NOT be using alembic upgrade head
    assert "upgrade" not in args
    assert "head" not in args


def test_install_fails_clearly_when_schema_sync_missing(fake_repo, tmp_path):
    """If schema_sync.py is missing, install() should return non-zero
    rather than silently succeeding."""
    # Don't create scripts/schema_sync.py
    r = Alembic(fake_repo)
    rc = r.install(tmp_path / "log.txt")
    assert rc != 0
