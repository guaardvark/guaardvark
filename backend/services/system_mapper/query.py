"""Focused questions about one mapped codebase, answered as short readable text.

    where <feature text>     files and lines that implement a feature, each with why it matched
    module <path>            imports, importers (load time, inside functions, tests), routes,
                             tools, tasks, definitions and findings of one file
    symbol <name>            definitions, callers and callees of a function or class
    route <path>             handler, frontend callers and pages of an HTTP route
    tool <name>              class, registration, how the chat reaches it, MCP exposure
    subsystem <name>         what a named area holds and what it depends on
    impact <path|symbol>     what depends on it, and the tests to run after changing it
    findings <scope>         findings for a folder, file, subsystem or finding kind

Everything comes from the stored snapshot and its index (store.py); nothing
here reads the tree again except ``where``, which opens its best few files to
quote the matching lines. Calls are resolved through each file's imports,
best effort: a call on an object whose type is unknown matches by name only,
and the answer says which is which.
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from .corpus import is_test_path

MODES = ("where", "module", "symbol", "route", "tool", "subsystem", "impact", "findings")
MAX_CHARS = 4800

_STOP = frozenset("""
    a an and are as at be by can code codebase does done file files find for from function functions
    handle handled handles how implement implemented implementation in into is it its me of on or
    show tell that the this to what where which who why with handles lives located location
    defined decided decide decides happen happens part parts piece logic thing things guaardvark
    you your yours yourself i my we our us would could should will made own good get gets got
    out take takes without
""".split())


# ── Shared helpers ─────────────────────────────────────────────────────────

def _words(text: str) -> list[str]:
    from .facts import tokens_of
    out = []
    for t in sorted(tokens_of(text), key=text.lower().find):
        if t not in _STOP:
            out.append(t)
    return out


def stem(word: str) -> str:
    """A crude English stem so "images" meets "image" and "boxes" meets "box"."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("sses", "xes", "zes", "ches", "shes")):
        return w[:-2]
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _stems(tokens) -> set[str]:
    return {stem(t) for t in tokens}


# Words that name a piece of interface: a question with one is about the UI.
_UI_WORDS = frozenset({"box", "button", "field", "toggle", "slider", "dialog", "tab", "menu", "panel",
                       "card", "chip", "icon", "checkbox", "dropdown", "modal", "page", "screen",
                       "input", "picker", "switch", "badge", "tooltip", "banner", "toolbar", "sidebar"})


class _Out:
    """Accumulates lines up to a character budget."""

    def __init__(self, budget: int = MAX_CHARS) -> None:
        self.lines: list[str] = []
        self.size = 0
        self.budget = budget
        self.cut = False

    def add(self, line: str = "") -> bool:
        if self.size + len(line) + 1 > self.budget:
            self.cut = True
            return False
        self.lines.append(line)
        self.size += len(line) + 1
        return True

    def text(self) -> str:
        body = "\n".join(self.lines).rstrip()
        if self.cut:
            body += "\n… (answer cut to fit; ask a narrower question for the rest)"
        return body


def _short(text: str, n: int = 110) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ── The view: snapshot + index + derived lookups ───────────────────────────

class View:
    """Lookups over one snapshot and its index, built once per map."""

    def __init__(self, root: Path, snapshot: dict, index: dict) -> None:
        self.root = root
        self.snap = snapshot
        self.idx = index
        self.files: dict[str, dict] = index.get("files", {})
        self.node_meta: dict = snapshot.get("node_meta", {})
        self.rel_by_id = {meta["id"]: rel for rel, meta in self.files.items()}
        self._defs_by_file: dict[str, dict[str, list]] = {}
        self._importers: Optional[dict[str, list]] = None
        self._defs_by_name: Optional[dict[str, list]] = None
        self._df: Optional[Counter] = None

    # -- files ----------------------------------------------------------------
    def resolve_file(self, arg: str) -> tuple[Optional[str], list[str]]:
        """(file, candidates) for a path, a dotted module name or a file name."""
        a = (arg or "").strip().strip("`'\"").removeprefix("./")
        a = re.sub(r":\d+(-\d+)?$", "", a)
        root_str = str(self.root).rstrip("/") + "/"
        if a.startswith(root_str):
            a = a[len(root_str):]
        if a in self.files:
            return a, [a]
        if a in self.rel_by_id:
            return self.rel_by_id[a], [self.rel_by_id[a]]
        dotted = a.replace("/", ".").removesuffix(".py")
        if dotted in self.rel_by_id:
            return self.rel_by_id[dotted], [self.rel_by_id[dotted]]
        tail = [r for r in self.files if r.endswith("/" + a)]
        if not tail and "." not in a.rsplit("/", 1)[-1]:
            tail = [r for r in self.files if Path(r).stem == a.rsplit("/", 1)[-1]]
        tail.sort(key=lambda r: (is_test_path(r), len(r)))
        if len(tail) == 1:
            return tail[0], tail
        return (tail[0] if tail and not is_test_path(tail[0]) and len(tail) <= 3 and
                all(is_test_path(t) for t in tail[1:]) else None), tail

    def describe_file(self, rel: str) -> str:
        meta = self.files.get(rel, {})
        nm = self.node_meta.get(meta.get("id"), {})
        bits = ["Python" if meta.get("lang") == "py" else "JavaScript", f"{meta.get('lines', 0)} lines"]
        if meta.get("subsystem"):
            bits.append(f"subsystem: {meta['subsystem']}")
        if nm.get("lifecycle"):
            bits.append(f"lifecycle: {nm['lifecycle']}")
        return f"{rel} — " + ", ".join(bits)

    # -- definitions ------------------------------------------------------------
    def defs_in(self, rel: str) -> dict[str, list]:
        if rel not in self._defs_by_file:
            self._defs_by_file[rel] = {d[0]: d for d in self.idx.get("defs", {}).get(rel, [])}
        return self._defs_by_file[rel]

    def defs_by_name(self) -> dict[str, list]:
        """{bare name: [(file, def)]} over every definition."""
        if self._defs_by_name is None:
            out: dict[str, list] = defaultdict(list)
            for rel, defs in self.idx.get("defs", {}).items():
                for d in defs:
                    out[d[0].rsplit(".", 1)[-1]].append((rel, d))
            self._defs_by_name = out
        return self._defs_by_name

    def find_defs(self, name: str) -> list[tuple[str, list]]:
        """Definitions matching ``name``: "func", "Class.method", "path:func"."""
        name = name.strip().strip("`'\"()")
        name = re.sub(r"\(\)$", "", name)
        file_part = None
        if ":" in name and not name.startswith("/"):
            file_part, name = name.rsplit(":", 1)
        bare = name.rsplit(".", 1)[-1]
        hits = []
        for rel, d in self.defs_by_name().get(bare, []):
            if "." in name and not (d[0] == name or d[0].endswith("." + name)):
                continue
            if file_part:
                frel, _ = self.resolve_file(file_part)
                if frel and rel != frel:
                    continue
            hits.append((rel, d))
        hits.sort(key=lambda h: (is_test_path(h[0]), h[0]))
        return hits

    # -- imports ------------------------------------------------------------------
    def imports_of(self, rel: str) -> list[list]:
        return self.idx.get("imports", {}).get(rel, [])

    def importers_of(self, rel: str) -> list[list]:
        """[[src, kind, line]] for every file importing ``rel``."""
        if self._importers is None:
            out: dict[str, list] = defaultdict(list)
            for src, edges in self.idx.get("imports", {}).items():
                for dst, kind, line in edges:
                    out[dst].append([src, kind, line])
            self._importers = out
        return self._importers.get(rel, [])

    # -- calls ----------------------------------------------------------------------
    def resolve_call(self, rel: str, caller: str, callee: str) -> tuple[Optional[str], Optional[str]]:
        """(file, qualname) a call site lands on, or (None, None)."""
        parts = callee.split(".")
        binds = self.idx.get("bindings", {}).get(rel, {})
        local = self.defs_in(rel)
        if parts[0] == "?":
            return None, None
        if parts[0] in ("self", "cls") and len(parts) == 2:
            cls = caller.rsplit(".", 1)[0] if "." in caller else ""
            while cls:
                q = f"{cls}.{parts[1]}"
                if q in local:
                    return rel, q
                cls = cls.rsplit(".", 1)[0] if "." in cls else ""
            return None, None
        head = parts[0]
        if len(parts) == 1:
            if head in local:
                return rel, head
            if head in binds:
                target, sym = binds[head]
                if sym == "default":
                    sym = self.files.get(target, {}).get("default_export") or ""
                if sym and sym in self.defs_in(target):
                    return target, sym
                return (target, sym or None) if sym else (None, None)
            return None, None
        if head in binds:
            target, sym = binds[head]
            rest = parts[1:]
            if sym:
                q = ".".join([sym, *rest])
                return (target, q) if q in self.defs_in(target) else (None, None)
            # Bound to a module: follow submodules (backend.services.x.func).
            mod = self.files.get(target, {}).get("id", "")
            cur = target
            i = 0
            while i < len(rest) - 1:
                nxt = self.rel_by_id.get(f"{mod}.{rest[i]}") if mod else None
                if not nxt:
                    break
                cur, mod = nxt, f"{mod}.{rest[i]}"
                i += 1
            q = ".".join(rest[i:])
            return (cur, q) if q in self.defs_in(cur) else (None, None)
        if head in local:
            q = ".".join(parts)
            return (rel, q) if q in local else (None, None)
        return None, None

    def call_sites(self):
        for rel, calls in self.idx.get("calls", {}).items():
            for line, caller, callee in calls:
                yield rel, line, caller, callee

    # -- words ------------------------------------------------------------------------
    def doc_freq(self) -> Counter:
        if self._df is None:
            df: Counter = Counter()
            for toks in self.idx.get("tokens", {}).values():
                df.update(toks.split())
            self._df = df
        return self._df

    def idf(self, word: str) -> float:
        n = max(1, len(self.files))
        return math.log((n + 1) / (self.doc_freq().get(word, 0) + 1)) + 0.1

    def stemmed_tokens(self) -> dict[str, set[str]]:
        """{file: stems of its words}, built once per map."""
        if getattr(self, "_stemmed", None) is None:
            self._stemmed = {rel: _stems(toks.split()) for rel, toks in self.idx.get("tokens", {}).items()}
            df: Counter = Counter()
            for st in self._stemmed.values():
                df.update(st)
            self._stem_df = df
        return self._stemmed

    def stem_idf(self, s: str) -> float:
        self.stemmed_tokens()
        n = max(1, len(self.files))
        return math.log((n + 1) / (self._stem_df.get(s, 0) + 1)) + 0.1

    # -- findings -----------------------------------------------------------------------
    def findings(self, include_dismissed: bool = False) -> list[dict]:
        from .actions import ranked_findings
        return ranked_findings(self.snap, self.root, include_dismissed=include_dismissed)


_view_cache: dict[str, tuple[dict, View]] = {}
_view_lock = threading.Lock()


def view_for(root: Path, snapshot: dict, index: dict) -> View:
    key = snapshot.get("cache_key") or {}
    with _view_lock:
        hit = _view_cache.get(str(root))
        if hit and hit[0] == key:
            return hit[1]
    v = View(root, snapshot, index)
    with _view_lock:
        _view_cache[str(root)] = (key, v)
    return v


# ── Modes ──────────────────────────────────────────────────────────────────

def _fmt_def(rel: str, d: list) -> str:
    qual, kind, line, end, doc = d
    rng = f"{line}-{end}" if end and end != line else f"{line}"
    out = f"{rel}:{rng}  {kind} {qual}"
    return out + (f" — {_short(doc, 90)}" if doc else "")


def q_module(v: View, arg: str) -> str:
    rel, cands = v.resolve_file(arg)
    out = _Out()
    if rel is None:
        if cands:
            out.add(f"'{arg}' matches {len(cands)} files; name one:")
            for c in cands[:15]:
                out.add(f"  {c}")
            return out.text()
        return f"No file or module '{arg}' in the map. Try: where {arg}"
    meta = v.files[rel]
    nm = v.node_meta.get(meta["id"], {})
    out.add(v.describe_file(rel))
    if meta.get("doc"):
        out.add(f"  {_short(meta['doc'], 160)}")
    imps = v.imports_of(rel)
    top = sorted({d for d, k, _ in imps if k == "top"})
    lazy = sorted({d for d, k, _ in imps if k == "lazy"} - set(top))
    out.add(f"Imports at load time ({len(top)}): " + (", ".join(top[:14]) + (" …" if len(top) > 14 else "") if top else "none in this repository"))
    if lazy:
        out.add(f"Imports inside functions ({len(lazy)}): " + ", ".join(lazy[:12]) + (" …" if len(lazy) > 12 else ""))
    importers = v.importers_of(rel)
    prod_top = sorted({(s, l) for s, k, l in importers if k == "top" and not is_test_path(s)})
    prod_lazy = sorted({(s, l) for s, k, l in importers if k == "lazy" and not is_test_path(s)})
    tests = sorted({s for s, k, _ in importers if is_test_path(s)})
    out.add(f"Imported at load time by ({len(prod_top)}): " + (", ".join(f"{s}:{l}" for s, l in prod_top[:12]) + (" …" if len(prod_top) > 12 else "") if prod_top else "nothing"))
    if prod_lazy:
        out.add(f"Imported inside functions by ({len(prod_lazy)}): " + ", ".join(f"{s}:{l}" for s, l in prod_lazy[:12]) + (" …" if len(prod_lazy) > 12 else ""))
    out.add(f"Imported by tests ({len(tests)}): " + (", ".join(tests[:10]) + (" …" if len(tests) > 10 else "") if tests else "none"))
    reach = v.snap.get("reachability", {})
    routes = [r for r in reach.get("routes", []) if r["file"] == rel]
    if routes:
        out.add(f"Routes ({len(routes)}):")
        for r in sorted(routes, key=lambda r: r["line"])[:12]:
            out.add(f"  {','.join(r['methods'])} {r['path']} → {r['function']}() line {r['line']}")
        if len(routes) > 12:
            out.add(f"  … {len(routes) - 12} more")
    tools = [t for t in v.snap.get("tool_graph", {}).get("registered_tools", []) if t.get("file") == rel]
    if tools:
        out.add("Tools: " + ", ".join(f"{t['name']} ({t['class']}, line {t['line']})" for t in tools))
    tasks = [t for t in v.snap.get("celery_tasks", []) if t.get("file") == rel]
    if tasks:
        out.add("Celery tasks: " + ", ".join(f"{t['name']} (line {t['line']})" for t in tasks[:10]))
    pages = [p for p, eps in reach.get("page_endpoints", {}).items()
             if any(e["via"].startswith(rel + ":") for e in eps)]
    if pages:
        out.add("Pages that reach its HTTP calls: " + ", ".join(pages[:8]))
    defs = v.idx.get("defs", {}).get(rel, [])
    if defs:
        top_defs = [d for d in defs if "." not in d[0] or d[1] == "method"]
        out.add(f"Defines ({len(defs)}): " + ", ".join(f"{d[0]} {d[2]}" for d in top_defs[:20]) + (" …" if len(top_defs) > 20 else ""))
    fs = [f for f in v.findings() if rel in (f.get("paths") or [])]
    if fs:
        out.add(f"Findings ({len(fs)}):")
        for f in fs[:6]:
            out.add(f"  {f['severity']} {f['kind']} {f['id']}: {_short(f['summary'], 120)}")
    else:
        out.add("Findings: none")
    if nm.get("lifecycle") == "dormant":
        out.add("Note: nothing outside tests imports it, and no task, route, tool or plugin reaches it.")
    return out.text()


def q_symbol(v: View, arg: str) -> str:
    hits = v.find_defs(arg)
    out = _Out()
    if not hits:
        return f"No definition named '{arg}' in the map. Try: where {arg}"
    out.add(f"Definitions of '{arg}' ({len(hits)}):")
    for rel, d in hits[:6]:
        out.add("  " + _fmt_def(rel, d))
    if len(hits) > 6:
        out.add(f"  … {len(hits) - 6} more")
    targets = {(rel, d[0]) for rel, d in hits}
    bare = arg.strip().rsplit(":", 1)[-1].rsplit(".", 1)[-1].strip("()")
    resolved: list[tuple[str, int, str]] = []
    by_name: list[tuple[str, int, str]] = []
    for rel, line, caller, callee in v.call_sites():
        last = callee.rsplit(".", 1)[-1]
        if last != bare:
            continue
        t_rel, t_q = v.resolve_call(rel, caller, callee)
        if t_rel is not None and (t_rel, t_q) in targets:
            resolved.append((rel, line, caller))
        elif t_rel is None:
            by_name.append((rel, line, caller))
    resolved.sort(key=lambda c: (is_test_path(c[0]), c[0], c[1]))
    by_name.sort(key=lambda c: (is_test_path(c[0]), c[0], c[1]))
    out.add(f"Callers resolved through imports ({len(resolved)}):"
            + (" all in tests, none in the product" if resolved and all(is_test_path(c[0]) for c in resolved) else ""))
    for rel, line, caller in resolved[:25]:
        out.add(f"  {rel}:{line}" + (f" in {caller}()" if caller else " at module level"))
    if len(resolved) > 25:
        out.add(f"  … {len(resolved) - 25} more")
    if by_name:
        out.add(f"Calls matching by name only, receiver type unknown ({len(by_name)}):")
        for rel, line, caller in by_name[:12]:
            out.add(f"  {rel}:{line}" + (f" in {caller}()" if caller else ""))
        if len(by_name) > 12:
            out.add(f"  … {len(by_name) - 12} more")
    rel, d = hits[0]
    callees = []
    for line, caller, callee in v.idx.get("calls", {}).get(rel, []):
        if caller == d[0] or caller.startswith(d[0] + "."):
            t_rel, t_q = v.resolve_call(rel, caller, callee)
            callees.append((line, callee, t_rel, t_q))
    if callees:
        seen = set()
        lines = []
        for line, callee, t_rel, t_q in callees:
            if callee in seen:
                continue
            seen.add(callee)
            where = f" → {t_rel}:{v.defs_in(t_rel)[t_q][2]}" if t_rel and t_q in v.defs_in(t_rel) else ""
            lines.append(f"{callee}{where} (line {line})")
        out.add(f"Calls made by {d[0]} ({len(seen)} distinct):")
        for ln in lines[:20]:
            out.add("  " + ln)
        if len(lines) > 20:
            out.add(f"  … {len(lines) - 20} more")
    out.add("Resolution is static and best effort: calls through variables, getattr or inheritance may be missing.")
    return out.text()


def _norm_route(path: str) -> str:
    from .reachability import _normalize
    return _normalize(path)


def q_route(v: View, arg: str) -> str:
    from .reachability import _caller_key
    want = _norm_route(arg.strip() if arg.strip().startswith("/") else "/" + arg.strip())
    reach = v.snap.get("reachability", {})
    routes = [r for r in reach.get("routes", []) if _norm_route(r["path"]) == want]
    prefix = False
    if not routes:
        routes = [r for r in reach.get("routes", []) if _norm_route(r["path"]).startswith(want + "/")]
        prefix = bool(routes)
    out = _Out()
    if not routes:
        near = sorted({r["path"] for r in reach.get("routes", []) if want.rsplit("/", 1)[-1] in r["path"]})[:8]
        msg = f"No backend route {want}."
        if near:
            msg += " Routes with that last segment: " + ", ".join(near)
        return msg
    out.add(f"Routes {'under' if prefix else 'for'} {want} ({len(routes)}):")
    for r in sorted(routes, key=lambda r: r["path"])[:15]:
        out.add(f"  {','.join(r['methods'])} {r['path']} → {r['file']}:{r['line']} {r['function']}()")
    keys = {_norm_route(r["path"]) for r in routes}
    callers = []
    for c in reach.get("callers", []):
        norm, pre = _caller_key(c)
        if norm in keys or (pre and any(k.startswith(norm + "/") or k == norm for k in keys)):
            callers.append(c)
    out.add(f"Frontend callers ({len(callers)}):" if callers else "Frontend callers: none found (it may be called by scripts, MCP, the CLI or another service)")
    for c in callers[:12]:
        out.add(f"  {c['file']}:{c['line']}  {_short(c['raw'], 80)}")
    pages = sorted(p for p, eps in reach.get("page_endpoints", {}).items() if any(e["path"] in keys for e in eps))
    if pages:
        out.add("Pages that reach it: " + ", ".join(pages[:10]))
    fs = [f for f in v.findings(include_dismissed=False)
          if (f.get("evidence") or {}).get("route") in keys or (f.get("evidence") or {}).get("path") in keys]
    for f in fs[:4]:
        out.add(f"Finding {f['id']}: {f['kind']} — {_short(f['summary'])}")
    return out.text()


def q_tool(v: View, arg: str) -> str:
    tg = v.snap.get("tool_graph", {})
    tools = tg.get("registered_tools", [])
    name = arg.strip().strip("`'\"")
    t = next((x for x in tools if x["name"] == name), None)
    out = _Out()
    if t is None:
        close = [x["name"] for x in tools if name.lower() in x["name"].lower()]
        if len(close) == 1:
            t = next(x for x in tools if x["name"] == close[0])
        else:
            unreg = next((x for x in tg.get("defined_not_registered", []) if x["name"] == name), None)
            if unreg:
                return (f"Tool class {unreg['class']} ({unreg['file']}:{unreg['line']}) is named '{name}' "
                        "but tool_registry_init.py does not register it, so neither chat nor MCP can call it.")
            return f"No registered tool '{name}'." + (f" Close: {', '.join(close[:10])}" if close else "")
    out.add(f"Tool {t['name']}: class {t['class']} at {t['file']}:{t['line']}-{t.get('end', t['line'])}")
    if t.get("description"):
        out.add(f"  {_short(t['description'], 220)}")
    cond = f", only when not ({'; '.join(t['conditions'])})" if t.get("conditions") else ""
    out.add(f"Registered by {t['registered_in']}() at backend/tools/tool_registry_init.py:{t['registry_line']}"
            f" (category {t.get('category') or 'none'}{cond})")
    if t.get("surface") == "mcp":
        out.add("Surface: MCP clients only (the chat process does not register it).")
    out.add("How the chat reaches it: " + ("; ".join(t.get("reach") or []) or "nothing offers it"))
    others = [x for x in t.get("listed_in", []) if f"list {x}" not in (t.get("reach") or []) and x != "CORE_TOOLS"]
    if others:
        out.add("Also named in sets that classify tools: " + ", ".join(others))
    mcp = t.get("mcp") or {}
    out.add("MCP: " + ("offered" if mcp.get("exposed") else "not offered") + (f" ({mcp['reason']})" if mcp.get("reason") else ""))
    attrs = t.get("attrs") or {}
    if attrs:
        out.add("Declared: " + ", ".join(f"{k}={v}" for k, v in sorted(attrs.items())))
    out.add(f"Files naming it as a string: {t.get('reference_count', 0)}")
    fs = [f for f in v.findings() if (f.get("evidence") or {}).get("tool") == t["name"]]
    for f in fs[:3]:
        out.add(f"Finding {f['id']}: {f['kind']} — {_short(f['summary'])}")
    return out.text()


def q_subsystem(v: View, arg: str) -> str:
    from . import subsystems as subs
    definitions = subs.load(v.root) or []
    out = _Out()
    sub = subs.find(arg, definitions)
    if sub is None:
        names = ", ".join(s["name"] for s in definitions)
        return f"No subsystem '{arg}'. Subsystems: {names}" if names else "This tree has no subsystems.json."
    name = sub["name"]
    files = [rel for rel, m in v.files.items() if m.get("subsystem") == name]
    prod = [r for r in files if not is_test_path(r)]
    tests = [r for r in files if is_test_path(r)]
    lines = sum(v.files[r].get("lines", 0) for r in files)
    out.add(f"Subsystem '{name}': {sub['description']}")
    out.add(f"{len(prod)} source files, {len(tests)} test files, {lines} lines. Globs: {', '.join(sub['globs'][:6])}{' …' if len(sub['globs']) > 6 else ''}")

    def importers_n(r):
        return v.node_meta.get(v.files[r]["id"], {}).get("importers", 0)

    out.add("Most imported files:")
    for r in sorted(prod, key=lambda r: (-importers_n(r), r))[:10]:
        doc = v.files[r].get("doc")
        out.add(f"  {r} ({importers_n(r)} importers)" + (f" — {_short(doc, 80)}" if doc else ""))
    reach = v.snap.get("reachability", {})
    routes = [r for r in reach.get("routes", []) if r["file"] in set(files)]
    if routes:
        prefixes = Counter("/".join(_norm_route(r["path"]).split("/")[:3]) for r in routes)
        out.add(f"Routes: {len(routes)} — " + ", ".join(f"{p}/* ({n})" for p, n in prefixes.most_common(6)))
    tools = [t["name"] for t in v.snap.get("tool_graph", {}).get("registered_tools", []) if t.get("file") in set(files)]
    if tools:
        out.add(f"Tools ({len(tools)}): " + ", ".join(tools[:20]))
    tasks = [t["name"] for t in v.snap.get("celery_tasks", []) if t.get("file") in set(files)]
    if tasks:
        out.add(f"Celery tasks ({len(tasks)}): " + ", ".join(tasks[:12]))
    pages = [p for p in files if p.startswith("frontend/src/pages/") and not is_test_path(p)]
    if pages:
        out.add("Pages: " + ", ".join(Path(p).stem for p in pages))
    fileset = set(files)
    deps: Counter = Counter()
    users: Counter = Counter()
    for src, edges in v.idx.get("imports", {}).items():
        s_sub = v.files.get(src, {}).get("subsystem")
        if is_test_path(src):
            continue
        for dst, kind, _ in edges:
            d_sub = v.files.get(dst, {}).get("subsystem")
            if src in fileset and d_sub and d_sub != name:
                deps[d_sub] += 1
            elif dst in fileset and s_sub and s_sub != name:
                users[s_sub] += 1
    if deps:
        out.add("Depends on: " + ", ".join(f"{n} ({c})" for n, c in deps.most_common(8)))
    if users:
        out.add("Used by: " + ", ".join(f"{n} ({c})" for n, c in users.most_common(8)))
    fs = [f for f in v.findings() if fileset & set(f.get("paths") or [])]
    if fs:
        out.add("Findings: " + ", ".join(f"{k} {n}" for k, n in Counter(f["kind"] for f in fs).most_common()))
    return out.text()


def _tests_for(v: View, rels: set[str], depth: int = 2) -> list[str]:
    """Test files that import any of ``rels`` directly or through up to
    ``depth`` non-test modules, plus test_<stem>.py name matches."""
    def is_test_file(f: str) -> bool:
        name = Path(f).name
        return is_test_path(f) and (name.startswith("test_") or name.endswith("_test.py")
                                    or ".test." in name or ".spec." in name)

    tests: set[str] = set()
    frontier = set(rels)
    seen = set(rels)
    for _ in range(depth + 1):
        nxt = set()
        for rel in frontier:
            for src, kind, _ in v.importers_of(rel):
                if is_test_path(src):
                    if is_test_file(src):
                        tests.add(src)
                elif src not in seen:
                    seen.add(src)
                    nxt.add(src)
        frontier = nxt
    for rel in rels:
        stem = Path(rel).stem
        for f in v.files:
            if Path(f).name in (f"test_{stem}.py",) or Path(f).name.startswith(f"{stem}.test."):
                tests.add(f)
    return sorted(tests)


def q_impact(v: View, arg: str) -> str:
    out = _Out()
    rel, cands = v.resolve_file(arg)
    start: set[str] = set()
    callers: list[tuple[str, int]] = []
    if rel is not None:
        start = {rel}
        out.add(f"Impact of changing {v.describe_file(rel)}")
    else:
        hits = v.find_defs(arg)
        if not hits:
            if cands:
                return f"'{arg}' matches several files: " + ", ".join(cands[:10])
            return f"No file or definition '{arg}' in the map."
        start = {h[0] for h in hits}
        targets = {(h[0], h[1][0]) for h in hits}
        bare = arg.rsplit(".", 1)[-1].strip("()")
        for r, line, caller, callee in v.call_sites():
            if callee.rsplit(".", 1)[-1] != bare:
                continue
            t_rel, t_q = v.resolve_call(r, caller, callee)
            if (t_rel, t_q) in targets:
                callers.append((r, line))
        out.add(f"Impact of changing {arg}: defined in " + ", ".join(sorted(start)))
        prod_callers = sorted({c for c in callers if not is_test_path(c[0])})
        out.add(f"Direct callers ({len(prod_callers)}): " + (", ".join(f"{r}:{l}" for r, l in prod_callers[:15]) + (" …" if len(prod_callers) > 15 else "") if prod_callers else "none resolved"))
    levels: list[set[str]] = []
    seen = set(start)
    frontier = set(start)
    for _ in range(4):
        nxt = set()
        for r in frontier:
            for src, kind, _ in v.importers_of(r):
                if kind == "type" or is_test_path(src) or src in seen:
                    continue
                seen.add(src)
                nxt.add(src)
        if not nxt:
            break
        levels.append(nxt)
        frontier = nxt
    total = sum(len(lv) for lv in levels)
    if levels:
        out.add(f"Modules that depend on it: {total} within {len(levels)} import hops "
                f"({', '.join(str(len(lv)) for lv in levels)} per hop)")
        out.add("Direct importers: " + ", ".join(sorted(levels[0])[:20]) + (" …" if len(levels[0]) > 20 else ""))
        subs = Counter(v.files.get(r, {}).get("subsystem") or "none" for lv in levels for r in lv)
        out.add("Subsystems reached: " + ", ".join(f"{n} ({c})" for n, c in subs.most_common(8)))
    else:
        out.add("Nothing outside tests imports it.")
    for r in sorted(start):
        if r.startswith("backend/api/") or any(x["file"] == r for x in v.snap.get("reachability", {}).get("routes", [])):
            n = sum(1 for x in v.snap.get("reachability", {}).get("routes", []) if x["file"] == r)
            if n:
                out.add(f"{r} serves {n} HTTP routes; their callers break if signatures change (ask: route <path>).")
    seeds = start | {c[0] for c in callers if not is_test_path(c[0])}
    direct = _tests_for(v, seeds, depth=0)
    near = [t for t in _tests_for(v, seeds, depth=2) if t not in set(direct)]
    if not direct and not near:
        out.add("Tests to run: no test imports it, directly or within two hops")
        return out.text()
    out.add(f"Tests to run — import it directly or are named for it ({len(direct)}):")
    for t in direct[:25]:
        out.add(f"  {t}")
    if len(direct) > 25:
        out.add(f"  … {len(direct) - 25} more")
    if near:
        out.add(f"Also reach it within two imports ({len(near)}): " + ", ".join(near[:12]) + (" …" if len(near) > 12 else ""))
    return out.text()


def q_findings(v: View, arg: str) -> str:
    from . import subsystems as subs
    scope = (arg or "").strip()
    fs = v.findings()
    label = "all"
    if scope:
        kinds = {f["kind"] for f in fs}
        definitions = subs.load(v.root) or []
        sub = subs.find(scope, definitions) if definitions else None
        if scope in kinds:
            fs = [f for f in fs if f["kind"] == scope]
            label = f"kind {scope}"
        elif sub is not None and not ("/" in scope or scope.endswith(".py")):
            files = {rel for rel, m in v.files.items() if m.get("subsystem") == sub["name"]}
            fs = [f for f in fs if files & set(f.get("paths") or [])]
            label = f"subsystem {sub['name']}"
        else:
            folder = scope.strip().rstrip("/").removeprefix("./")
            rel = None if (v.root / folder).is_dir() else v.resolve_file(scope)[0]
            prefix = rel or folder
            fs = [f for f in fs if any(p == prefix or p.startswith(prefix.rstrip("/") + "/")
                                       for p in (f.get("paths") or []))]
            label = prefix
    out = _Out()
    counts = Counter(f["severity"] for f in fs)
    out.add(f"Findings for {label}: {len(fs)} (" + ", ".join(f"{k} {counts[k]}" for k in ("high", "medium", "low", "info") if counts[k]) + ")")
    for f in fs[:30]:
        paths = f.get("paths") or []
        where = f" [{', '.join(paths[:2])}{' …' if len(paths) > 2 else ''}]" if paths else ""
        if not out.add(f"  {f['severity']} {f['kind']} {f['id']}: {_short(f['summary'], 110)}{where}"):
            break
    if len(fs) > 30:
        out.add(f"  … {len(fs) - 30} more")
    return out.text()


# ── where ──────────────────────────────────────────────────────────────────

def _zvec_hits(root: Path, q: str, limit: int = 6) -> list[tuple[str, int, str]]:
    """(file, line, snippet) from the zvec-grep index, only when the checkout
    has one (<root>/.zvec-grep); [] otherwise or on any failure."""
    if not (root / ".zvec-grep").is_dir():
        return []
    try:
        from backend.tools.code_search_tools import _hybrid_search
        text = _hybrid_search(str(root), q, limit) or ""
    except Exception:
        return []
    hits = []
    for m in re.finditer(r"([\w./-]+\.(?:py|jsx?|tsx?)):(\d+)", text):
        hits.append((m.group(1), int(m.group(2)), ""))
    return hits[:limit]


def q_where(v: View, arg: str) -> str:
    from .facts import tokens_of

    words = _words(arg)
    out = _Out()
    if not words:
        return "Say what to look for, e.g. where image seed box"
    stems = {w: stem(w) for w in words}
    weights = {w: v.stem_idf(stems[w]) for w in words}
    ui_question = bool(set(words) & _UI_WORDS)
    need = max(1, min(2, len(words)))
    score: dict[str, float] = defaultdict(float)
    why: dict[str, list[str]] = defaultdict(list)
    lines_hit: dict[str, list[tuple[int, str]]] = defaultdict(list)

    def matched(tokens) -> list[str]:
        st = _stems(tokens)
        return [w for w in words if stems[w] in st]

    # 1. Words in the file (identifiers, strings, comments) and in its path.
    # A long file holds most words somewhere, so its matches count for less
    # (BM25's length normalisation, k1=1.2, b=0.75, on distinct words).
    stemmed = v.stemmed_tokens()
    avg_len = (sum(len(st) for st in stemmed.values()) / len(stemmed)) if stemmed else 1.0
    for rel, st in stemmed.items():
        m = [w for w in words if stems[w] in st]
        if not m:
            continue
        norm = 2.2 / (1 + 1.2 * (0.25 + 0.75 * len(st) / avg_len))
        s = sum(weights[w] for w in m) * (len(m) / len(words)) ** 2 * norm
        pm = matched(tokens_of(rel))
        s += 1.5 * sum(weights[w] for w in pm)
        if is_test_path(rel):
            s *= 0.3
        if ui_question:
            s *= 0.6 if rel.endswith(".py") else 1.5
        score[rel] += s
        if len(m) == len(words):
            why[rel].append("uses every word")
        if pm:
            why[rel].append("path: " + ", ".join(pm))

    # 2. Definitions whose name or docstring matches.
    sym_hits: list[tuple[float, str, list]] = []
    for rel, defs in v.idx.get("defs", {}).items():
        for d in defs:
            nm = matched(tokens_of(d[0]))
            dm = matched(tokens_of(d[4] or "")) if d[4] else []
            if not nm and len(dm) < 2:
                continue
            s = 2.0 * sum(weights[w] for w in nm) + 0.7 * sum(weights[w] for w in dm)
            if is_test_path(rel):
                s *= 0.3
            sym_hits.append((s, rel, d))
    sym_hits.sort(key=lambda h: -h[0])
    for s, rel, d in sym_hits[:40]:
        score[rel] += s * 0.6
        why[rel].append(f"defines {d[0]}")

    # 3. Interface text: English UI strings and where their keys are used.
    # A label rarely says "box" or "button", so for those questions the other
    # words carry the match and short labels rank first.
    i18n = v.idx.get("i18n", {})
    core = [w for w in words if w not in _UI_WORDS] or words
    core_need = max(1, min(2, len(core)))
    ui_hits = []
    for key, text in i18n.get("en", {}).items():
        st = _stems(tokens_of(text)) | _stems(tokens_of(key))
        m = [w for w in core if stems[w] in st]
        if len(m) < core_need:
            continue
        s = sum(weights[w] for w in m)
        if ui_question and key.rsplit(".", 1)[-1] in ("label", "title", "placeholder", "helper", "tooltip"):
            s *= 1.3
        ui_hits.append((s, key, text))
    ui_hits.sort(key=lambda h: (-h[0], len(h[2])))
    ui_weight = 1.6 if ui_question else 0.8
    for s, key, text in ui_hits[:25]:
        for rel, line in i18n.get("uses", {}).get(key, [])[:4]:
            score[rel] += s * ui_weight
            why[rel].append(f'UI text "{_short(text, 40)}"')
            lines_hit[rel].append((line, f'UI text "{_short(text, 50)}" ({key})'))

    # 4. Routes, tools and subsystems by name.
    for r in v.snap.get("reachability", {}).get("routes", []):
        m = matched(tokens_of(r["path"]))
        if len(m) >= need:
            score[r["file"]] += sum(weights[w] for w in m)
            why[r["file"]].append(f"route {r['path']}")
            lines_hit[r["file"]].append((r["line"], f"route {','.join(r['methods'])} {r['path']}"))
    for t in v.snap.get("tool_graph", {}).get("registered_tools", []):
        m = matched(tokens_of(t["name"]) | tokens_of(t.get("description", "")))
        if len(m) >= need:
            score[t["file"]] += sum(weights[w] for w in m)
            why[t["file"]].append(f"tool {t['name']}")
            lines_hit[t["file"]].append((t["line"], f"tool {t['name']}"))
    from . import subsystems as subs
    sub_scores = []
    for sub in subs.load(v.root) or []:
        m = matched(tokens_of(sub["name"]) | tokens_of(sub["description"]))
        if len(m) >= need:
            sub_scores.append((sum(weights[w] for w in m), sub))
    sub_scores.sort(key=lambda h: -h[0])
    best_subs = {s["name"] for _, s in sub_scores[:2]}
    for rel in list(score):
        if v.files.get(rel, {}).get("subsystem") in best_subs:
            score[rel] *= 1.25

    # 5. Semantic hits from the zvec-grep index, when the checkout has one.
    zv = _zvec_hits(v.root, arg)
    for rel, line, _ in zv:
        if rel in v.files:
            score[rel] += 3.0
            why[rel].append("semantic search")
            lines_hit[rel].append((line, "semantic search hit"))

    ranked = sorted(score.items(), key=lambda kv: -kv[1])[:8]
    how = "semantic index and words" if zv else (
        "words in code, UI text, names and docstrings"
        + ("" if (v.root / ".zvec-grep").is_dir() else "; no semantic index for this checkout"))
    out.add(f"where \"{arg.strip()}\" — words: {', '.join(words)} (matched by {how})")
    if not ranked:
        out.add("Nothing in the map matches those words.")
        return out.text()
    out.add("Best files:")
    for rel, _s in ranked:
        reasons = list(dict.fromkeys(why.get(rel, [])))[:3]
        sub = v.files.get(rel, {}).get("subsystem")
        out.add(f"  {rel}" + (f" ({sub})" if sub else "") + (": " + "; ".join(reasons) if reasons else ""))
    out.add("Lines:")
    all_stems = set(stems.values())
    for rel, _s in ranked[:5]:
        for line, text in _best_lines(v.root / rel, all_stems, lines_hit.get(rel, [])):
            if not out.add(f"  {rel}:{line}  {_short(text, 110)}"):
                break
    syms = [(rel, d) for _, rel, d in sym_hits[:6]]
    if syms:
        out.add("Definitions:")
        for rel, d in syms:
            out.add("  " + _fmt_def(rel, d))
    if sub_scores:
        s = sub_scores[0][1]
        out.add(f"Subsystem: {s['name']} — {_short(s['description'], 120)}")
    return out.text()


def _best_lines(path: Path, words: set[str], known: list[tuple[int, str]], n: int = 3) -> list[tuple[int, str]]:
    """Up to ``n`` lines of ``path`` worth quoting: the known hits (UI text,
    routes) first, then the lines that use the most query words."""
    out: list[tuple[int, str]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return [(ln, why) for ln, why in known[:n]]
    used = set()
    for ln, why in sorted(set(known))[:n]:
        if 0 < ln <= len(lines) and ln not in used:
            used.add(ln)
            out.append((ln, f"{lines[ln - 1].strip()}   ← {why}"))
    if len(out) >= n:
        return out[:n]
    from .facts import tokens_of
    scored = []
    for i, text in enumerate(lines, 1):
        if i in used or len(text) > 400:
            continue
        hit = _stems(tokens_of(text)) & words
        if len(hit) >= 1:
            scored.append((len(hit), -i, i, text.strip()))
    scored.sort(reverse=True)
    for _, _, i, text in scored[: n - len(out)]:
        out.append((i, text))
    return sorted(out)


# ── Entry point ────────────────────────────────────────────────────────────

_HANDLERS = {
    "where": q_where, "module": q_module, "symbol": q_symbol, "route": q_route,
    "tool": q_tool, "subsystem": q_subsystem, "impact": q_impact, "findings": q_findings,
}

_NATURAL = [
    (re.compile(r"^(?:what|who|which \w+)\s+calls?\s+(.+?)\??$", re.I), "symbol"),
    (re.compile(r"^callers? of\s+(.+?)\??$", re.I), "symbol"),
    (re.compile(r"^(?:what|which \w+)\s+(?:depends on|imports|uses)\s+(.+?)\??$", re.I), "impact"),
    (re.compile(r"^(?:what|which tests?)\s+(?:breaks?|to run|should i run)\s+(?:if i change|for|after changing)\s+(.+?)\??$", re.I), "impact"),
    (re.compile(r"^where\s+(?:is|are|does|do)?\s*(.+?)\??$", re.I), "where"),
    (re.compile(r"^(?:what is in|what's in|tell me about)\s+(?:the\s+)?(.+?)\s+subsystem\??$", re.I), "subsystem"),
]


def parse(question: str) -> tuple[str, str]:
    """(mode, argument) for free text: "module backend/x.py", "what calls F",
    "where is the seed box". Defaults to ``where``."""
    q = (question or "").strip()
    head, _, rest = q.partition(" ")
    if head.lower() in MODES:
        return head.lower(), rest.strip()
    for rx, mode in _NATURAL:
        m = rx.match(q)
        if m:
            return mode, m.group(1).strip()
    return "where", q


def answer(root: Path, snapshot: dict, index: dict, mode: str, arg: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in _HANDLERS:
        return f"Unknown mode '{mode}'. Modes: {', '.join(MODES)}"
    if mode != "findings" and not (arg or "").strip():
        return f"{mode} needs something to look up, e.g. {mode} " + {
            "where": "image seed box", "module": "backend/services/chat_turn.py", "symbol": "run_chat_turn",
            "route": "/api/chat/unified", "tool": "generate_image", "subsystem": "chat engine",
            "impact": "backend/services/agent_tools.py"}.get(mode, "x")
    v = view_for(root, snapshot, index)
    return _HANDLERS[mode](v, arg)
