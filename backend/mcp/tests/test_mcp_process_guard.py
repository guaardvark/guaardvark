"""The MCP server process stays Flask-free: backend.app refuses to import there."""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_backend_app_refuses_to_import_in_the_mcp_process():
    env = dict(os.environ, GUAARDVARK_MCP_PROCESS="1")
    proc = subprocess.run(
        [sys.executable, "-c", "import backend.app"],
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode != 0
    assert "cannot be imported inside the MCP server process" in proc.stderr
