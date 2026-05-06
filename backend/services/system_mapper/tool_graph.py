"""LLM tool registry × invocation map.

For Guaardvark specifically, but designed to skip gracefully on other codebases:

  * Discovers tool registrations in `backend/tools/tool_registry_init.py`
    (any `*_registry.register(...)` call or `register_all_tools()` body).
  * Reads `CORE_TOOLS` (or equivalent constant list) from
    `backend/services/unified_chat_engine.py` to see which registered tools
    the LLM is actually allowed to call.
  * Reports:
      - UNWIRED_TOOL — registered, not in CORE_TOOLS (the April 14 hazard
        about disconnected memory tools)
      - UNREGISTERED_TOOL — listed in CORE_TOOLS but no registration found
        (likely a typo or a refactor leftover)

If neither file exists, returns empty results without raising.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .core import Finding, FindingKind, Severity


def _extract_registered_tools(registry_init: Path) -> dict[str, dict]:
    """Parse tool_registry_init.py-shaped file and pull out tool registrations.

    Handles three Guaardvark-style patterns:
      1. `register_tool(WordPressContentTool())`           — top-level function call
      2. `<something>.register(WordPressContentTool())`     — method call
      3. `<something>.register("name", ToolClass(...))`     — name-first variant

    Pattern 1+2: the tool's canonical name is whatever the very next
    `<list>.append("<name>")` statement in the same function adds. We walk the
    function body in order and pair `register_tool(...)` calls with the
    immediately-following `registered.append(...)` call.
    """
    out: dict[str, dict] = {}
    if not registry_init.is_file():
        return out
    try:
        text = registry_init.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(text)
    except Exception:
        return out

    def _is_register_call(call: ast.Call) -> tuple[bool, str | None]:
        """Returns (is_register_call, class_name_if_inferable)."""
        func = call.func
        # Pattern 1: register_tool(...)
        if isinstance(func, ast.Name) and func.id in ("register_tool", "add_tool"):
            for arg in call.args:
                if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                    return True, arg.func.id
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    return True, None  # name-first variant
            return True, None
        # Pattern 2/3: <obj>.register(...)
        if isinstance(func, ast.Attribute) and func.attr in ("register", "register_tool", "add_tool"):
            for arg in call.args:
                if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                    return True, arg.func.id
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    return True, None
            return True, None
        return False, None

    def _walk_for_registrations(body: list[ast.stmt]) -> None:
        """Walk a function body; pair register_tool calls with subsequent append(name)."""
        pending_class: list[tuple[str | None, int]] = []  # (class_name, line)
        for stmt in body:
            # Inspect the statement for register_tool calls
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call):
                    is_reg, cls = _is_register_call(node)
                    if is_reg:
                        pending_class.append((cls, node.lineno))
                    # Pattern 3: <obj>.register("name", X())
                    elif (isinstance(node.func, ast.Attribute) and
                          node.func.attr in ("register", "register_tool", "add_tool") and
                          node.args and isinstance(node.args[0], ast.Constant)):
                        nm = node.args[0].value
                        out[nm] = {"name": nm, "class": None, "line": node.lineno}
                # Look for `<list>.append("<name>")`
                if (isinstance(node, ast.Call) and
                    isinstance(node.func, ast.Attribute) and node.func.attr == "append" and
                    node.args and isinstance(node.args[0], ast.Constant) and
                    isinstance(node.args[0].value, str) and pending_class):
                    nm = node.args[0].value
                    cls, line = pending_class.pop(0)
                    out[nm] = {"name": nm, "class": cls, "line": line}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _walk_for_registrations(node.body)
    # Also catch any module-level register calls
    _walk_for_registrations(tree.body)

    return out


def _extract_core_tools(chat_engine: Path) -> tuple[list[str], dict[str, list[str]]]:
    """Pull every `*_TOOLS` constant from a Python file.

    Returns (union_of_all_tool_names, breakdown_by_constant_name). The agent's
    "wired" set is the union — Guaardvark splits tools across multiple lists
    (CORE_TOOLS, BROWSER_TOOLS, CODE_TOOLS, ...) and any of them counts as wired.
    """
    if not chat_engine.is_file():
        return [], {}
    try:
        text = chat_engine.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(text)
    except Exception:
        return [], {}

    breakdown: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if not isinstance(tgt, ast.Name):
                continue
            # Catch CORE_TOOLS, BROWSER_TOOLS, AGENT_TOOLS, etc. — any *_TOOLS
            if not (tgt.id.endswith("_TOOLS") or tgt.id == "CORE_TOOLS"):
                continue
            if not isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                continue
            names = [
                e.value for e in node.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if names:
                breakdown[tgt.id] = names

    union: list[str] = []
    seen: set[str] = set()
    for names in breakdown.values():
        for n in names:
            if n not in seen:
                seen.add(n)
                union.append(n)
    return union, breakdown


def _find_invocations(root: Path, tool_names: set[str]) -> dict[str, list[str]]:
    """Where does each tool name show up as a quoted string in backend code?

    Cheap text grep — captures references in `execute_tool("foo")`,
    `tool_name == "foo"`, prompts, schemas, etc. Fewer false positives than
    full AST analysis would give us, since tools are most often referenced as
    plain strings.
    """
    out: dict[str, list[str]] = {name: [] for name in tool_names}
    backend = root / "backend"
    if not backend.is_dir():
        return out
    name_re = {name: re.compile(rf"""['"]\b{re.escape(name)}\b['"]""") for name in tool_names}
    for py in backend.rglob("*.py"):
        if "/__pycache__/" in str(py) or "/venv/" in str(py):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = str(py.relative_to(root))
        for name, rx in name_re.items():
            if rx.search(text):
                out[name].append(rel)
    return out


def analyze(root: Path, extra_excludes: frozenset[str] = frozenset()) -> dict[str, Any]:
    registry_path = root / "backend" / "tools" / "tool_registry_init.py"
    chat_path = root / "backend" / "services" / "unified_chat_engine.py"

    registered = _extract_registered_tools(registry_path)
    core_tools, breakdown = _extract_core_tools(chat_path)

    # No Guaardvark-shaped tool layer? Empty graph, no findings.
    if not registered and not core_tools:
        return {
            "graph": {
                "registered_tools": [],
                "core_tools": [],
                "wired": [],
                "unwired": [],
                "unregistered": [],
            },
            "findings": [],
            "stats": {"applicable": False},
        }

    registered_names = set(registered.keys())
    core_set = set(core_tools)
    wired = sorted(registered_names & core_set)
    unwired = sorted(registered_names - core_set)
    unregistered = sorted(core_set - registered_names)

    # Where is each tool referenced?
    invocations = _find_invocations(root, registered_names | core_set)

    findings: list[Finding] = []

    for name in unwired:
        # If it's referenced in only the registry init file itself, it's truly
        # disconnected. If multiple files reference it, the agent might still
        # reach it via some other path — soften severity.
        invocation_files = [f for f in invocations.get(name, []) if "tool_registry_init" not in f]
        sev = Severity.HIGH if not invocation_files else Severity.MEDIUM
        findings.append(Finding(
            kind=FindingKind.UNWIRED_TOOL,
            severity=sev,
            summary=f"Tool '{name}' is registered but not in CORE_TOOLS — agent cannot call it",
            paths=[str(registry_path.relative_to(root))],
            evidence={
                "tool": name,
                "class": registered[name].get("class"),
                "registry_line": registered[name].get("line"),
                "other_references": invocation_files[:5],
            },
        ))

    for name in unregistered:
        # Find which constant lists this tool
        in_lists = [k for k, v in breakdown.items() if name in v]
        findings.append(Finding(
            kind=FindingKind.UNREGISTERED_TOOL,
            severity=Severity.HIGH,
            summary=f"{', '.join(in_lists) or 'CORE_TOOLS'} lists '{name}' but no registration found",
            paths=[str(chat_path.relative_to(root))] if chat_path.is_file() else [],
            evidence={"tool": name, "in_constants": in_lists},
        ))

    return {
        "graph": {
            "registered_tools": [
                {"name": k, **{kk: vv for kk, vv in v.items() if kk != "name"},
                 "wired": k in core_set,
                 "reference_count": len(invocations.get(k, [])),
                 }
                for k, v in registered.items()
            ],
            "core_tools": core_tools,
            "tool_lists": breakdown,
            "wired": wired,
            "unwired": unwired,
            "unregistered": unregistered,
        },
        "findings": findings,
        "stats": {
            "applicable": True,
            "registered_count": len(registered),
            "core_tool_count": len(core_tools),
            "tool_lists_found": list(breakdown.keys()),
            "wired_count": len(wired),
            "unwired_count": len(unwired),
            "unregistered_count": len(unregistered),
        },
    }
