import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRY = REPO_ROOT / "scripts" / "dep_reconciler.py"


def _run_entry(env_overrides: dict, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_overrides, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, str(ENTRY), f"--repo-root={cwd}"],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_min_repo(tmp_path):
    """A minimal repo with no manifests — every reconciler is inactive."""
    (tmp_path / "data" / "dep_reconciler").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    return tmp_path


def test_first_run_with_nothing_to_reconcile_exits_zero(tmp_path):
    repo = _make_min_repo(tmp_path)
    state_file = repo / "state.json"
    r = _run_entry(
        {"GUAARDVARK_DEP_STATE_FILE": str(state_file)},
        cwd=repo,
    )
    assert r.returncode == 0, r.stderr
    # State file written even when nothing to do.
    assert state_file.is_file()


def test_kill_switch_skips_everything(tmp_path):
    repo = _make_min_repo(tmp_path)
    state_file = repo / "state.json"
    r = _run_entry(
        {
            "GUAARDVARK_DEP_RECONCILER": "disabled",
            "GUAARDVARK_DEP_STATE_FILE": str(state_file),
        },
        cwd=repo,
    )
    assert r.returncode == 0
    # No state file written when disabled.
    assert not state_file.is_file()


def test_sync_sentinel_aborts_with_exit_2(tmp_path):
    repo = _make_min_repo(tmp_path)
    sentinel = repo / "data" / "dep_reconciler" / ".sync_in_progress"
    sentinel.write_text("syncing")
    state_file = repo / "state.json"
    r = _run_entry(
        {"GUAARDVARK_DEP_STATE_FILE": str(state_file)},
        cwd=repo,
    )
    assert r.returncode == 2
    assert "sync in progress" in (r.stdout + r.stderr).lower()


def test_orphan_state_entry_pruned(tmp_path):
    """A state entry for a reconciler that's no longer active should be removed."""
    repo = _make_min_repo(tmp_path)
    state_file = repo / "state.json"
    state_file.write_text(json.dumps({
        "version": 1, "hostname": "test", "updated_at": "",
        "reconcilers": {
            "plugin:ghost": {"manifest_hash": "sha256:dead", "extra": {}}
        }
    }))
    r = _run_entry(
        {"GUAARDVARK_DEP_STATE_FILE": str(state_file)},
        cwd=repo,
    )
    assert r.returncode == 0
    state = json.loads(state_file.read_text())
    assert "plugin:ghost" not in state["reconcilers"]


def test_dry_run_reports_drift_but_writes_no_state(tmp_path):
    """--dry-run must NOT write state even when drift is detected."""
    repo = _make_min_repo(tmp_path)
    (repo / "backend").mkdir()
    (repo / "backend" / "requirements.txt").write_text("flask\n")
    state_file = repo / "state.json"
    r = subprocess.run(
        [sys.executable, str(ENTRY), f"--repo-root={repo}", "--dry-run"],
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT),
             "GUAARDVARK_DEP_STATE_FILE": str(state_file)},
        cwd=str(repo), capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "drift" in r.stdout.lower(), r.stdout
    assert "dry-run" in r.stdout.lower()
    assert not state_file.is_file(), "dry-run must not write state"


def test_only_does_not_prune_other_plugin_state(tmp_path):
    """--only=plugin_bundle must NOT delete plugin:foo entries when run with no plugins enabled.

    Regression guard for the bug where --only triggered orphan pruning that
    deleted state entries for reconcilers it didn't look at.
    """
    repo = _make_min_repo(tmp_path)
    state_file = repo / "state.json"
    state_file.write_text(json.dumps({
        "version": 1, "hostname": "test", "updated_at": "",
        "reconcilers": {
            "backend_venv": {"manifest_hash": "sha256:keep", "extra": {}},
            "plugin:discord": {"manifest_hash": "sha256:keep", "extra": {}},
        }
    }))
    r = subprocess.run(
        [sys.executable, str(ENTRY), f"--repo-root={repo}", "--only=plugin_bundle"],
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT),
             "GUAARDVARK_DEP_STATE_FILE": str(state_file)},
        cwd=str(repo), capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    state = json.loads(state_file.read_text())
    # Both entries must survive the partial run.
    assert "backend_venv" in state["reconcilers"]
    assert "plugin:discord" in state["reconcilers"]
