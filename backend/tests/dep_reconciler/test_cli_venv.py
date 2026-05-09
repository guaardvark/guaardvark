from unittest.mock import patch

import pytest

from scripts.dep_reconciler.reconcilers.cli_venv import CliVenv


@pytest.fixture
def fake_repo(tmp_path):
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "requirements.txt").write_text("click\n")
    (tmp_path / "cli" / "setup.py").write_text("from setuptools import setup\nsetup(name='llx')\n")
    return tmp_path


def test_id(fake_repo):
    assert CliVenv(fake_repo).id == "cli_venv"


def test_inactive_when_no_setup_py(tmp_path):
    (tmp_path / "cli").mkdir()
    assert not CliVenv(tmp_path).is_active()


def test_active_when_setup_py_present(fake_repo):
    assert CliVenv(fake_repo).is_active()


def test_compute_hash_tracks_both_files(fake_repo):
    r = CliVenv(fake_repo)
    h1 = r.compute_hash()
    (fake_repo / "cli" / "requirements.txt").write_text("click\nrich\n")
    h2 = r.compute_hash()
    assert h1 != h2


def test_install_runs_pip_install_editable(fake_repo, tmp_path):
    r = CliVenv(fake_repo)
    with patch.object(r, "_run_subprocess", return_value=0) as m:
        rc = r.install(tmp_path / "log.txt")
    assert rc == 0
    args = m.call_args_list[0].args[0]
    assert "install" in args
    assert "-e" in args
