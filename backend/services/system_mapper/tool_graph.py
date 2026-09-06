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
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .core import Finding, FindingKind, Severity


# Subprocess probe: import the real registry in a sanitized, offline, no-GPU
# environment and emit the actual registered tool names as JSON on stdout.
# This makes loop-registration (for tool in CODE_MANIPULATION_TOOLS: ...) visible,
# which the AST pass cannot reliably see.
_PROBE = r"""
import os, sys, json
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["OLLAMA_HOST"] = "127.0.0.1:1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["SYSTEM_MAPPER_PROBE"] = "1"
sys.path.insert(0, sys.argv[1])
try:
    from backend.tools.tool_registry_init import get_registered_tools
    names = list(get_registered_tools())
    print(json.dumps({"ok": True, "tools": names}))
except Exception as exc:
    print(json.dumps({"ok": False, "error": repr(exc)}))
"""


def _probe_runtime_registry(root: Path, timeout: float = 20.0) -> tuple[set[str], dict]:
    """Run the real tool registry in a subprocess and return its registered names.

    Returns (names, info). On any failure (timeout / nonzero exit / non-JSON /
    {"ok": False}) returns (set(), {"error": ...}) so the caller can fall back to
    the AST extraction. Never raises.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE, str(root)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return set(), {"error": "probe timed out"}
    except Exception as exc:  # pragma: no cover - defensive
        return set(), {"error": f"probe spawn failed: {exc!r}"}

    if proc.returncode != 0:
        return set(), {"error": f"probe exit {proc.returncode}: {proc.stderr.strip()[-400:]}"}

    stdout = (proc.stdout or "").strip()
    # The probe may emit log lines before the JSON; take the last JSON object line.
    payload = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if payload is None:
        return set(), {"error": "probe produced no JSON"}
    if not payload.get("ok"):
        return set(), {"error": payload.get("error", "probe reported failure")}
    names = {n for n in payload.get("tools", []) if isinstance(n, str)}
    return names, {"count": len(names)}


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

    # Map imported names -> source dotted module, so loop registration like
    # `for tool in CODE_MANIPULATION_TOOLS:` can be resolved to the list's
    # element classes (and thence their `name` attributes).
    imported_from: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported_from[alias.asname or alias.name] = node.module

    def _class_name_attr(sub_tree: ast.Module, class_name: str) -> str | None:
        """Find a class's `name = "..."` class-level attribute in a parsed module."""
        for sub in ast.walk(sub_tree):
            if isinstance(sub, ast.ClassDef) and sub.name == class_name:
                for stmt in sub.body:
                    if isinstance(stmt, ast.Assign):
                        for tgt in stmt.targets:
                            if isinstance(tgt, ast.Name) and tgt.id == "name" and \
                               isinstance(stmt.value, ast.Constant) and \
                               isinstance(stmt.value.value, str):
                                return stmt.value.value
        return None

    def _resolve_imported_list(list_name: str) -> list[str] | None:
        """Resolve a module-level list referenced in a for-loop to tool names.

        Lists like CODE_MANIPULATION_TOOLS are imported from another module.
        We follow the import to the source file, read the list's element class
        names, and resolve each class's `name = "..."` class attribute. Returns
        a list of resolved tool-name strings (best-effort), else None.
        """
        src_mod = imported_from.get(list_name)
        if not src_mod:
            return None
        rel = Path(*src_mod.split(".")).with_suffix(".py")
        # registry_init is <root>/backend/tools/tool_registry_init.py — walk up
        # to find the repo root where the dotted-module path resolves to a file.
        root_guess = registry_init
        for _ in range(8):
            root_guess = root_guess.parent
            cand = root_guess / rel
            if not cand.is_file():
                continue
            try:
                sub_tree = ast.parse(cand.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                return None
            class_names: list[str] | None = None
            for sub in ast.walk(sub_tree):
                if isinstance(sub, ast.Assign):
                    for tgt in sub.targets:
                        if isinstance(tgt, ast.Name) and tgt.id == list_name and \
                           isinstance(sub.value, (ast.List, ast.Tuple)):
                            class_names = [
                                e.func.id for e in sub.value.elts
                                if isinstance(e, ast.Call) and isinstance(e.func, ast.Name)
                            ]
            if class_names is None:
                return None
            resolved = [n for c in class_names if (n := _class_name_attr(sub_tree, c))]
            return resolved
        return None

    def _handle_for_loop(stmt: ast.For) -> bool:
        """Detect `for <v> in <NAME>: register_tool(<v>); registered.append(<v>.name)`.

        On a match, resolve <NAME> to the list's element classes and their `name`
        attributes and record each as registered. Returns True if handled as a
        registration loop (so the normal pairing logic can skip this stmt)."""
        if not isinstance(stmt.target, ast.Name):
            return False
        if not isinstance(stmt.iter, ast.Name):
            return False
        loop_var = stmt.target.id
        iter_name = stmt.iter.id
        has_register = False
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call):
                f = n.func
                fname = (f.id if isinstance(f, ast.Name)
                         else f.attr if isinstance(f, ast.Attribute) else None)
                if fname in ("register_tool", "add_tool", "register") and any(
                    isinstance(a, ast.Name) and a.id == loop_var for a in n.args
                ):
                    has_register = True
        if not has_register:
            return False
        names = _resolve_imported_list(iter_name)
        if not names:
            # Couldn't resolve, but it IS a registration loop — return True so we
            # don't mis-handle it, even though we add nothing.
            return True
        for nm in names:
            out.setdefault(nm, {"name": nm, "class": None, "line": stmt.lineno})
        return True

    def _walk_for_registrations(body: list[ast.stmt]) -> None:
        """Walk a function body; pair register_tool calls with subsequent append(name)."""
        pending_class: list[tuple[str | None, int]] = []  # (class_name, line)
        for stmt in body:
            if isinstance(stmt, ast.For) and _handle_for_loop(stmt):
                continue
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

    def _collect(nodes, overwrite: bool) -> None:
        for node in nodes:
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
    """Never raises — returns an empty/INFO result on any internal failure."""
    try:
        return _analyze(root, extra_excludes)
    except Exception as exc:  # pragma: no cover - last-resort guard
        return {
            "graph": {"registered_tools": [], "core_tools": [],
                      "wired": [], "unwired": [], "unregistered": []},
            "findings": [Finding(
                kind=FindingKind.UNWIRED_TOOL,
                severity=Severity.INFO,
                summary=f"tool_graph.analyze failed internally: {exc!r}",
            )],
            "stats": {"applicable": False, "tool_registry_source": "ast_fallback",
                      "error": repr(exc)},
        }


def _analyze(root: Path, extra_excludes: frozenset[str] = frozenset()) -> dict[str, Any]:
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
            "stats": {"applicable": False, "tool_registry_source": "ast_fallback"},
        }

    # Prefer the live registry when it can be probed: it sees loop-registered
    # tools the AST pass may miss. Fall back to AST-only when the probe fails.
    runtime_names, probe_info = _probe_runtime_registry(root)
    if runtime_names:
        tool_registry_source = "runtime"
        # Synthesize graph entries for runtime-only names not seen by the AST.
        for nm in runtime_names:
            registered.setdefault(nm, {"name": nm, "class": None, "line": None})
        registered_names = set(registered.keys()) | runtime_names
    else:
        tool_registry_source = "ast_fallback"
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
            "registered_count": len(registered_names),
            "core_tool_count": len(core_tools),
            "tool_lists_found": list(breakdown.keys()),
            "wired_count": len(wired),
            "unwired_count": len(unwired),
            "unregistered_count": len(unregistered),
            "tool_registry_source": tool_registry_source,
            "runtime_probe": probe_info,
            "runtime_tool_count": len(runtime_names),
        },
    }
