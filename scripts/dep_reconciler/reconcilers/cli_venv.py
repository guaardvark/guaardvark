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

    def install(self, log_path: Path) -> int:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {self.id} install @ {os.getpid()} ===\n")
            log.flush()
            return self._run_subprocess(
                [sys.executable, "-m", "pip", "install", "-e", str(self.root / "cli")],
                log,
            )

    @staticmethod
    def _run_subprocess(args: list[str], log) -> int:
        proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
        return proc.returncode
