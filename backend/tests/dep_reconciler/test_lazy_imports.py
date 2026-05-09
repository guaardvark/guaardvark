"""Guard against non-stdlib top-level imports in scripts/dep_reconciler/.

Why: the reconciler may run before the very deps it's responsible for
installing have been pip-installed. Any non-stdlib top-level import
would cause ModuleNotFoundError at boot. Lazy-import inside method
bodies instead.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PKG_DIR = REPO_ROOT / "scripts" / "dep_reconciler"

# sys.stdlib_module_names is Python 3.10+. We require 3.12+, so safe to use.
STDLIB = sys.stdlib_module_names | {"scripts"}  # our own package allowed


def _top_level_imports(py: Path) -> list[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                out.append(node.module)
    return out


@pytest.mark.parametrize("py_file", sorted(PKG_DIR.rglob("*.py")), ids=lambda p: p.relative_to(PKG_DIR).as_posix())
def test_only_stdlib_imports_at_top_level(py_file: Path):
    """Top-level imports must come from stdlib or the scripts.* namespace."""
    bad: list[str] = []
    for mod in _top_level_imports(py_file):
        top = mod.split(".")[0]
        if top not in STDLIB:
            bad.append(mod)
    assert not bad, (
        f"{py_file.relative_to(REPO_ROOT)} has non-stdlib top-level imports: {bad}. "
        "Move them inside method bodies."
    )
