import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRY = REPO_ROOT / "scripts" / "dep_reconciler.py"


def test_trust_on_upgrade_does_not_run_installers(tmp_path):
    """No state file + existing venv-marker → write state without re-installing."""
    # Build a minimal repo with one active reconciler (BackendVenv).
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("flask\n")
    (tmp_path / "backend" / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "backend" / "venv" / "bin" / "flask").write_text("#!/bin/sh\n")  # marker
    (tmp_path / "data" / "dep_reconciler").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    state_file = tmp_path / "state.json"

    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "GUAARDVARK_DEP_STATE_FILE": str(state_file),
        "GUAARDVARK_TRUST_ON_UPGRADE": "1",
    }
    r = subprocess.run(
        [sys.executable, str(ENTRY), f"--repo-root={tmp_path}"],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    state = json.loads(state_file.read_text())
    # State entry was written for backend_venv WITHOUT triggering pip install.
    assert "backend_venv" in state["reconcilers"]
    # Crucial: our subprocess output should NOT contain "installing: backend_venv"
    assert "installing: backend_venv" not in (r.stdout + r.stderr)
    assert "trust-on-upgrade" in (r.stdout + r.stderr).lower()
