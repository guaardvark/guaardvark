import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.slow
def test_setup_env_and_start(tmp_path):
    if not os.environ.get("GUAARDVARK_FULL_TESTS"):
        pytest.skip("Optional slow test; set GUAARDVARK_FULL_TESTS=1 to run")

    repo_root = Path(__file__).resolve().parents[2]
    work_dir = tmp_path / "guaardvark"
    shutil.copytree(repo_root, work_dir)

    setup_script = work_dir / "devtools" / "setup_env.sh"
    subprocess.run([str(setup_script), "--with-llm"], cwd=work_dir, check=False)

    start_script = work_dir / "start.sh"
    start_proc = subprocess.run(
        [str(start_script)], cwd=work_dir, text=True, capture_output=True
    )

    subprocess.run(["pkill", "-f", "flask"], check=False)
    subprocess.run(["pkill", "-f", "vite"], check=False)
    subprocess.run(["pkill", "-f", "npm"], check=False)

    output = (start_proc.stdout or "") + (start_proc.stderr or "")
    assert "ImportError" not in output
