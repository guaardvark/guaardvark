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

Frontend URLs are resolved per file: a service that declares
``const BASE_URL = `${API_BASE_URL}/plugins` `` (API_BASE_URL imported from
apiClient) has its calls resolved against /api/plugins, and other module-level
constants (`const CODE_EXEC_BASE = "/code-execution"`) are substituted the
same way, including ones imported from another file. A template that ends in an
expression glued to a word (`/api/social-outreach${path}`, a query string
`${plan ? "?plan=1" : ""}`) is matched as a prefix. Test files are not callers.

Each page under frontend/src/pages is linked to the endpoints its own code and
the modules it imports call (`page_endpoints`).
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import Finding, FindingKind, Severity, source_files


# ── Backend route extraction ────────────────────────────────────────────────

ROUTE_DECORATORS = {"route", "get", "post", "put", "delete", "patch"}


def _backend_routes(root: Path, extra_excludes: frozenset[str], corpus=None) -> list[dict]:
    """Flask route registrations under backend/.

    Returns a list of dicts: {path, methods, file, line, blueprint_var, function},
    the path carrying its Blueprint(url_prefix=...) prefix.
    """
    from .corpus import is_test_path

    backend_dir = root / "backend"
    if corpus is None and not backend_dir.is_dir():
        return []

    out: list[dict] = []
    for rel, f in _file_facts(root, extra_excludes, corpus, under="backend/", suffixes=(".py",)):
        # Relative to root, so a checkout under a folder named tests keeps its routes.
        rel_str = "/" + rel
        name = rel.rsplit("/", 1)[-1]
        if any(x in rel_str for x in ("/_archive/", "/backs/")) or is_test_path(rel) or \
           "_BACK" in name or "BACKUP" in name:
            continue  # don't conflate dead code, tests, or mocks with live routes
        for r in f.get("routes", []):
            out.append({
                "path": r["path"],
                "methods": r["methods"],
                "file": rel,
                "line": r["line"],
                "blueprint_var": r["bp_var"],
                "function": r["function"],
            })
    return out


def _file_facts(root: Path, extra_excludes: frozenset[str], corpus, under: str, suffixes):
    """(relative path, facts) for surveyed files below ``under`` with one of
    ``suffixes``, from the corpus or the per-file facts cache."""
    from . import facts as facts_mod

    if corpus is not None:
        for rel in corpus.files:
            if rel.startswith(under) and rel.endswith(suffixes):
                yield rel, corpus.facts[rel]
        return
    folder = root / under.rstrip("/")
    if not folder.is_dir():
        return
    for path in source_files(root, extra_excludes, under=folder):
        if not path.name.endswith(suffixes):
            continue
        rel = path.relative_to(root).as_posix()
        f = facts_mod.file_facts(path, rel)
        if f is not None:
            yield rel, f


# ── Frontend caller extraction ──────────────────────────────────────────────

JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


def _read_base_url_default(root: Path) -> str:
    """Read the BASE_URL default from frontend/src/api/apiClient.js.

    Matches `export const BASE_URL = (import.meta.env.X || "/api")...` and pulls
    the literal default. Returns "/api" if the file/constant isn't found."""
    api_client = root / "frontend" / "src" / "api" / "apiClient.js"
    if not api_client.is_file():
        return "/api"
    try:
        text = api_client.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "/api"
    # `BASE_URL = (import.meta.env.VITE_API_BASE_URL || "/api")` — take the
    # fallback string literal. Also tolerate `BASE_URL = "/api"`.
    m = re.search(r"""BASE_URL\s*=\s*\([^)]*\|\|\s*[`'"]([^`'"]+)[`'"]""", text)
    if m:
        return m.group(1).rstrip("/") or "/api"
    m = re.search(r"""BASE_URL\s*=\s*[`'"]([^`'"]+)[`'"]""", text)
    if m:
        return m.group(1).rstrip("/") or "/api"
    return "/api"


def _resolve_js_url(raw: str, base_url: str) -> str:
    """Resolve a raw frontend URL string into an absolute /api/... path.

    - `${BASE_URL}` (and `${ BASE_URL }`) → the resolved base_url.
    - any other `${VAR}` → a `<param>` segment (handled by _normalize later).
    - collapses a doubled `/api/api` that results from base + leading-/api path.
    """
    resolved = re.sub(r"\$\{\s*BASE_URL\s*\}", base_url, raw)
    # Collapse double /api/api that arises when a caller writes
    # `${BASE_URL}/api/foo` (base already ends in /api).
    resolved = re.sub(r"/api/api(?=/|$)", "/api", resolved)
    return resolved


_TEMPLATE_NAME = re.compile(r"\$\{\s*([A-Za-z_$][\w$]*)\s*\}")


def _const_env(rel: str, corpus, cache: dict, depth: int = 0) -> dict[str, str]:
    """{name: template} for the URL constants ``rel`` declares or imports."""
    if rel in cache:
        return cache[rel]
    cache[rel] = {}
    env: dict[str, str] = {}
    f = corpus.facts.get(rel) or {}
    if depth < 4:
        for local, (target, exported) in corpus.bindings(rel).items():
            if not exported or exported == "default":
                continue
            other = _const_env(target, corpus, cache, depth + 1)
            if exported in other:
                env[local] = other[exported]
    env.update(f.get("consts") or {})
    cache[rel] = env
    return env


def _expand(template: str, env: dict[str, str], base_url: str, depth: int = 0) -> str:
    """Substitute known constants into a URL template, recursively."""
    if depth > 6:
        return template

    def sub(m):
        name = m.group(1)
        if name in env and env[name] != template:
            return _expand(env[name], env, base_url, depth + 1)
        if name == "BASE_URL":
            return base_url
        return m.group(0)

    return _TEMPLATE_NAME.sub(sub, template)


def _frontend_callers(root: Path, extra_excludes: frozenset[str], corpus=None) -> list[dict]:
    """HTTP calls in frontend/src outside tests, each with its URL resolved."""
    from .corpus import Corpus, is_test_path

    fe = root / "frontend" / "src"
    if not fe.is_dir():
        return []
    if corpus is None:
        corpus = Corpus.build(root, extra_excludes)

    base_url = _read_base_url_default(root)
    cache: dict[str, dict] = {}
    out: list[dict] = []
    for rel in corpus.js_files():
        if not rel.startswith("frontend/src/") or is_test_path(rel):
            continue
        f = corpus.facts[rel]
        if not f.get("api_calls"):
            continue
        env = _const_env(rel, corpus, cache)
        local = sorted(f.get("local_consts") or [], key=lambda c: c[2])
        for call in f["api_calls"]:
            raw = call["raw"]
            call_env = env
            if local:
                # A block-level constant declared above the call, nearest wins.
                call_env = dict(env)
                for name, value, line in local:
                    if line <= call["line"]:
                        call_env[name] = value
            expanded = _expand(raw, call_env, base_url)
            resolved = _resolve_js_url(expanded, base_url)
            keep = (
                resolved.startswith(("/api/", "/socket.io/"))
                or resolved == "/api"
                or "/api/" in resolved
            )
            if not keep:
                continue
            out.append({
                "raw": raw,
                "resolved": resolved,
                "file": rel,
                "line": call["line"],
            })
    return out


# ── Path normalization: turn `/api/u/123` and `/api/u/<int:id>` into a common shape ──

_PARAM_PATTERNS = [
    (re.compile(r"<[^>]+>"), "<param>"),                 # Flask: <int:id> -> <param>
    (re.compile(r"\$\{[^}]+\}"), "<param>"),             # JS template: ${userId}
    (re.compile(r":[A-Za-z_][A-Za-z0-9_]*"), "<param>"), # Express-style: :id
    (re.compile(r"/\d+(?=/|$)"), "/<param>"),            # Hardcoded numeric IDs
]

# An expression glued to the end of a word: `/x/list${query}` or
# `/api/social-outreach${path}`. What follows is a query string or more path.
_GLUED_TAIL = re.compile(r"(?<=[^/])\$\{.*$")


def _strip_template_tail(path: str) -> tuple[str, bool]:
    """(path without a glued trailing expression, whether one was cut)."""
    p = path.split("?")[0].split("#")[0]
    cut = _GLUED_TAIL.sub("", p)
    return cut, cut != p


def _normalize(path: str) -> str:
    p = path.split("?")[0].split("#")[0]
    # find /api/... if present
    if "/api/" in p and not p.startswith("/api/"):
        p = "/api/" + p.split("/api/", 1)[1]
    for rx, sub in _PARAM_PATTERNS:
        p = rx.sub(sub, p)
    p = re.sub(r"/+", "/", p).rstrip("/")
    return p or "/"


def _caller_key(call: dict) -> tuple[str, bool]:
    """(normalized path, is_prefix) for one frontend call."""
    stripped, cut = _strip_template_tail(call.get("resolved", call["raw"]))
    return _normalize(stripped), cut


def _served_by(norm_path: str, prefix: bool, served: set[str], served_list: list[str]) -> bool:
    if norm_path in served:
        return True
    if prefix and any(s.startswith(norm_path + "/") or s == norm_path for s in served_list):
        return True
    # A served route that is a strict prefix of the caller (a catch-all).
    return any(norm_path.startswith(s + "/") for s in served_list)


def _js_reach(corpus, start: str, is_page) -> list[tuple[str, dict]]:
    """(file, api call) pairs reachable from the file ``start``: its own calls,
    and those in the functions and components it uses from other frontend
    modules, followed through imports by name. A name bound to a whole module
    (a default-exported service object) brings in that module's calls."""
    from .corpus import is_test_path

    out: list[tuple[str, dict]] = []
    seen: set[tuple[str, str]] = set()
    queue: list[tuple[str, str]] = [(start, "")]
    while queue:
        rel, sym = queue.pop()
        if (rel, sym) in seen or len(seen) > 400:
            continue
        seen.add((rel, sym))
        f = corpus.facts.get(rel) or {}
        lo, hi = 0, 10 ** 9
        if sym:
            d = next((d for d in f.get("defs", []) if d["name"] == sym), None)
            if d is not None:
                lo, hi = d["line"], d["end"]
        for call in f.get("api_calls", []):
            if lo <= call["line"] <= hi:
                out.append((rel, call))
        binds = corpus.bindings(rel)
        local = {d["name"] for d in f.get("defs", [])}
        for line, _caller, callee in f.get("calls", []):
            if not lo <= line <= hi:
                continue
            head, _, rest = callee.partition(".")
            if head in binds:
                target, exported = binds[head]
                if (target == rel or is_page(target) or not target.startswith("frontend/src/")
                        or is_test_path(target)):
                    continue
                if exported == "":
                    nxt = rest.split(".", 1)[0]
                elif exported == "default":
                    nxt = (corpus.facts.get(target) or {}).get("default_export", "")
                else:
                    nxt = exported
                tdefs = {d["name"] for d in (corpus.facts.get(target) or {}).get("defs", [])}
                queue.append((target, nxt if nxt in tdefs else ""))
            elif sym and head in local and head != sym:
                queue.append((rel, head))
    return out


def _page_endpoints(corpus, callers: list[dict], norm_routes: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """{page file: [{path, served, routes, via}]}: the endpoints each page under
    frontend/src/pages reaches through its own calls and the functions and
    components it uses from other modules (other pages are not followed; each
    is listed on its own)."""
    from .corpus import is_test_path

    if corpus is None:
        return {}
    served = set(norm_routes)
    served_list = sorted(served)
    kept = {(c["file"], c["line"], c["raw"]): c for c in callers}

    def is_page(rel: str) -> bool:
        return rel.startswith("frontend/src/pages/") and not is_test_path(rel)

    out: dict[str, list[dict]] = {}
    for page in (f for f in corpus.js_files() if is_page(f)):
        endpoints: dict[str, dict] = {}
        for rel, call in _js_reach(corpus, page, is_page):
            c = kept.get((rel, call["line"], call["raw"]))
            if c is None:
                continue  # not an /api call
            norm, prefix = _caller_key(c)
            if norm in endpoints:
                continue
            matches = norm_routes.get(norm) or []
            if not matches and prefix:
                matches = [r for p, rs in norm_routes.items() if p.startswith(norm + "/") for r in rs][:3]
            endpoints[norm] = {
                "path": norm,
                "served": bool(matches) or _served_by(norm, prefix, served, served_list),
                "routes": sorted({f"{r['file']}:{r['line']}" for r in matches})[:3],
                "via": f"{c['file']}:{c['line']}",
            }
        if endpoints:
            out[page] = sorted(endpoints.values(), key=lambda e: e["path"])
    return out


# ── Orchestration ───────────────────────────────────────────────────────────

def analyze(root: Path, extra_excludes: frozenset[str] = frozenset(), corpus=None) -> dict[str, Any]:
    from .corpus import Corpus

    root = Path(root).resolve()
    if corpus is None and (root / "frontend" / "src").is_dir():
        corpus = Corpus.build(root, extra_excludes)
    routes = _backend_routes(root, extra_excludes, corpus)
    callers = _frontend_callers(root, extra_excludes, corpus)

    # Index routes by normalized path
    norm_routes: dict[str, list[dict]] = defaultdict(list)
    for r in routes:
        norm_routes[_normalize(r["path"])].append(r)

    # Index callers by normalized path (constants substituted, glued tails cut).
    norm_calls: dict[str, list[dict]] = defaultdict(list)
    prefix_calls: set[str] = set()
    for c in callers:
        norm, prefix = _caller_key(c)
        norm_calls[norm].append(c)
        if prefix:
            prefix_calls.add(norm)

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
                evidence={"path": norm_path, "methods": sorted(method_intersect),
                          "key": f"{norm_path}|{','.join(sorted(method_intersect))}"},
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
                evidence={"prefix": prefix, "file_count": len(files), "key": prefix},
            ))

    # 3. GHOST_ENDPOINT: route exists, no caller normalizes to it.
    # Collect candidates first, sort by path, then emit at most GHOST_LIMIT (LOW)
    # plus one INFO rollup if there are more — keeps the findings panel signal-rich.
    called_paths = set(norm_calls.keys())
    called_prefixes = sorted(prefix_calls)
    ghost_candidates: list[tuple[str, list[str], list[str]]] = []
    for norm_path, route_list in norm_routes.items():
        if norm_path in called_paths:
            continue
        if any(norm_path == p or norm_path.startswith(p + "/") for p in called_prefixes):
            continue
        # Skip socket.io and known internal-only patterns
        if norm_path.startswith("/socket.io"):
            continue
        files = sorted({r["file"] for r in route_list})
        methods = sorted({m for r in route_list for m in r["methods"]})
        ghost_candidates.append((norm_path, files, methods))

    ghost_candidates.sort(key=lambda t: t[0])
    GHOST_LIMIT = 25
    ghost_total = len(ghost_candidates)
    for norm_path, files, methods in ghost_candidates[:GHOST_LIMIT]:
        findings.append(Finding(
            kind=FindingKind.GHOST_ENDPOINT,
            severity=Severity.LOW,
            summary=f"{norm_path} — backend route with no frontend caller",
            paths=files,
            evidence={"route": norm_path, "methods": methods, "key": norm_path},
        ))
    ghost_shown = min(ghost_total, GHOST_LIMIT)
    if ghost_total > GHOST_LIMIT:
        findings.append(Finding(
            kind=FindingKind.GHOST_ENDPOINT,
            severity=Severity.INFO,
            summary=(f"{ghost_total - GHOST_LIMIT} additional ghost endpoints "
                     f"suppressed (ask system_map_query for a route to see its callers)"),
            paths=[],
            evidence={"suppressed": ghost_total - GHOST_LIMIT,
                      "shown": ghost_shown, "total": ghost_total, "key": "rollup"},
        ))

    # 4. GHOST_API_CALLER: frontend hits a path the backend doesn't serve.
    # A URL with no fixed segment after /api (`${BASE_URL}/${path}`) names no
    # route at all, so it is counted as unresolved rather than reported.
    served = set(norm_routes.keys())
    served_list = sorted(served)
    unresolved = 0
    for norm_path, call_list in norm_calls.items():
        if _served_by(norm_path, norm_path in prefix_calls, served, served_list):
            continue
        if not any(seg and seg != "<param>" for seg in norm_path.split("/")[2:]):
            unresolved += len(call_list)
            continue
        files = sorted({c["file"] for c in call_list})
        findings.append(Finding(
            kind=FindingKind.GHOST_API_CALLER,
            severity=Severity.MEDIUM,
            summary=f"{norm_path} — frontend caller with no matching backend route",
            paths=files,
            evidence={"path": norm_path, "key": norm_path,
                      "callers": [c["file"] + ":" + str(c["line"]) for c in call_list[:5]]},
        ))

    page_endpoints = _page_endpoints(corpus, callers, norm_routes)

    return {
        "graph": {
            "routes": routes,
            "callers": callers,
            "edges": [
                {"path": p, "routes": [r["file"] for r in rs], "callers": [c["file"] for c in norm_calls.get(p, [])]}
                for p, rs in norm_routes.items()
            ],
            "page_endpoints": page_endpoints,
        },
        "findings": findings,
        "stats": {
            "backend_routes": len(routes),
            "unique_paths": len(norm_routes),
            "frontend_callers": len(callers),
            "unique_called_paths": len(norm_calls),
            "prefix_callers": len(prefix_calls),
            "unresolved_callers": unresolved,
            "pages_linked": len(page_endpoints),
            "ghost_endpoints": sum(1 for f in findings if f.kind == FindingKind.GHOST_ENDPOINT),
            "ghost_endpoints_total": ghost_total,
            "ghost_endpoints_shown": ghost_shown,
            "ghost_callers": sum(1 for f in findings if f.kind == FindingKind.GHOST_API_CALLER),
            "path_collisions": sum(1 for f in findings if f.kind == FindingKind.URL_PATH_COLLISION),
            "prefix_collisions": sum(1 for f in findings if f.kind == FindingKind.URL_PREFIX_COLLISION),
        },
    }
