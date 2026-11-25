"""A CUDA_VISIBLE_DEVICES the launcher set survives importing the indexing
modules, which otherwise pick the first card whenever nvidia-smi answers."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

PROBE = """
import subprocess, sys
real_run = subprocess.run
def run(cmd, *a, **k):
    if list(cmd)[:1] == ["nvidia-smi"]:
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    return real_run(cmd, *a, **k)
subprocess.run = run
import os
import {module}
print("VISIBLE=" + repr(os.environ.get("CUDA_VISIBLE_DEVICES")))
"""


def _visible_after_import(module, preset):
    env = {k: v for k, v in os.environ.items() if k not in ("CUDA_VISIBLE_DEVICES", "CELERY_WORKER_MODE")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    if preset is not None:
        env["CUDA_VISIBLE_DEVICES"] = preset
    done = subprocess.run([sys.executable, "-c", PROBE.format(module=module)], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=180)
    lines = [line for line in done.stdout.splitlines() if line.startswith("VISIBLE=")]
    assert lines, done.stderr[-800:]
    return lines[-1][len("VISIBLE="):]


@pytest.mark.parametrize("preset, expected", [("", "''"), ("1", "'1'"), (None, "'0'")])
def test_llama_index_config_keeps_a_set_value(preset, expected):
    assert _visible_after_import("backend.utils.llama_index_local_config", preset) == expected
