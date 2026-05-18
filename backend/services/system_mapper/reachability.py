"""Frontend ↔ backend reachability map.

Pairs every backend `@bp.route('/api/...')` (and `@app.route(...)`) with the
frontend file(s) that hit that path via fetch / axios / apiClient. Surfaces:

  * GHOST_ENDPOINT       — a backend route nothing in the frontend calls
  * GHOST_API_CALLER     — a frontend call to a path the backend doesn't serve
  * URL_PATH_COLLISION   — two backend files registering the same exact path
  * URL_PREFIX_COLLISION — two backend files registering /api/foo/* (the audit's
                           '/api/meta' class of bugs)

Path matching is template-aware: `/api/users/<int:id>` matches a frontend call
to `/api/users/${userId}` after both are normalized to `/api/users/<param>`.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import Finding, FindingKind, Severity, is_excluded


# ── Backend route extraction ────────────────────────────────────────────────

ROUTE_DECORATORS = {"route", "get", "post", "put", "delete", "patch"}


def _backend_routes(root: Path, extra_excludes: frozenset[str]) -> list[dict]:
    """Walk backend Python and extract Flask route registrations.

    Returns a list of dicts: {path, methods, file, line, blueprint_var, prefix}.
    Captures Blueprint(url_prefix=...) and pairs each @bp.route with that prefix.
    """
    backend_dir = root / "backend"
    if not backend_dir.is_dir():
        return []

    out: list[dict] = []
    for py in backend_dir.rglob("*.py"):
        if is_excluded(py, extra_excludes):
            continue
        rel_str = str(py)
        if any(s in rel_str for s in ("/_archive/", "/backs/", "/tests/")) or \
           "_BACK" in py.name or "BACKUP" in py.name or py.name.startswith("test_"):
            continue  # don't conflate dead code, tests, or mocks with live routes
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue

        # First pass: find Blueprint(...) assignments and capture url_prefix per var name.
        bp_prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                func_name = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute)
                    else None
                )
                if func_name == "Blueprint":
                    prefix = ""
                    for kw in node.value.keywords:
                        if kw.arg == "url_prefix" and isinstance(kw.value, ast.Constant):
                            prefix = kw.value.value or ""
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            bp_prefixes[tgt.id] = prefix

        # Second pass: collect @<bp>.route(...) decorators
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in ROUTE_DECORATORS:
                    continue
                if not isinstance(func.value, ast.Name):
                    continue
                bp_var = func.value.id
                prefix = bp_prefixes.get(bp_var, "")

                path_arg = ""
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    path_arg = dec.args[0].value or ""

                methods = []
                if func.attr in {"get", "post", "put", "delete", "patch"}:
                    methods = [func.attr.upper()]
                else:
                    for kw in dec.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                            for elt in kw.value.elts:
                                if isinstance(elt, ast.Constant):
                                    methods.append(elt.value)

                full_path = (prefix.rstrip("/") + "/" + path_arg.lstrip("/")) if prefix else path_arg
                full_path = "/" + full_path.lstrip("/")
                full_path = re.sub(r"/+", "/", full_path)

                out.append({
                    "path": full_path,
                    "methods": sorted(set(methods)) or ["GET"],
                    "file": str(py.relative_to(root)),
                    "line": node.lineno,
                    "blueprint_var": bp_var,
                    "function": node.name,
                })
    return out


# ── Frontend caller extraction ──────────────────────────────────────────────

JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
# Capture the URL (path) string in fetch / axios / apiClient calls — handles
# template literals via a follow-up normalization step.
FETCH_PATTERNS = [
    re.compile(r"""fetch\(\s*[`'"]([^`'"]+)[`'"]"""),
    re.compile(r"""axios\.(?:get|post|put|delete|patch)\(\s*[`'"]([^`'"]+)[`'"]"""),
    re.compile(r"""axios\(\s*\{\s*url:\s*[`'"]([^`'"]+)[`'"]"""),
    re.compile(r"""apiClient\.(?:get|post|put|delete|patch)\(\s*[`'"]([^`'"]+)[`'"]"""),
]


def _frontend_callers(root: Path, extra_excludes: frozenset[str]) -> list[dict]:
    fe = root / "frontend" / "src"
    if not fe.is_dir():
        return []

    out: list[dict] = []
    for jsf in fe.rglob("*"):
        if not jsf.is_file() or jsf.suffix not in JS_EXTS:
            continue
        if is_excluded(jsf, extra_excludes):
            continue
        try:
            text = jsf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line_no, line in enumerate(text.split("\n"), 1):
            for pat in FETCH_PATTERNS:
                for m in pat.finditer(line):
                    raw = m.group(1)
                    # Only care about API-shaped paths
                    if not raw.startswith(("/api/", "/socket.io/")) and "/api/" not in raw:
                        continue
                    out.append({
                        "raw": raw,
                        "file": str(jsf.relative_to(root)),
                        "line": line_no,
                    })
    return out


# ── Path normalization: turn `/api/u/123` and `/api/u/<int:id>` into a common shape ──

_PARAM_PATTERNS = [
    (re.compile(r"<[^>]+>"), "<param>"),                 # Flask: <int:id> -> <param>
    (re.compile(r"\$\{[^}]+\}"), "<param>"),             # JS template: ${userId}
    (re.compile(r":[A-Za-z_][A-Za-z0-9_]*"), "<param>"), # Express-style: :id
    (re.compile(r"/\d+(?=/|$)"), "/<param>"),            # Hardcoded numeric IDs
]


def _normalize(path: str) -> str:
    p = path.split("?")[0].split("#")[0]
    # find /api/... if present
    if "/api/" in p and not p.startswith("/api/"):
        p = "/api/" + p.split("/api/", 1)[1]
    for rx, sub in _PARAM_PATTERNS:
        p = rx.sub(sub, p)
    p = re.sub(r"/+", "/", p).rstrip("/")
    return p or "/"


# ── Orchestration ───────────────────────────────────────────────────────────

def analyze(root: Path, extra_excludes: frozenset[str] = frozenset()) -> dict[str, Any]:
    routes = _backend_routes(root, extra_excludes)
    callers = _frontend_callers(root, extra_excludes)

    # Index routes by normalized path
    norm_routes: dict[str, list[dict]] = defaultdict(list)
    for r in routes:
        norm_routes[_normalize(r["path"])].append(r)

    # Index callers by normalized path
    norm_calls: dict[str, list[dict]] = defaultdict(list)
    for c in callers:
        norm_calls[_normalize(c["raw"])].append(c)

    findings: list[Finding] = []

    # 1. URL_PATH_COLLISION: same exact path served by 2+ different files
    for norm_path, route_list in norm_routes.items():
        files = {r["file"] for r in route_list}
        if len(files) <= 1:
            continue
        # Treat method-overlapping registrations as the actual collision risk.
        # If two files claim the same path AND the same method, that's a true bomb.
        methods_per_file: dict[str, set[str]] = defaultdict(set)
        for r in route_list:
            methods_per_file[r["file"]].update(r["methods"])
        method_intersect = set.intersection(*methods_per_file.values())
        if method_intersect:
            findings.append(Finding(
                kind=FindingKind.URL_PATH_COLLISION,
                severity=Severity.HIGH,
                summary=f"{norm_path} registered with overlapping methods {sorted(method_intersect)} by {len(files)} files",
                paths=sorted(files),
                evidence={"path": norm_path, "methods": sorted(method_intersect)},
            ))

    # 2. URL_PREFIX_COLLISION: /api/<segment> claimed by 2+ files
    prefix_to_files: dict[str, set[str]] = defaultdict(set)
    for r in routes:
        norm = _normalize(r["path"])
        parts = norm.strip("/").split("/")
        if len(parts) >= 2:
            prefix_to_files["/" + "/".join(parts[:2])].add(r["file"])
    for prefix, files in prefix_to_files.items():
        if len(files) > 1:
            findings.append(Finding(
                kind=FindingKind.URL_PREFIX_COLLISION,
                severity=Severity.MEDIUM,
                summary=f"{prefix}/* shared by {len(files)} blueprint files (load-order dependent)",
                paths=sorted(files),
                evidence={"prefix": prefix, "file_count": len(files)},
            ))

    # 3. GHOST_ENDPOINT: route exists, no caller normalizes to it
    called_paths = set(norm_calls.keys())
    for norm_path, route_list in norm_routes.items():
        if norm_path in called_paths:
            continue
        # Skip socket.io and known internal-only patterns
        if norm_path.startswith("/socket.io"):
            continue
        files = sorted({r["file"] for r in route_list})
        findings.append(Finding(
            kind=FindingKind.GHOST_ENDPOINT,
            severity=Severity.LOW,
            summary=f"{norm_path} — backend route with no frontend caller",
            paths=files,
            evidence={"route": norm_path, "methods": sorted({m for r in route_list for m in r["methods"]})},
        ))

    # 4. GHOST_API_CALLER: frontend hits a path the backend doesn't serve
    served = set(norm_routes.keys())
    for norm_path, call_list in norm_calls.items():
        if norm_path in served:
            continue
        # Soft-match: if any served route is a strict prefix of this caller, treat as served.
        if any(norm_path.startswith(s + "/") or norm_path == s for s in served):
            continue
        files = sorted({c["file"] for c in call_list})
        findings.append(Finding(
            kind=FindingKind.GHOST_API_CALLER,
            severity=Severity.MEDIUM,
            summary=f"{norm_path} — frontend caller with no matching backend route",
            paths=files,
            evidence={"path": norm_path, "callers": [c["file"] + ":" + str(c["line"]) for c in call_list[:5]]},
        ))

    return {
        "graph": {
            "routes": routes,
            "callers": callers,
            "edges": [
                {"path": p, "routes": [r["file"] for r in rs], "callers": [c["file"] for c in norm_calls.get(p, [])]}
                for p, rs in norm_routes.items()
            ],
        },
        "findings": findings,
        "stats": {
            "backend_routes": len(routes),
            "unique_paths": len(norm_routes),
            "frontend_callers": len(callers),
            "unique_called_paths": len(norm_calls),
            "ghost_endpoints": sum(1 for f in findings if f.kind == FindingKind.GHOST_ENDPOINT),
            "ghost_callers": sum(1 for f in findings if f.kind == FindingKind.GHOST_API_CALLER),
            "path_collisions": sum(1 for f in findings if f.kind == FindingKind.URL_PATH_COLLISION),
            "prefix_collisions": sum(1 for f in findings if f.kind == FindingKind.URL_PREFIX_COLLISION),
        },
    }
