"""Aggregated reconciler for shared-venv plugins.

One pip invocation installs the union of every enabled plugin's
requirements*.txt files. Per-plugin manifest hashes still feed
into state for fine-grained drift visibility.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from scripts.dep_reconciler.base import Reconciler


class PluginBundle(Reconciler):
    id = "plugin_bundle"
    name = "Enabled plugin requirements (aggregated)"

    def __init__(self, repo_root: Path, member_plugin_ids: list[str]):
        self.root = repo_root
        self.members = sorted(member_plugin_ids)

    def manifests(self) -> list[Path]:
        out: list[Path] = []
        for pid in self.members:
            for req in sorted((self.root / "plugins" / pid).glob("requirements*.txt")):
                out.append(req)
        return out

    def is_active(self) -> bool:
        return any(m.is_file() for m in self.manifests())

    def compute_hash(self) -> str:
        """Hash includes the member set AND each member's reqs hash."""
        from scripts.dep_reconciler.util import hash_file
        h = hashlib.sha256()
        for pid in self.members:
            h.update(pid.encode("utf-8"))
            h.update(b"\x00")
            for req in sorted((self.root / "plugins" / pid).glob("requirements*.txt")):
                sub = hash_file(req) or ""
                h.update(sub.encode("ascii"))
                h.update(b"\n")
        return f"sha256:{h.hexdigest()}"

    def member_hashes(self) -> dict[str, str]:
        """Per-plugin hash for fine-grained state tracking."""
        from scripts.dep_reconciler.util import hash_file
        out: dict[str, str] = {}
        for pid in self.members:
            h = hashlib.sha256()
            for req in sorted((self.root / "plugins" / pid).glob("requirements*.txt")):
                sub = hash_file(req) or ""
                h.update(sub.encode("ascii"))
                h.update(b"\n")
            out[pid] = f"sha256:{h.hexdigest()}"
        return out

    def install(self, log_path: Path) -> int:
        manifests = self.manifests()
        if not manifests:
            return 0
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {self.id} install (members={self.members}) ===\n")
            log.flush()
            args = [sys.executable, "-m", "pip", "install"]
            for m in manifests:
                args += ["-r", str(m)]
            return self._run_subprocess(args, log)

    @staticmethod
    def _run_subprocess(args: list[str], log) -> int:
        proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
        return proc.returncode
