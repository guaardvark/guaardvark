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


def test_extra_state_returns_alembic_current(fake_repo):
    r = Alembic(fake_repo)
    with patch.object(r, "_alembic_current", return_value="abc123"):
        assert r.extra_state()["alembic_head"] == "abc123"


def test_install_runs_alembic_upgrade_head(fake_repo, tmp_path):
    r = Alembic(fake_repo)
    with patch.object(r, "_run_subprocess", return_value=0) as m:
        rc = r.install(tmp_path / "log.txt")
    assert rc == 0
    args = m.call_args_list[0].args[0]
    assert "alembic" in args  # python -m alembic ...
    assert "upgrade" in args
    assert "head" in args
