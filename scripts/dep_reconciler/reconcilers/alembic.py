"""Reconcile alembic schema against backend/migrations/versions/."""
from __future__ import annotations

import importlib.util
import os
import socket
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
        return self.root / "backend" / "migrations" / "alembic.ini"

    @property
    def alembic_cwd(self) -> Path:
        # alembic.ini's `script_location = migrations` is CWD-relative, so we
        # invoke alembic from `backend/` (the parent of `migrations/`), not
        # from the ini's directory.
        return self.root / "backend"

    @property
    def models_py(self) -> Path:
        return self.root / "backend" / "models.py"

    def manifests(self) -> list[Path]:
        # models.py is the schema source-of-truth under the single-master-migration
        # policy (scripts/schema_sync.py reads it and applies db.create_all + stamp).
        # versions/ is included so historical migration churn still triggers a check.
        return [self.versions_dir, self.models_py]

    def is_active(self) -> bool:
        if not self.versions_dir.is_dir():
            return False
        if not self.alembic_ini.is_file():
            return False
        # First-boot guard: if alembic isn't importable yet, BackendVenv runs
        # first and installs it; we'll pick up reconciliation next boot.
        if not self._alembic_importable():
            return False
        if not self._db_reachable():
            # Postgres not up yet — schema sync happens later via scripts/schema_sync.py.
            # We don't log a warning here; that path is part of normal start.sh flow.
            return False
        return True

    def compute_hash(self) -> str:
        # Hash both versions/ dir AND models.py so drift fires for either source
        # changing. models.py edit without a migration file (the typical pattern
        # under the schema-sync policy) MUST trigger a reconcile, otherwise the
        # DB silently lags behind the model definitions.
        import hashlib
        from scripts.dep_reconciler.util import hash_dir, hash_file
        h = hashlib.sha256()
        h.update((hash_dir(self.versions_dir) or "").encode("ascii"))
        h.update(b"\n")
        h.update((hash_file(self.models_py) or "").encode("ascii"))
        h.update(b"\n")
        return f"sha256:{h.hexdigest()}"

    def extra_state(self) -> dict[str, object]:
        cur = self._alembic_current()
        return {"alembic_head": cur} if cur else {}

    def install(self, log_path: Path) -> int:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {self.id} install @ {os.getpid()} ===\n")
            log.flush()
            schema_sync_path = self.root / "scripts" / "schema_sync.py"
            if not schema_sync_path.is_file():
                log.write(f"ERROR: schema_sync.py not found at {schema_sync_path}\n")
                return 1
            return self._run_subprocess(
                [sys.executable, str(schema_sync_path)],
                log,
                cwd=self.root,
            )

    # --- helpers (test seams) ---

    @staticmethod
    def _alembic_importable() -> bool:
        return importlib.util.find_spec("alembic") is not None

    @staticmethod
    def _db_reachable() -> bool:
        """Quick TCP connect to Postgres host:port from env. False on any failure."""
        host = os.environ.get("DB_HOST", "localhost")
        try:
            port = int(os.environ.get("DB_PORT", "5432"))
        except ValueError:
            return False
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except (OSError, socket.timeout):
            return False

    def _alembic_current(self) -> str | None:
        try:
            out = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(self.alembic_ini), "current"],
                capture_output=True, text=True, timeout=15, cwd=self.alembic_cwd,
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
