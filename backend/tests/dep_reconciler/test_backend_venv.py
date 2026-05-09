from unittest.mock import patch

import pytest

from scripts.dep_reconciler.reconcilers.backend_venv import BackendVenv


@pytest.fixture
def fake_repo(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements-base.txt").write_text("flask==3.0\n")
    (tmp_path / "backend" / "requirements.txt").write_text("alembic\n")
    return tmp_path


def test_id_is_stable(fake_repo):
    assert BackendVenv(fake_repo).id == "backend_venv"


def test_manifests_lists_both_requirements(fake_repo):
    r = BackendVenv(fake_repo)
    paths = [p.name for p in r.manifests()]
    assert paths == ["requirements-base.txt", "requirements.txt"]


def test_compute_hash_changes_when_manifest_changes(fake_repo):
    r = BackendVenv(fake_repo)
    h1 = r.compute_hash()
    (fake_repo / "backend" / "requirements.txt").write_text("alembic\nrequests\n")
    h2 = r.compute_hash()
    assert h1 != h2


def test_extra_state_includes_numpy_major_when_installed(fake_repo):
    r = BackendVenv(fake_repo)
    with patch.object(r, "_pip_show", return_value="Version: 2.1.3"):
        assert r.extra_state()["numpy_major"] == 2


def test_extra_state_omits_numpy_major_when_not_installed(fake_repo):
    r = BackendVenv(fake_repo)
    with patch.object(r, "_pip_show", return_value=None):
        assert "numpy_major" not in r.extra_state()


def test_install_runs_pip_install(fake_repo, tmp_path):
    r = BackendVenv(fake_repo)
    log = tmp_path / "log.txt"
    with patch.object(r, "_run_subprocess", return_value=0) as m:
        rc = r.install(log)
    assert rc == 0
    # First call should be pip install -r requirements-base.txt -r requirements.txt
    args = m.call_args_list[0].args[0]
    assert "pip" in args
    assert "install" in args
    assert "-r" in args


def test_install_returns_nonzero_when_pip_fails(fake_repo, tmp_path):
    r = BackendVenv(fake_repo)
    with patch.object(r, "_run_subprocess", return_value=1):
        assert r.install(tmp_path / "log.txt") == 1
