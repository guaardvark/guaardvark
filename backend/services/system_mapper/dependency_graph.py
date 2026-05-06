"""Module dependency graph + cycle detection.

Walks every .py file under the root, parses imports via AST, builds a directed
graph of module → set(modules-it-imports), and reports strongly-connected
components (cycles) via Tarjan's algorithm. Modules that participate in many
cycles get an OVER_COUPLED finding — these are the architecturally interesting
"hub" nodes.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import Finding, FindingKind, Severity, is_excluded


def _module_name(rel: Path) -> str:
    """Convert a path like backend/services/foo.py to backend.services.foo."""
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _parse_imports(py_path: Path) -> list[str]:
    """Return the module-strings imported by py_path (best effort, never raises)."""
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append(node.module)
                for alias in node.names:
                    out.append(f"{node.module}.{alias.name}")
    return out


def _tarjan_scc(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's SCC. Returns SCCs of size >1 OR self-loops (real cycles only)."""
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    cycles: list[list[str]] = []

    def strongconnect(node: str) -> None:
        indices[node] = index_counter[0]
        lowlinks[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for succ in graph.get(node, ()):
            if succ not in indices:
                strongconnect(succ)
                lowlinks[node] = min(lowlinks[node], lowlinks[succ])
            elif succ in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[succ])
        if lowlinks[node] == indices[node]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc.append(w)
                if w == node:
                    break
            # keep only real cycles: SCCs with >1 member OR a self-loop
            if len(scc) > 1 or (len(scc) == 1 and scc[0] in graph.get(scc[0], set())):
                cycles.append(sorted(scc))

    import sys
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 10000))
    try:
        for node in list(graph.keys()):
            if node not in indices:
                strongconnect(node)
    finally:
        sys.setrecursionlimit(old_limit)
    return cycles


def analyze(root: Path, extra_excludes: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Build dep graph and detect cycles. Returns dict with graph, findings, stats."""
    # 1. Discover all internal modules (those we own and can analyze)
    modules: dict[str, Path] = {}  # module_name -> path
    for py in root.rglob("*.py"):
        if is_excluded(py, extra_excludes):
            continue
        try:
            rel = py.relative_to(root)
        except ValueError:
            continue
        modules[_module_name(rel)] = py

    internal_names = set(modules.keys())

    # 2. Parse imports and filter to internal-only edges
    graph: dict[str, set[str]] = defaultdict(set)
    external_count = 0
    for mod_name, path in modules.items():
        for imp in _parse_imports(path):
            if imp == mod_name:
                continue  # don't count self-references from `from . import x`
            # Match the import to an internal module — try full name first,
            # then prefix matches (from x.y import z → check if x.y.z is internal).
            if imp in internal_names:
                graph[mod_name].add(imp)
            else:
                # Try matching against a known module by walking up the dotted parts
                parts = imp.split(".")
                matched = False
                for n in range(len(parts), 0, -1):
                    cand = ".".join(parts[:n])
                    if cand in internal_names and cand != mod_name:
                        graph[mod_name].add(cand)
                        matched = True
                        break
                if not matched:
                    external_count += 1

    # 3. Cycle detection via Tarjan
    cycles = _tarjan_scc(dict(graph))

    # 4. Findings
    findings: list[Finding] = []

    # Over-coupled hubs: modules that appear in many cycles
    cycle_membership: dict[str, int] = defaultdict(int)
    for cyc in cycles:
        for m in cyc:
            cycle_membership[m] += 1

    for cyc in cycles:
        # Severity: short cycles = medium (more localized), long = low (more diffuse)
        sev = Severity.MEDIUM if len(cyc) <= 5 else Severity.LOW
        findings.append(Finding(
            kind=FindingKind.IMPORT_CYCLE,
            severity=sev,
            summary=f"Import cycle ({len(cyc)} modules): {' → '.join(cyc[:6])}{'…' if len(cyc) > 6 else ''}",
            paths=[str(modules[m].relative_to(root)) for m in cyc if m in modules],
            evidence={"cycle": cyc, "size": len(cyc)},
        ))

    for module, hits in sorted(cycle_membership.items(), key=lambda x: -x[1])[:8]:
        if hits >= 5:
            findings.append(Finding(
                kind=FindingKind.OVER_COUPLED,
                severity=Severity.MEDIUM,
                summary=f"{module} participates in {hits} import cycles — refactor candidate",
                paths=[str(modules[module].relative_to(root))] if module in modules else [],
                evidence={"cycle_count": hits},
            ))

    # Dormant: modules that nothing internal imports. But careful — pytest-discovered
    # tests, scripts, app entry points, and Flask blueprints are all "dormant" in the
    # static-import sense yet very much alive at runtime. Tag conservatively.
    importers: dict[str, set[str]] = defaultdict(set)
    for src, targets in graph.items():
        for t in targets:
            importers[t].add(src)

    for mod_name, path in modules.items():
        if importers.get(mod_name):
            continue
        rel = path.relative_to(root)
        rel_str = str(rel)
        # Skip the obvious not-imported-but-loaded patterns
        if rel.name == "__init__.py":
            continue
        if any(s in rel_str for s in ("/tests/", "tests/", "/test_", "/_archive/", "/backs/")):
            continue
        if any(rel_str.startswith(s) for s in ("scripts/", "cli/", "training/")):
            continue
        # api blueprints are auto-discovered
        if "/api/" in rel_str and rel_str.endswith("_api.py"):
            continue
        findings.append(Finding(
            kind=FindingKind.DORMANT_MODULE,
            severity=Severity.LOW,
            summary=f"Module has no static importer: {rel_str}",
            paths=[rel_str],
            evidence={},
        ))

    # Backup-artifact findings (files that look like accidental commits)
    for mod_name, path in modules.items():
        rel = str(path.relative_to(root))
        if (
            "_BACK" in rel
            or rel.endswith((".BACK", ".BACKUP", ".bak"))
            or "__BACKUP" in rel
            or "/backs/" in rel
            or "/_archive/" in rel
        ):
            findings.append(Finding(
                kind=FindingKind.BACKUP_ARTIFACT,
                severity=Severity.LOW,
                summary=f"Backup/archived artifact in source tree: {rel}",
                paths=[rel],
                evidence={},
            ))

    # 5. Serialize graph (JSON-friendly: lists, not sets)
    graph_serializable = {k: sorted(v) for k, v in graph.items()}

    return {
        "graph": graph_serializable,
        "findings": findings,
        "file_count": len(modules),
        "stats": {
            "modules": len(modules),
            "internal_edges": sum(len(v) for v in graph.values()),
            "external_imports": external_count,
            "cycles": len(cycles),
            "over_coupled_hubs": [m for m, h in cycle_membership.items() if h >= 5],
        },
    }
