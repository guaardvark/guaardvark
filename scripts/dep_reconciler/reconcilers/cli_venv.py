"""Reconcile the CLI editable install."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from scripts.dep_reconciler.base import Reconciler


class CliVenv(Reconciler):
    id = "cli_venv"
    name = "CLI tool (editable install)"

    def __init__(self, repo_root: Path):
        self.root = repo_root

    def manifests(self) -> list[Path]:
        return [
            self.root / "cli" / "requirements.txt",
            self.root / "cli" / "setup.py",
        ]

    def is_active(self) -> bool:
        return (self.root / "cli" / "setup.py").is_file()

    def compute_hash(self) -> str:
        from scripts.dep_reconciler.util import hash_file
        h = hashlib.sha256()
        for m in self.manifests():
            sub = hash_file(m) or ""
            h.update(sub.encode("ascii"))
            h.update(b"\n")
        return f"sha256:{h.hexdigest()}"

    def extra_state(self) -> dict[str, object]:
        """Check if the guaardvark binary is actually present in the current venv's bin directory."""
        venv_bin = Path(sys.executable).parent
        guaardvark_bin = venv_bin / "guaardvark"
        return {
            "installed": guaardvark_bin.is_file(),
        }

    def install(self, log_path: Path) -> int:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {self.id} install @ {os.getpid()} ===\n")
            log.flush()
            if self._already_installed():
                log.write("guaardvark already installed editable from cli/ at the current VERSION — nothing to do\n")
                return 0
            # cli/ is a setup.py project, so an isolated build fetches setuptools
            # from PyPI on every run — a DNS blip there failed the whole reconcile
            # (client box, 2026-08-31). The venv already has setuptools; build in
            # place, and fall back to the isolated build only if that fails.
            target = str(self.root / "cli")
            rc = self._run_subprocess(
                [sys.executable, "-m", "pip", "install", "--no-build-isolation", "-e", target],
                log,
            )
            if rc != 0:
                log.write("in-place editable build failed; retrying with an isolated build env\n")
                rc = self._run_subprocess(
                    [sys.executable, "-m", "pip", "install", "-e", target],
                    log,
                )
            return rc

    def _already_installed(self) -> bool:
        """Editable install already points at this checkout at the current VERSION.

        Read from the venv's own metadata (PEP 610 direct_url.json), no pip, no
        network — this is what lets a converged box boot offline.
        """
        import json
        from importlib.metadata import PackageNotFoundError, distribution

        try:
            dist = distribution("guaardvark")
        except PackageNotFoundError:
            return False
        try:
            want = (self.root / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if dist.version != want:
            return False
        try:
            direct = json.loads(dist.read_text("direct_url.json") or "{}")
        except (ValueError, OSError):
            return False
        if not (direct.get("dir_info") or {}).get("editable"):
            return False
        url = direct.get("url", "")
        src = url[len("file://"):] if url.startswith("file://") else url
        try:
            return Path(src).resolve() == (self.root / "cli").resolve()
        except OSError:
            return False

    @staticmethod
    def _run_subprocess(args: list[str], log) -> int:
        proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
        return proc.returncode
