from unittest.mock import patch

import pytest

from scripts.dep_reconciler.reconcilers.frontend import Frontend


@pytest.fixture
def fake_repo(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text('{"name":"x"}')
    (tmp_path / "frontend" / "package-lock.json").write_text('{"lockfileVersion":3}')
    return tmp_path


def test_id(fake_repo):
    assert Frontend(fake_repo).id == "frontend"


def test_manifests_only_lockfile(fake_repo):
    """package.json edits without lockfile regen don't trigger reinstall."""
    paths = [p.name for p in Frontend(fake_repo).manifests()]
    assert paths == ["package-lock.json"]


def test_inactive_when_lockfile_missing(tmp_path):
    (tmp_path / "frontend").mkdir()
    assert not Frontend(tmp_path).is_active()


def test_compute_hash_tracks_lockfile(fake_repo):
    r = Frontend(fake_repo)
    h1 = r.compute_hash()
    (fake_repo / "frontend" / "package-lock.json").write_text('{"lockfileVersion":3,"x":1}')
    h2 = r.compute_hash()
    assert h1 != h2


def test_install_runs_npm_ci(fake_repo, tmp_path):
    """npm ci is lockfile-strict — won't silently rewrite package-lock.json."""
    r = Frontend(fake_repo)
    with patch.object(r, "_run_subprocess", return_value=0) as m:
        rc = r.install(tmp_path / "log.txt")
    assert rc == 0
    args = m.call_args_list[0].args[0]
    assert args[:2] == ["npm", "ci"]
