"""/swarm/cleanup accepts only a swarm_id that is one folder under the worktree base."""

import subprocess

import pytest
from fastapi.testclient import TestClient

import service.app as app_module
from service.app import app

_SECRET = "test-secret-cleanup"
_HDRS = {"X-Swarm-Internal-Token": _SECRET}


@pytest.fixture
def client(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    # A manifest at the repo root: what load_existing(repo, "..") would read.
    (repo / "manifest.json").write_text('{"swarm_id": "x", "worktrees": {}}')
    (repo / ".swarm-worktrees").mkdir()
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("SWARM_INTERNAL_SECRET", _SECRET)
    monkeypatch.setattr(app_module, "_internal_secret", _SECRET, raising=False)
    monkeypatch.setattr(app_module, "_active_orchestrators", {}, raising=True)
    return TestClient(app, base_url="http://127.0.0.1:8210"), repo


@pytest.mark.parametrize("swarm_id", ["..", ".", "a/b", "../../etc", "/etc", "x/..", "x/../.."])
def test_cleanup_refuses_ids_outside_one_folder(client, swarm_id):
    c, repo = client
    resp = c.post("/swarm/cleanup", json={"swarm_id": swarm_id, "repo_path": str(repo)}, headers=_HDRS)
    assert resp.status_code == 400
    assert (repo / "manifest.json").exists()


def test_cleanup_of_an_unknown_real_id_is_not_found(client):
    c, repo = client
    resp = c.post("/swarm/cleanup", json={"swarm_id": "swarm-nope", "repo_path": str(repo)}, headers=_HDRS)
    assert resp.status_code == 404
