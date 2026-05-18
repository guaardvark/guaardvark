"""Reconcile frontend node_modules against package-lock.json."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts.dep_reconciler.base import Reconciler


class Frontend(Reconciler):
    id = "frontend"
    name = "Frontend node_modules"

    def __init__(self, repo_root: Path):
        self.root = repo_root

    def manifests(self) -> list[Path]:
        # Lockfile only — it's the installed truth, same way pip uses requirements.txt.
        return [self.root / "frontend" / "package-lock.json"]

    def is_active(self) -> bool:
        return self.manifests()[0].is_file()

    def compute_hash(self) -> str:
        from scripts.dep_reconciler.util import hash_file
        return hash_file(self.manifests()[0]) or ""

    def install(self, log_path: Path) -> int:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {self.id} install @ {os.getpid()} ===\n")
            log.flush()
            # `npm ci` is lockfile-strict: errors on drift instead of silently
            # rewriting package-lock.json. With lockfile-only hashing, this is
            # the correct tool — `npm install` would mutate the lockfile and
            # register as drift on the next reconciler boot.
            return self._run_subprocess(
                ["npm", "ci"], log, cwd=self.root / "frontend"
            )

    @staticmethod
    def _run_subprocess(args: list[str], log, cwd: Path | None = None) -> int:
        proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, cwd=cwd)
        return proc.returncode
