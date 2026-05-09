"""End-to-end: simulate the full Interconnector-sync recovery flow.

Builds a minimal repo, runs the reconciler twice (drift / no-drift),
and verifies state evolves correctly.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRY = REPO_ROOT / "scripts" / "dep_reconciler.py"


@pytest.fixture
def fake_repo(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("flask\n")
    (tmp_path / "data" / "dep_reconciler").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    return tmp_path


def _run(repo, state_file, **extra_env):
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "GUAARDVARK_DEP_STATE_FILE": str(state_file),
        # Force trust-on-upgrade so we don't actually run pip in tests.
        "GUAARDVARK_TRUST_ON_UPGRADE": "1",
        **extra_env,
    }
    return subprocess.run(
        [sys.executable, str(ENTRY), f"--repo-root={repo}"],
        env=env, cwd=str(repo), capture_output=True, text=True, timeout=30,
    )


def test_first_run_writes_state_via_trust_on_upgrade(fake_repo):
    state_file = fake_repo / "state.json"
    r = _run(fake_repo, state_file)
    assert r.returncode == 0, r.stderr
    state = json.loads(state_file.read_text())
    assert "backend_venv" in state["reconcilers"]


def test_second_run_no_drift_exits_zero(fake_repo):
    state_file = fake_repo / "state.json"
    r1 = _run(fake_repo, state_file)
    assert r1.returncode == 0

    r2 = _run(fake_repo, state_file)
    assert r2.returncode == 0
    assert "drift" not in r2.stdout.lower(), r2.stdout


def test_manifest_change_triggers_drift_log(fake_repo):
    state_file = fake_repo / "state.json"
    _run(fake_repo, state_file)

    # Mutate the manifest.
    (fake_repo / "backend" / "requirements.txt").write_text("flask\nrequests\n")

    # Don't enable trust-on-upgrade for the second run.
    r = _run(fake_repo, state_file, GUAARDVARK_TRUST_ON_UPGRADE="")
    # We don't have pip available in the test repo, so install will fail.
    # What we want to verify is the drift detection log line.
    assert "drift: backend_venv" in r.stdout
