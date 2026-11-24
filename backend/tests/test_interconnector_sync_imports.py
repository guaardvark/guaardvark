"""Every first-party module that synced code imports is synced too.

A client receives app.py, config.py and the rest of the synced tree; if one of
them imports a ``backend.*`` module the sync does not carry, the client dies at
import on its next boot. test_interconnector_exclude.py checks the top-level
entries under backend/; this follows the imports themselves, so a module
behind an exclude rule or outside the listed paths is caught as well.
"""
import ast
import inspect
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.services.interconnector_file_sync_service import InterconnectorFileSyncService

_REPO = Path(inspect.getfile(InterconnectorFileSyncService)).resolve().parents[2]


def _tracked_python(repo: Path):
    if shutil.which("git") and (repo / ".git").exists():
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--", "*.py"],
            capture_output=True, text=True, check=True,
        ).stdout
        return [p for p in out.split("\0") if p]
    return [
        str(p.relative_to(repo)).replace(os.sep, "/")
        for p in repo.rglob("*.py")
        if "venv" not in p.parts and "node_modules" not in p.parts
    ]


def _carried(svc, rel: str) -> bool:
    if svc.should_exclude_file(rel):
        return False
    for entry in svc.default_sync_paths:
        if rel == entry or (entry.endswith("/") and rel.startswith(entry)):
            return True
    return False


def _module_file(repo: Path, dotted: str):
    base = repo.joinpath(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return str(candidate.relative_to(repo)).replace(os.sep, "/")
    return None


def _backend_imports(tree):
    """(dotted module, line) for every absolute ``backend`` import in ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "backend" or alias.name.startswith("backend."):
                    yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "backend" or node.module.startswith("backend."):
                yield node.module, node.lineno
                for alias in node.names:
                    if alias.name != "*":
                        yield f"{node.module}.{alias.name}", node.lineno


def unsynced_backend_imports(repo: Path, svc=None):
    """Imports in synced code that resolve to a backend file the sync leaves behind."""
    svc = svc or InterconnectorFileSyncService()
    found = []
    for rel in _tracked_python(repo):
        if not _carried(svc, rel):
            continue
        try:
            tree = ast.parse((repo / rel).read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for dotted, line in _backend_imports(tree):
            target = _module_file(repo, dotted)
            if target and not _carried(svc, target):
                found.append(f"{rel}:{line} imports {dotted} ({target})")
    return sorted(set(found))


def test_synced_code_imports_only_synced_backend_modules():
    missing = unsynced_backend_imports(_REPO)
    assert not missing, (
        "synced code imports backend modules the Interconnector never sends "
        "(add them to default_sync_paths):\n" + "\n".join(missing[:50])
    )


def test_a_new_unlisted_package_is_caught(tmp_path):
    (tmp_path / "backend" / "brand_new").mkdir(parents=True)
    (tmp_path / "backend" / "brand_new" / "__init__.py").write_text("X = 1\n")
    (tmp_path / "backend" / "app.py").write_text("from backend.brand_new import X\n")
    found = unsynced_backend_imports(tmp_path)
    assert found == ["backend/app.py:1 imports backend.brand_new (backend/brand_new/__init__.py)"]
