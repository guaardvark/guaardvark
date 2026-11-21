"""The main venv's requirement files, and the check that reads them.

chatterbox-tts pins torch and transformers to releases with known
vulnerabilities, so it is installed without its dependencies and the floors
live in requirements.txt. These tests fail if that arrangement is undone.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.requirements import Requirement

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
VENV_SYNC = PLUGIN_ROOT / "scripts" / "venv_sync.sh"


def _requirements(name: str) -> dict[str, Requirement]:
    reqs = {}
    for raw in (PLUGIN_ROOT / name).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            req = Requirement(line)
            reqs[req.name.lower()] = req
    return reqs


def test_chatterbox_is_installed_without_its_dependencies():
    assert "chatterbox-tts" not in _requirements("requirements.txt")
    assert "chatterbox-tts" in _requirements("requirements-nodeps.txt")


@pytest.mark.parametrize("package, vulnerable, fixed", [
    ("torch", ["2.6.0", "2.9.1"], "2.10.0"),              # CVE-2026-24747
    ("transformers", ["5.2.0", "5.9.0"], "5.10.0"),       # CVE-2026-4372, -5241, -9856
])
def test_security_floors(package, vulnerable, fixed):
    spec = _requirements("requirements.txt")[package].specifier
    for version in vulnerable:
        assert not spec.contains(version), f"{package} {version} must not satisfy '{spec}'"
    assert spec.contains(fixed)


def _requirements_ok(tmp_path: Path, *lines: str) -> subprocess.CompletedProcess:
    """Run af_requirements_ok against a stand-in venv that is this interpreter."""
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    # A wrapper, not a symlink: a symlinked interpreter resolves to the base
    # Python and would not see this environment's packages.
    python = venv_bin / "python"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    reqs = tmp_path / "reqs.txt"
    reqs.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return subprocess.run(
        ["bash", "-c", '. "$1"; af_requirements_ok "$2" "$3"', "_",
         str(VENV_SYNC), str(tmp_path / "venv"), str(reqs)],
        capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_requirements_check_passes_when_satisfied(tmp_path):
    result = _requirements_ok(tmp_path, "# comment", "pytest>=1.0   # trailing comment", "packaging")
    assert result.returncode == 0, result.stderr


def test_requirements_check_names_an_old_package(tmp_path):
    result = _requirements_ok(tmp_path, "pytest>=99999")
    assert result.returncode == 1
    assert "REQUIREMENTS_FAIL: pytest" in result.stderr


def test_requirements_check_names_a_missing_package(tmp_path):
    result = _requirements_ok(tmp_path, "no-such-package-for-this-test")
    assert result.returncode == 1
    assert "is not installed" in result.stderr


def test_requirements_check_skips_markers_for_other_pythons(tmp_path):
    result = _requirements_ok(tmp_path, 'no-such-package-for-this-test; python_version < "3.0"')
    assert result.returncode == 0, result.stderr
