"""Reconcile alembic schema against backend/migrations/versions/."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from scripts.dep_reconciler.base import Reconciler


class Alembic(Reconciler):
    id = "alembic"
    name = "Alembic database migrations"

    def __init__(self, repo_root: Path):
        self.root = repo_root

    @property
    def versions_dir(self) -> Path:
        return self.root / "backend" / "migrations" / "versions"

    @property
    def alembic_ini(self) -> Path:
        return self.root / "backend" / "alembic.ini"

    def manifests(self) -> list[Path]:
        return [self.versions_dir]

    def is_active(self) -> bool:
        if not self.versions_dir.is_dir():
            return False
        if not self.alembic_ini.is_file():
            return False
        # First-boot guard: if alembic isn't importable yet, BackendVenv runs
        # first and installs it; we'll pick up reconciliation next boot.
        return self._alembic_importable()

    def compute_hash(self) -> str:
        from scripts.dep_reconciler.util import hash_dir
        return hash_dir(self.versions_dir) or ""

    def extra_state(self) -> dict[str, object]:
        cur = self._alembic_current()
        return {"alembic_head": cur} if cur else {}

    def install(self, log_path: Path) -> int:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {self.id} install @ {os.getpid()} ===\n")
            log.flush()
            return self._run_subprocess(
                [sys.executable, "-m", "alembic", "-c", str(self.alembic_ini), "upgrade", "head"],
                log,
                cwd=self.root,
            )

    # --- helpers (test seams) ---

    @staticmethod
    def _alembic_importable() -> bool:
        return importlib.util.find_spec("alembic") is not None

    def _alembic_current(self) -> str | None:
        try:
            out = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(self.alembic_ini), "current"],
                capture_output=True, text=True, timeout=15, cwd=self.root,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if out.returncode != 0:
            return None
        # `alembic current` output is e.g. "abc123 (head)" or empty if no rev applied.
        line = out.stdout.strip().split("\n")[0] if out.stdout.strip() else ""
        return line.split()[0] if line else None

    @staticmethod
    def _run_subprocess(args: list[str], log, cwd: Path | None = None) -> int:
        proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, cwd=cwd)
        return proc.returncode
