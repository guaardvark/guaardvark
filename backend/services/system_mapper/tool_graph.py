"""LLM tool inventory and how the chat reaches each tool. Read, never run.

For Guaardvark specifically, but designed to skip gracefully on other codebases:

  * Every tool class is found statically: a class whose ``name`` attribute is
    a string literal, with its file, class and line (facts.tool_classes).
  * ``backend/tools/tool_registry_init.py`` says which of those classes are
    registered and by which ``register_*`` function: direct
    ``register_tool(X())`` calls, classes the function imports and registers
    in a loop, lists imported from another module (``for tool in
    CODE_MANIPULATION_TOOLS``) and class names looked up with getattr. A
    function that returns early unless ``in_mcp_process()`` registers its tools
    for MCP clients only.
  * How the chat can reach a registered tool, from the source: the ``*_TOOLS``
    lists and pins in ``backend/services/unified_chat_engine.py`` (CORE_TOOLS
    on every turn, the others on keyword matches), names the chat engine or
    ``backend/services/turn_router.py`` route to directly, slash commands in
    ``backend/services/slash_command_executor.py``, and the semantic selector,
    which ranks every registered tool against each message when the chat
    engine defines one.
  * MCP exposure from ``backend/mcp/config.py``'s default policy.

Findings:
  - UNWIRED_TOOL — registered for chat, but no list, pin, route, slash command
    or selector can offer it.
  - UNREGISTERED_TOOL — a list names a tool that no registration provides.

Nothing here imports the mapped tree or starts a process: the map of a
checkout, or of a repository someone uploaded, never runs its code.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from . import facts as facts_mod
from .core import Finding, FindingKind, Severity, source_files

REGISTRY_INIT = "backend/tools/tool_registry_init.py"
CHAT_ENGINE = "backend/services/unified_chat_engine.py"
TURN_ROUTER = "backend/services/turn_router.py"
SLASH_EXECUTOR = "backend/services/slash_command_executor.py"
MCP_CONFIG = "backend/mcp/config.py"

# Lists a connected MCP server fills at runtime; empty in the source.
_RUNTIME_LISTS = frozenset({"MCP_NATIVE_TOOLS"})


def _parse(path: Path) -> ast.Module | None:
    if not path.is_file():
        return None
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, ValueError):
        return None


def _str_list(node) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return None


# ── Tool classes ─────────────────────────────────────────────────────────────

def tool_classes(root: Path, extra_excludes: frozenset[str] = frozenset(), corpus=None) -> dict[str, list[dict]]:
    """{module dotted name: [tool class facts + file]} for backend/ Python."""
    out: dict[str, list[dict]] = {}
    if corpus is not None:
        items = ((rel, corpus.facts[rel]) for rel in corpus.python_files() if rel.startswith("backend/"))
    else:
        backend = root / "backend"
        items = []
        if backend.is_dir():
            for py in source_files(root, extra_excludes, pattern="*.py", under=backend):
                rel = py.relative_to(root).as_posix()
                f = facts_mod.file_facts(py, rel)
                if f is not None:
                    items.append((rel, f))
    from .corpus import is_test_path, module_name
    for rel, f in items:
        if is_test_path(rel) or not f.get("tool_classes"):
            continue
        out[module_name(rel)] = [{**t, "file": rel} for t in f["tool_classes"]]
    return out


# ── Registration ─────────────────────────────────────────────────────────────

def _module_list(root: Path, module: str, list_name: str) -> list[str]:
    """Class names in ``list_name = [A(), B, ...]`` of a module, by file."""
    tree = _parse(root / (module.replace(".", "/") + ".py"))
    if tree is None:
        return []
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else \
            [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == list_name for t in targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                names = []
                for e in node.value.elts:
                    if isinstance(e, ast.Call) and isinstance(e.func, ast.Name):
                        names.append(e.func.id)
                    elif isinstance(e, ast.Name):
                        names.append(e.id)
                return names
    return []


def _guards(fn: ast.FunctionDef) -> list[str]:
    """Conditions under which a register function returns before registering
    anything: ``if not in_mcp_process(): return``."""
    out = []
    for stmt in fn.body:
        if isinstance(stmt, ast.If) and any(isinstance(s, ast.Return) for s in stmt.body):
            try:
                out.append(ast.unparse(stmt.test))
            except Exception:
                out.append("?")
        if isinstance(stmt, (ast.Try, ast.For)):
            break
    return out


def _category(fn: ast.FunctionDef) -> str | None:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "category" and isinstance(node.value, ast.Constant):
                    return node.value.value
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "_tool_categories":
                    return node.value.value
    return None


def registrations(root: Path) -> list[dict]:
    """One entry per class the registry init registers:
    {class, module, function, line, guards, category}."""
    tree = _parse(root / REGISTRY_INIT)
    if tree is None:
        return []
    out: list[dict] = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        calls_register = any(
            isinstance(n, ast.Call) and (
                (isinstance(n.func, ast.Name) and n.func.id in ("register_tool", "add_tool"))
                or (isinstance(n.func, ast.Attribute) and n.func.attr in ("register", "register_tool", "add_tool")))
            for n in ast.walk(fn))
        if not calls_register:
            continue
        imported: dict[str, tuple[str, int]] = {}   # local name -> (module, line)
        module_alias: dict[str, str] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    if a.name[:1].isupper():
                        imported[a.asname or a.name] = (node.module, node.lineno)
                    else:
                        module_alias[a.asname or a.name] = f"{node.module}.{a.name}"
        guards = _guards(fn)
        category = _category(fn)
        found: dict[tuple[str, str], int] = {}

        def add(cls: str, module: str, line: int) -> None:
            found.setdefault((cls, module), line)

        for node in ast.walk(fn):
            if isinstance(node, ast.For):
                it = node.iter
                if isinstance(it, ast.Name) and it.id in imported:
                    module, line = imported[it.id]
                    for cls in _module_list(root, module, it.id):
                        add(cls, module, node.lineno)
                elif isinstance(it, (ast.Tuple, ast.List)):
                    for e in it.elts:
                        if isinstance(e, ast.Name) and e.id in imported:
                            add(e.id, imported[e.id][0], node.lineno)
                        elif isinstance(e, (ast.Tuple, ast.List)) and e.elts and isinstance(e.elts[0], ast.Constant):
                            # ("RemoveBackgroundTool", "remove_background"): the class is
                            # looked up with getattr on a module imported in the loop.
                            for sub in ast.walk(node):
                                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                                        and sub.func.id == "getattr" and sub.args
                                        and isinstance(sub.args[0], ast.Name)
                                        and sub.args[0].id in module_alias):
                                    add(e.elts[0].value, module_alias[sub.args[0].id], node.lineno)
                elif isinstance(it, ast.Name):
                    # A local list of (class name, tool name) pairs.
                    for stmt in fn.body:
                        if (isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == it.id
                                                                for t in stmt.targets)
                                and isinstance(stmt.value, (ast.List, ast.Tuple))):
                            for e in stmt.value.elts:
                                if isinstance(e, (ast.Tuple, ast.List)) and e.elts and \
                                        isinstance(e.elts[0], ast.Constant):
                                    for sub in ast.walk(node):
                                        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                                                and sub.func.id == "getattr" and sub.args
                                                and isinstance(sub.args[0], ast.Name)
                                                and sub.args[0].id in module_alias):
                                            add(e.elts[0].value, module_alias[sub.args[0].id], node.lineno)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in imported:
                add(node.func.id, imported[node.func.id][0], node.lineno)

        for (cls, module), line in found.items():
            out.append({"class": cls, "module": module, "function": fn.name, "line": line,
                        "guards": guards, "category": category})
    return out


# ── Wiring: how the chat reaches a tool ───────────────────────────────────────

def _extract_core_tools(chat_engine: Path) -> tuple[list[str], dict[str, list[str]]]:
    """Pull every `*_TOOLS` constant from a Python file.

    Returns (union_of_all_tool_names, breakdown_by_constant_name). The agent's
    "wired" set is the union — Guaardvark splits tools across multiple lists
    (CORE_TOOLS, BROWSER_TOOLS, CODE_TOOLS, ...) and any of them counts as wired.
    Lists of pinned tools (KNOWLEDGE_NAV_TOOLS_PINNED) count too.
    """
    tree = _parse(chat_engine)
    if tree is None:
        return [], {}

    breakdown: dict[str, list[str]] = {}

    def _collect(nodes, overwrite: bool) -> None:
        for node in nodes:
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if not isinstance(tgt, ast.Name):
                    continue
                if not (tgt.id.endswith("_TOOLS") or tgt.id.endswith("_TOOLS_PINNED")):
                    continue
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                        and node.value.func.id == "frozenset" and node.value.args:
                    names = _str_list(node.value.args[0])
                else:
                    names = _str_list(node.value)
                if names and (overwrite or tgt.id not in breakdown):
                    breakdown[tgt.id] = names

    # Module-level lists are the wiring and win by name. The chat engine also
    # builds a function-local CORE_TOOLS set inside its semantic selector (the
    # tools pinned regardless of similarity); letting that inner set overwrite
    # the module-level list kept a tool that had been added to the real list
    # reported as unwired. Function-local collections still count for names
    # no module-level list defines.
    _collect(tree.body, overwrite=True)
    _collect(ast.walk(tree), overwrite=False)

    union: list[str] = []
    seen: set[str] = set()
    for names in breakdown.values():
        for n in names:
            if n not in seen:
                seen.add(n)
                union.append(n)
    return union, breakdown


def _offering_lists(path: Path) -> set[str]:
    """Names of the tool lists the chat engine offers from: those in its
    keyword table (TOOL_CONTEXT_KEYWORDS), those its _pin_* functions add,
    and CORE_TOOLS. Other *_TOOLS sets classify tools (serial, GPU-heavy)
    and offer nothing."""
    tree = _parse(path)
    out: set[str] = {"CORE_TOOLS"}
    if tree is None:
        return out
    for node in ast.walk(tree):
        scope = None
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOL_CONTEXT_KEYWORDS"
                                                for t in node.targets):
            scope = node.value
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name.startswith("_pin_") or node.name == "select_tools_for_context"):
            scope = node
        if scope is not None:
            out |= {n.id for n in ast.walk(scope) if isinstance(n, ast.Name)}
    return out


def _string_constants(path: Path) -> set[str]:
    tree = _parse(path)
    if tree is None:
        return set()
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _slash_commands(path: Path) -> dict[str, str]:
    """{tool: "/command"} from SLASH_COMMAND_TOOL_MAP."""
    tree = _parse(path)
    out: dict[str, str] = {}
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "SLASH_COMMAND_TOOL_MAP" for t in targets) \
                    and isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values):
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                        out.setdefault(v.value, f"/{k.value}")
    return out


def _has_selector(path: Path) -> bool:
    tree = _parse(path)
    if tree is None:
        return False
    return any(isinstance(n, ast.ClassDef) and n.name == "SemanticToolSelector" for n in tree.body)


def _mcp_policy(path: Path) -> dict[str, list[str]]:
    """The default MCP tool policy lists from backend/mcp/config.py."""
    tree = _parse(path)
    out = {"deny_categories": [], "allow": [], "deny": []}
    if tree is None:
        return out
    names = {"DEFAULT_DENY_CATEGORIES": "deny_categories", "DEFAULT_ALLOW_TOOLS": "allow",
             "DEFAULT_DENY_TOOLS": "deny"}
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        if target in names:
            out[names[target]] = _str_list(node.value) or []
    return out


def _mcp_exposure(name: str, category: str | None, attrs: dict, policy: dict) -> tuple[bool, str]:
    """Mirror of backend.mcp.config.tool_is_exposed over the default policy."""
    if name in policy["deny"]:
        return False, "in the default deny list"
    if name in policy["allow"]:
        return True, "in the default allow list"
    if attrs.get("is_dangerous"):
        return False, "marked dangerous"
    if attrs.get("requires_approval"):
        return False, "requires approval"
    if category and category in policy["deny_categories"]:
        return False, f"category '{category}' is denied by default"
    return True, ""


def _find_invocations(root: Path, tool_names: set[str], corpus=None) -> dict[str, list[str]]:
    """Files under backend/ that name each tool as a quoted string (tests and
    migrations included), from the per-file string facts."""
    out: dict[str, list[str]] = {name: [] for name in tool_names}
    if corpus is not None:
        items = [(rel, corpus.facts[rel]) for rel in corpus.python_files() if rel.startswith("backend/")]
    else:
        backend = root / "backend"
        items = []
        if backend.is_dir():
            for py in source_files(root, pattern="*.py", under=backend,
                                   exclude_dirs=frozenset({"__pycache__", "venv"})):
                rel = py.relative_to(root).as_posix()
                f = facts_mod.file_facts(py, rel)
                if f is not None:
                    items.append((rel, f))
    for rel, f in items:
        hits = tool_names & f.get("strings", set())
        for name in hits:
            out[name].append(rel)
    return out


# ── Analysis ─────────────────────────────────────────────────────────────────

def analyze(root: Path, extra_excludes: frozenset[str] = frozenset(), corpus=None) -> dict[str, Any]:
    """Never raises — returns an empty/INFO result on any internal failure."""
    try:
        return _analyze(Path(root).resolve(), extra_excludes, corpus)
    except Exception as exc:  # pragma: no cover - last-resort guard
        return {
            "graph": {"registered_tools": [], "core_tools": [],
                      "wired": [], "unwired": [], "unregistered": []},
            "findings": [Finding(
                kind=FindingKind.UNWIRED_TOOL,
                severity=Severity.INFO,
                summary=f"tool_graph.analyze failed internally: {exc!r}",
                evidence={"key": "analyzer-error"},
            )],
            "stats": {"applicable": False, "error": repr(exc)},
        }


def _analyze(root: Path, extra_excludes: frozenset[str], corpus) -> dict[str, Any]:
    registry_path = root / REGISTRY_INIT
    chat_path = root / CHAT_ENGINE
    classes_by_module = tool_classes(root, extra_excludes, corpus)
    regs = registrations(root)
    core_tools, breakdown = _extract_core_tools(chat_path)

    if not regs and not core_tools:
        return {
            "graph": {"registered_tools": [], "core_tools": [], "wired": [],
                      "unwired": [], "unregistered": []},
            "findings": [],
            "stats": {"applicable": False, "tool_registry_source": "static"},
        }

    # Registered tools: each registration's class resolved to its `name`.
    registered: dict[str, dict] = {}
    unresolved: list[dict] = []
    for reg in regs:
        candidates = classes_by_module.get(reg["module"], [])
        cls = next((c for c in candidates if c["class"].split(".")[-1] == reg["class"]), None)
        if cls is None:
            # Re-exported from another module: find the class by name anywhere.
            cls = next((c for cs in classes_by_module.values() for c in cs
                        if c["class"].split(".")[-1] == reg["class"]), None)
        if cls is None:
            unresolved.append(reg)
            continue
        mcp_only = any("in_mcp_process" in g and g.strip().startswith("not ") for g in reg["guards"])
        entry = registered.setdefault(cls["name"], {
            "name": cls["name"],
            "class": cls["class"],
            "file": cls["file"],
            "line": cls["line"],
            "end": cls["end"],
            "registered_in": reg["function"],
            "registry_line": reg["line"],
            "category": reg["category"],
            "conditions": reg["guards"],
            "surface": "mcp" if mcp_only else "chat+mcp",
            "attrs": cls.get("attrs", {}),
            "description": cls.get("description", ""),
        })
        entry.setdefault("also_registered_in", [])
        if reg["function"] != entry["registered_in"]:
            entry["also_registered_in"].append(reg["function"])

    defined = {c["name"]: c for cs in classes_by_module.values() for c in cs}
    tools_root = "backend/tools/"
    not_registered = sorted(
        ({"name": n, "class": c["class"], "file": c["file"], "line": c["line"]}
         for n, c in defined.items()
         if n not in registered and c["file"].startswith(tools_root)
         and any(b.endswith("Tool") for b in c.get("bases", []))),
        key=lambda d: d["name"])

    # Reach.
    list_of: dict[str, list[str]] = {}
    for list_name, names in breakdown.items():
        for n in names:
            list_of.setdefault(n, []).append(list_name)
    engine_strings = _string_constants(chat_path)
    router_strings = _string_constants(root / TURN_ROUTER)
    slash = _slash_commands(root / SLASH_EXECUTOR)
    selector = _has_selector(chat_path)
    policy = _mcp_policy(root / MCP_CONFIG)
    listed_strings = {n for names in breakdown.values() for n in names}

    offering = _offering_lists(chat_path)
    for name, entry in registered.items():
        reach: list[str] = []
        for list_name in list_of.get(name, []):
            if list_name not in offering:
                continue
            reach.append("every turn (CORE_TOOLS)" if list_name == "CORE_TOOLS" else f"list {list_name}")
        if name in router_strings:
            reach.append("turn router")
        if name in slash:
            reach.append(f"slash {slash[name]}")
        if name in engine_strings and name not in listed_strings:
            reach.append("named by the chat engine")
        if entry["surface"] == "mcp":
            reach_chat = False
        else:
            if selector:
                reach.append("semantic selector")
            reach_chat = bool(reach)
        exposed, why = _mcp_exposure(name, entry["category"], entry["attrs"], policy)
        entry["reach"] = reach
        entry["listed_in"] = list_of.get(name, [])
        entry["wired"] = reach_chat or entry["surface"] == "mcp"
        entry["mcp"] = {"exposed": exposed, "reason": why}

    registered_names = set(registered)
    wired = sorted(n for n, e in registered.items() if e["wired"])
    unwired = sorted(n for n, e in registered.items() if not e["wired"])
    runtime_filled = {n for list_name in _RUNTIME_LISTS for n in breakdown.get(list_name, [])}
    unregistered = sorted(listed_strings - registered_names - runtime_filled)

    invocations = _find_invocations(root, registered_names | listed_strings, corpus)
    findings: list[Finding] = []
    for name in unwired:
        e = registered[name]
        others = [f for f in invocations.get(name, []) if "tool_registry_init" not in f]
        findings.append(Finding(
            kind=FindingKind.UNWIRED_TOOL,
            severity=Severity.HIGH,
            summary=f"Tool '{name}' is registered for chat but no list, pin, route, slash command or selector offers it",
            paths=[e["file"], REGISTRY_INIT],
            evidence={"tool": name, "class": e["class"], "registry_line": e["registry_line"],
                      "other_references": others[:5], "key": name},
        ))
    for name in unregistered:
        in_lists = sorted(k for k, v in breakdown.items() if name in v)
        findings.append(Finding(
            kind=FindingKind.UNREGISTERED_TOOL,
            severity=Severity.HIGH,
            summary=f"{', '.join(in_lists) or 'CORE_TOOLS'} lists '{name}' but no registration found",
            paths=[CHAT_ENGINE] if chat_path.is_file() else [],
            evidence={"tool": name, "in_constants": in_lists, "key": name},
        ))

    registered_list = []
    for name, e in sorted(registered.items()):
        registered_list.append({**e, "reference_count": len(invocations.get(name, []))})

    return {
        "graph": {
            "registered_tools": registered_list,
            "core_tools": breakdown.get("CORE_TOOLS", []),
            "tool_lists": breakdown,
            "offering_lists": sorted(offering & set(breakdown)),
            "wired": wired,
            "unwired": unwired,
            "unregistered": unregistered,
            "defined_not_registered": not_registered,
            "unresolved_registrations": [f"{r['module']}.{r['class']}" for r in unresolved],
            "selector": selector,
        },
        "findings": findings,
        "stats": {
            "applicable": True,
            "registered_count": len(registered_names),
            "mcp_only_count": sum(1 for e in registered.values() if e["surface"] == "mcp"),
            "core_tool_count": len(breakdown.get("CORE_TOOLS", [])),
            "listed_count": len(listed_strings),
            "tool_lists_found": list(breakdown.keys()),
            "wired_count": len(wired),
            "unwired_count": len(unwired),
            "unregistered_count": len(unregistered),
            "tool_registry_source": "static",
            "semantic_selector": selector,
        },
    }
