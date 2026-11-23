"""Shell helpers the native install runs on a fresh machine.

Each test runs the real script with bash. Only the system commands the
script calls (sudo, apt-get) are replaced, by stubs placed first on PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
APT_SH = REPO / "scripts" / "lib" / "apt_install.sh"
UI_SH = REPO / "scripts" / "lib" / "terminal_ui.sh"
VERIFY_SH = REPO / "scripts" / "verify_gpu_stack.sh"
DETECTOR = REPO / "backend" / "services" / "hardware_detector.py"


def _bash(script: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged.pop("_GV_APT_REFRESHED", None)
    if env:
        merged.update(env)
    return subprocess.run(["bash", "-c", script], cwd=str(cwd or REPO), env=merged,
                          text=True, capture_output=True, timeout=30)


def _stub_bin(tmp_path: Path, lists_present: bool) -> tuple[Path, Path]:
    """sudo runs its arguments; apt-get logs each call and finds packages only
    once `apt-get update` has run (or when the lists were there already)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "apt-calls.log"
    lists = tmp_path / "lists-ready"
    if lists_present:
        lists.touch()
    (bin_dir / "sudo").write_text('#!/bin/bash\nexec "$@"\n')
    (bin_dir / "apt-get").write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "$*" >> "{calls}"
        case "$1" in
          update) touch "{lists}"; exit 0 ;;
          install) [ -e "{lists}" ] && exit 0
                   echo "E: Unable to locate package ${{@: -1}}" >&2; exit 100 ;;
        esac
        exit 1
        """))
    for f in ("sudo", "apt-get"):
        (bin_dir / f).chmod(0o755)
    return bin_dir, calls


def test_apt_install_refreshes_empty_lists_once_and_retries(tmp_path):
    bin_dir, calls = _stub_bin(tmp_path, lists_present=False)
    r = _bash(f'. "{APT_SH}"; gv_apt_install zstd && gv_apt_install postgresql-16-pgvector',
              env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    assert r.returncode == 0, r.stderr
    assert calls.read_text().splitlines() == [
        "install -y zstd", "update -qq", "install -y zstd",
        "install -y postgresql-16-pgvector",
    ]


def test_apt_install_does_not_refresh_when_lists_are_present(tmp_path):
    bin_dir, calls = _stub_bin(tmp_path, lists_present=True)
    r = _bash(f'. "{APT_SH}"; gv_apt_install zstd', env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    assert r.returncode == 0, r.stderr
    assert calls.read_text().splitlines() == ["install -y zstd"]


def test_apt_install_reports_failure_for_a_missing_package(tmp_path):
    bin_dir, calls = _stub_bin(tmp_path, lists_present=False)
    # The refresh does not make an unknown package appear: the stub keeps failing.
    (bin_dir / "apt-get").write_text(f'#!/bin/bash\necho "$*" >> "{calls}"\n[ "$1" = update ] && exit 0\nexit 100\n')
    r = _bash(f'. "{APT_SH}"; gv_apt_install no-such-pkg; echo "rc=$?"; gv_apt_install other; echo "rc=$?"',
              env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    assert "rc=100" in r.stdout and r.stdout.count("rc=") == 2
    assert "rc=0" not in r.stdout
    # One refresh per run, not one per package.
    assert calls.read_text().splitlines().count("update -qq") == 1
