"""Module dependency graph, import cycles and module lifecycle.

Covers Python and JavaScript (see corpus.py for how imports resolve). Edges
come in three kinds:

  * top   runs when the module is imported: module-level imports, and every
          JavaScript static import, re-export and require;
  * lazy  runs only when a function runs: imports inside a def, dynamic
          import() and import.meta.glob;
  * type  under ``if TYPE_CHECKING:``, which never runs.

Only top edges can deadlock an import, so cycles and over-coupling are
computed on them. Lifecycle counts top and lazy importers alike, but not test
files: a module only a test imports is not part of the running product.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import Finding, FindingKind, Severity
from .corpus import Corpus, module_name, node_id

# A module on this many short import cycles is reported as a hub.
HUB_MIN_CYCLES = 5
# Longest cycle counted for hubs, and a cap on the search per module.
_HUB_CYCLE_LEN = 4
_HUB_SEARCH_CAP = 200


def _module_name(rel: Path) -> str:
    """Convert a path like backend/services/foo.py to backend.services.foo."""
    return module_name(Path(rel).as_posix())


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
            if len(scc) > 1 or (len(scc) == 1 and scc[0] in graph.get(scc[0], set())):
                cycles.append(sorted(scc))

    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 10000))
    try:
        for node in list(graph.keys()):
            if node not in indices:
                strongconnect(node)
    finally:
        sys.setrecursionlimit(old_limit)
    return cycles


def _hub_counts(graph: dict[str, set[str]], cycles: list[list[str]]) -> dict[str, int]:
    """Short cycles each module sits on, counted inside its cycle group."""
    hits: dict[str, int] = defaultdict(int)
    for group in cycles:
        if len(group) < 3:
            continue
        members = set(group)
        for start in group:
            # Enumerate cycles whose smallest member is ``start``.
            found: list[list[str]] = []
            stack = [(start, [start])]
            while stack and len(found) < _HUB_SEARCH_CAP:
                node, path = stack.pop()
                for succ in graph.get(node, ()):
                    if succ not in members:
                        continue
                    if succ == start and len(path) > 1:
                        found.append(path)
                    elif succ not in path and succ > start and len(path) < _HUB_CYCLE_LEN:
                        stack.append((succ, path + [succ]))
            for cyc in found:
                for m in cyc:
                    hits[m] += 1
    return dict(hits)


def analyze(root: Path, extra_excludes: frozenset[str] = frozenset(),
            dynamic_modules: frozenset[str] | set[str] | None = None,
            corpus: Corpus | None = None) -> dict[str, Any]:
    """Build the import graph and its findings.

    ``dynamic_modules``: dotted names reached by string-keyed dispatch (Celery
    task names, blueprint auto-registration, plugin.json, tool tables). They
    have no importer and are alive, so they are never reported dormant.
    """
    corpus = corpus or Corpus.build(root, extra_excludes)
    root = corpus.root
    dynamic_modules = set(dynamic_modules or ())

    ids = {rel: node_id(rel) for rel in corpus.files}
    graph: dict[str, set[str]] = defaultdict(set)
    lazy: dict[str, set[str]] = defaultdict(set)
    type_only: dict[str, set[str]] = defaultdict(set)
    importers: dict[str, set[str]] = defaultdict(set)        # non-test, top + lazy
    test_importers: dict[str, set[str]] = defaultdict(set)
    lazy_importers: dict[str, set[str]] = defaultdict(set)

    edge_counts = {"top": 0, "lazy": 0, "type": 0}
    for e in corpus.edges():
        src, dst = ids[e.src], ids[e.dst]
        if src == dst:
            continue
        edge_counts[e.kind] += 1
        if e.kind == "top":
            graph[src].add(dst)
        elif e.kind == "lazy":
            lazy[src].add(dst)
        else:
            type_only[src].add(dst)
            continue
        if corpus.is_test(e.src):
            test_importers[dst].add(src)
        else:
            importers[dst].add(src)
            if e.kind == "lazy":
                lazy_importers[dst].add(src)

    rel_of = {v: k for k, v in ids.items()}
    entries = corpus.entry_points()
    cycles = _tarjan_scc(dict(graph))
    hubs = _hub_counts(graph, cycles)

    findings: list[Finding] = []
    for cyc in cycles:
        sev = Severity.MEDIUM if len(cyc) <= 5 else Severity.LOW
        findings.append(Finding(
            kind=FindingKind.IMPORT_CYCLE,
            severity=sev,
            summary=f"Import cycle ({len(cyc)} modules): {' → '.join(cyc[:6])}{'…' if len(cyc) > 6 else ''}",
            paths=[rel_of[m] for m in cyc if m in rel_of],
            evidence={"cycle": cyc, "size": len(cyc), "key": "|".join(cyc)},
        ))

    for module, count in sorted(hubs.items(), key=lambda x: -x[1])[:8]:
        if count >= HUB_MIN_CYCLES:
            findings.append(Finding(
                kind=FindingKind.OVER_COUPLED,
                severity=Severity.MEDIUM,
                summary=f"{module} sits on {count} short import cycles (≤{_HUB_CYCLE_LEN} modules) — refactor candidate",
                paths=[rel_of[module]] if module in rel_of else [],
                evidence={"cycle_count": count, "max_cycle_len": _HUB_CYCLE_LEN, "key": module},
            ))

    dormant_suppressed_dynamic = 0
    for rel in corpus.files:
        mod = ids[rel]
        if importers.get(mod):
            continue
        if mod in dynamic_modules or rel in entries:
            dormant_suppressed_dynamic += 1
            continue
        if not _dormant_candidate(rel):
            continue
        tests = sorted(test_importers.get(mod, ()))
        summary = f"Module has no importer outside tests: {rel}" if tests else \
            f"Module has no static importer: {rel}"
        findings.append(Finding(
            kind=FindingKind.DORMANT_MODULE,
            severity=Severity.LOW,
            summary=summary,
            paths=[rel],
            evidence={"key": rel, "test_importers": tests[:5]},
        ))

    for rel in corpus.files:
        if _is_backup_artifact(rel):
            findings.append(Finding(
                kind=FindingKind.BACKUP_ARTIFACT,
                severity=Severity.LOW,
                summary=f"Backup/archived artifact in source tree: {rel}",
                paths=[rel],
                evidence={"key": rel},
            ))
    # Non-code backups (x.py.BACK) are listed too: they are not modules.
    for rel in corpus.all_files:
        if rel not in corpus.facts and _is_backup_artifact(rel):
            findings.append(Finding(
                kind=FindingKind.BACKUP_ARTIFACT,
                severity=Severity.LOW,
                summary=f"Backup/archived artifact in source tree: {rel}",
                paths=[rel],
                evidence={"key": rel},
            ))

    test_basenames = {Path(r).stem[len("test_"):] for r in corpus.files
                      if Path(r).name.startswith("test_") and r.endswith(".py")}
    for rel in corpus.python_files():
        if not rel.startswith("backend/"):
            continue
        mod = ids[rel]
        if _lifecycle_for(rel, importers.get(mod)) not in ("active", "auto-loaded"):
            continue
        name = Path(rel).name
        if name == "__init__.py" or rel.endswith("_api.py"):
            continue
        stem = Path(rel).stem
        if stem in test_basenames or test_importers.get(mod):
            continue
        findings.append(Finding(
            kind=FindingKind.UNTESTED_MODULE,
            severity=Severity.INFO,
            summary=f"No test found for module: {rel}",
            paths=[rel],
            evidence={"expected_test": f"test_{stem}.py", "key": rel},
        ))

    node_meta = {}
    for rel in corpus.files:
        mod = ids[rel]
        f = corpus.facts[rel]
        lifecycle = _lifecycle_for(rel, importers.get(mod))
        if lifecycle == "dormant" and (mod in dynamic_modules or rel in entries):
            lifecycle = "auto-loaded"
        node_meta[mod] = {
            "path": rel,
            "lang": f["lang"],
            "lifecycle": lifecycle,
            "importers": len(importers.get(mod, ())),
            "lazy_importers": len(lazy_importers.get(mod, ())),
            "test_importers": len(test_importers.get(mod, ())),
            "imports": len(graph.get(mod, ())) + len(lazy.get(mod, ())),
            "lines": f.get("lines", 0),
        }
        if rel in entries:
            node_meta[mod]["started_by"] = entries[rel][:5]

    return {
        "graph": {k: sorted(v) for k, v in graph.items()},
        "lazy_graph": {k: sorted(v) for k, v in lazy.items()},
        "type_graph": {k: sorted(v) for k, v in type_only.items()},
        "node_meta": node_meta,
        "findings": findings,
        "file_count": len(corpus.files),
        "stats": {
            "modules": len(corpus.files),
            "python_modules": len(corpus.python_files()),
            "js_modules": len(corpus.js_files()),
            "internal_edges": sum(len(v) for v in graph.values()),
            "lazy_edges": sum(len(v) for v in lazy.values()),
            "type_only_edges": sum(len(v) for v in type_only.values()),
            "cycles": len(cycles),
            "largest_cycle": max((len(c) for c in cycles), default=0),
            "over_coupled_hubs": [m for m, h in hubs.items() if h >= HUB_MIN_CYCLES],
            "untested_modules": sum(1 for f in findings if f.kind == FindingKind.UNTESTED_MODULE),
            "dormant_suppressed_dynamic": dormant_suppressed_dynamic,
            "entry_points": len(entries),
            "import_roots": sorted(corpus.import_roots),
            "parse_errors": sorted(r for r in corpus.files if corpus.facts[r].get("error"))[:20],
        },
    }


def _dormant_candidate(rel: str) -> bool:
    """Whether a module with no importer is worth reporting: not a test, a
    script, an entry point, a package init, an auto-registered blueprint, or
    a frontend build/config file."""
    from .corpus import is_test_path

    name = rel.rsplit("/", 1)[-1]
    if name in ("__init__.py", "__main__.py", "conftest.py") or is_test_path(rel):
        return False
    if _is_backup_artifact(rel) or "/_template/" in rel:
        return False  # scaffolding copied to make a new extension
    if any(rel.startswith(s) for s in ("scripts/", "cli/", "training/", "docker/")):
        return False
    if "/api/" in rel and rel.endswith("_api.py"):
        return False
    if "/" not in rel:
        return False  # top-level entry scripts (run_tests.py, conftest.py)
    if not rel.endswith(".py"):
        if not rel.startswith("frontend/src/"):
            return False  # bundler config, public/ assets, plugin UIs loaded by URL
        if name in ("main.jsx", "main.js", "main.tsx", "setupTests.js"):
            return False
    if "/scripts/" in rel:
        return False
    return True


def _is_backup_artifact(rel: str) -> bool:
    return (
        "_BACK" in rel
        or rel.endswith((".BACK", ".BACKUP", ".bak"))
        or "__BACKUP" in rel
        or "/backs/" in rel
        or "/_archive/" in rel
    )


def _lifecycle_for(rel: str, importers: set | None) -> str:
    """Classify a module's lifecycle from its path + whether anything outside
    tests imports it. Keys match the frontend's LIFECYCLE style table."""
    from .corpus import is_test_path

    name = rel.rsplit("/", 1)[-1]
    if _is_backup_artifact(rel):
        return "archived"
    if is_test_path(rel):
        return "test"
    if any(rel.startswith(p) for p in ("scripts/", "cli/", "training/")) or "/scripts/" in rel:
        return "script"
    if name in ("config.py", "settings.py") or (
            not rel.endswith(".py") and not rel.startswith("frontend/src/") and ".config." in name):
        return "config"
    # Blueprints, package inits and the frontend entry are loaded at runtime
    # even with no static importer.
    if name == "__init__.py" or ("/api/" in rel and rel.endswith("_api.py")) or \
            rel in ("frontend/src/main.jsx", "frontend/src/main.js"):
        return "auto-loaded"
    if not importers:
        return "dormant"
    return "active"
