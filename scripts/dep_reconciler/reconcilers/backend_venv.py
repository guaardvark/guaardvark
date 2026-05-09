"""Reconcile the main backend venv against backend/requirements*.txt."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from scripts.dep_reconciler.base import Reconciler

# Mirror the legacy start.sh CRITICAL_PACKAGES set — these are the deps
# pip dependency resolution has been observed to silently drop on this
# project's requirements.txt. Pinned to the same versions start.sh used.
CRITICAL_PACKAGES = {
    "duckduckgo_search": "duckduckgo-search==8.1.1",
    "flask": "Flask==3.0.0",
    "celery": "celery==5.4.0",
    "redis": "redis==5.0.4",
    "llama_index": "llama-index-core>=0.13.0,<0.15.0",
    "lxml": "lxml==6.0.2",
    "alembic": "alembic",  # Not in legacy list but needed by the Alembic reconciler
}


class BackendVenv(Reconciler):
    id = "backend_venv"
    name = "Backend Python venv"

    def __init__(self, repo_root: Path):
        self.root = repo_root

    def manifests(self) -> list[Path]:
        return [
            self.root / "backend" / "requirements-base.txt",
            self.root / "backend" / "requirements.txt",
        ]

    def is_active(self) -> bool:
        return any(m.is_file() for m in self.manifests())

    def compute_hash(self) -> str:
        from scripts.dep_reconciler.util import hash_file
        h = hashlib.sha256()
        for m in self.manifests():
            sub = hash_file(m) or ""
            h.update(sub.encode("ascii"))
            h.update(b"\n")
        return f"sha256:{h.hexdigest()}"

    def extra_state(self) -> dict[str, object]:
        out: dict[str, object] = {}
        numpy_ver = self._pip_show("numpy")
        if numpy_ver:
            major = self._extract_major(numpy_ver)
            if major is not None:
                out["numpy_major"] = major
        gpu = self._gpu_uuid()
        if gpu:
            out["gpu_uuid"] = gpu
        return out

    def install(self, log_path: Path) -> int:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {self.id} install @ {os.getpid()} ===\n")
            log.flush()
            args = [sys.executable, "-m", "pip", "install"]
            for m in self.manifests():
                if m.is_file():
                    args += ["-r", str(m)]
            rc = self._run_subprocess(args, log)
            if rc != 0:
                return rc
            # Post-install: verify CRITICAL_PACKAGES
            for import_name, dist_name in CRITICAL_PACKAGES.items():
                if not self._pip_show(dist_name):
                    log.write(f"CRITICAL: {dist_name} missing after install — installing individually\n")
                    rc = self._run_subprocess([sys.executable, "-m", "pip", "install", dist_name], log)
                    if rc != 0:
                        return rc
            # PyTorch installer (subsumes start.sh:968-973). Idempotent — the
            # script skips installation if torch is already present at the
            # right CUDA major version. We invoke it unconditionally on each
            # drifted install; if torch is fine it's a no-op.
            torch_script = self.root / "scripts" / "install_pytorch.sh"
            if torch_script.is_file():
                log.write(f"Running {torch_script}\n")
                log.flush()
                rc = self._run_subprocess(["bash", str(torch_script)], log)
                if rc != 0:
                    log.write(f"WARN: install_pytorch.sh exited {rc}; backend may run without GPU torch\n")
                    # Don't fail the reconciler — torch absence is a degraded mode, not a crash.
                    # The post-install LLM-module check will catch hard failures.
            return 0

    # --- helpers (test seams) ---

    def _pip_show(self, dist_name: str) -> str | None:
        try:
            out = subprocess.run(
                [sys.executable, "-m", "pip", "show", dist_name],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if out.returncode != 0:
            return None
        for line in out.stdout.splitlines():
            if line.startswith("Version:"):
                return line.strip()
        return None

    @staticmethod
    def _extract_major(version_line: str) -> int | None:
        # "Version: 2.1.3" → 2
        try:
            v = version_line.split(":", 1)[1].strip()
            return int(v.split(".")[0])
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _gpu_uuid() -> str | None:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        if out.returncode != 0:
            return None
        return (out.stdout.strip().split("\n")[0] or None)

    @staticmethod
    def _run_subprocess(args: list[str], log) -> int:
        proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
        return proc.returncode
