"""Function-level dead-symbol detection (vulture-lite, AST, conservative).

The dependency_graph analyzer finds dead *modules* (nothing imports them). This
analyzer goes a level deeper: it finds *functions* that are defined inside a
live module but referenced nowhere in the whole repo — the "which functions are
placebo" question.

This is necessarily a STATIC, BEST-EFFORT analysis. Python's dynamic dispatch
(getattr, Celery task-name strings, Flask endpoint registration, framework
method-name conventions) means a function can be very much alive at runtime with
zero static references. So the bias here is heavily toward false-NEGATIVES: when
in doubt, DO NOT flag. The output is advisory (INFO/LOW) and is deliberately
left OUT of actions.DISPATCHABLE_KINDS so it can never auto-dispatch to the fix
engine — a wrong call could delete real recovery logic.

Algorithm:
  1. For each internal .py module, collect every top-level + class-level
     `def`/`async def` name DEFINED (with its source path).
  2. Across the WHOLE repo, collect every NAME that is *referenced*: ast.Name
     loads, ast.Attribute attrs, decorator names, literal getattr() string
     args, and every string literal (covers dynamic dispatch / task names /
     endpoints), plus __all__ entries.
  3. A defined function is a DEAD_SYMBOL candidate iff its name appears NOWHERE
     in the referenced set except its own definition.
  4. Conservatism filters (any one of these spares a candidate):
       - dunder names (__x__)
       - the name appears in any string literal anywhere
       - the function is decorated with anything (route/task/fixture/property…)
       - test_* functions
       - common framework override method names (execute/run/handle/…)
       - names that appear in any __all__
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import facts as facts_mod
from .core import Finding, FindingKind, Severity, source_files

# Cap so a pathological repo can't flood the findings list.
_MAX_FINDINGS = 100

# Method names that frameworks/base-classes invoke by convention. A subclass
# overriding one of these is live even with no static reference to *that*
# class's version. Conservative allowlist — better a miss than a wrong flag.
_FRAMEWORK_METHODS: frozenset[str] = frozenset({
    # tool / command / handler conventions
    "execute", "run", "handle", "call", "process", "dispatch", "invoke",
    "apply", "perform", "main",
    # lifecycle / context-manager / iterator / descriptor protocol-ish
    "setup", "teardown", "start", "stop", "close", "open", "connect",
    "disconnect", "shutdown", "initialize", "finalize", "cleanup",
    "on_start", "on_stop", "on_error", "on_success", "on_failure",
    # serialization / common dunder-ish-by-name
    "to_dict", "from_dict", "to_json", "from_json", "serialize", "deserialize",
    "validate", "save", "load", "delete", "create", "update", "get", "post",
    "put", "patch", "render", "build", "compile", "parse", "format",
    # pytest / unittest non-test_ hooks
    "setup_method", "teardown_method", "setup_class", "teardown_class",
    "setUp", "tearDown",
    # celery / task conventions
    "before_start", "after_return", "on_retry",
    # web framework callbacks
    "before_request", "after_request", "teardown_request", "index",
})


def _module_defs(f: dict) -> dict[str, dict[str, bool]]:
    """{name: {decorated, is_method}} for a module's top-level functions and
    the methods of its classes (nested classes included), from its facts."""
    defs: dict[str, dict[str, bool]] = {}
    for d in f.get("defs", []):
        if d["kind"] == "class":
            continue
        meta = defs.setdefault(d["name"], {"decorated": False, "is_method": False})
        if d.get("decorated"):
            meta["decorated"] = True
        if d["kind"] == "method":
            meta["is_method"] = True
    return defs


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def analyze(root: Path, extra_excludes: frozenset[str] = frozenset(), corpus=None) -> dict[str, Any]:
    """Find functions defined but statically referenced nowhere. Never raises."""
    findings: list[Finding] = []
    stats: dict[str, Any] = {
        "modules_scanned": 0,
        "functions_defined": 0,
        "dead_symbols": 0,
        "suppressed_conservative": 0,
    }

    try:
        # 1. Every internal module's defs and references, from its facts.
        #    module_defs: rel_path_str -> {name: meta}
        module_defs: dict[str, dict[str, dict[str, bool]]] = {}
        all_referenced: set[str] = set()
        all_strings: set[str] = set()
        all_exports: set[str] = set()

        if corpus is not None:
            items = [(rel, corpus.facts[rel]) for rel in corpus.python_files()]
        else:
            items = []
            for py in source_files(root, extra_excludes, pattern="*.py"):
                try:
                    rel = py.relative_to(root).as_posix()
                except ValueError:
                    continue
                f = facts_mod.file_facts(py, rel)
                if f is not None:
                    items.append((rel, f))
        for rel, f in items:
            if f.get("error"):
                continue  # unparseable → skip, never raise
            module_defs[rel] = _module_defs(f)
            all_referenced |= f["refs"]
            all_strings |= f["strings"]
            all_exports |= f["exports"]
            stats["modules_scanned"] += 1

        # 2. Evaluate each defined function against the global reference set.
        for rel, defs in module_defs.items():
            for name, meta in defs.items():
                stats["functions_defined"] += 1

                # --- conservatism gates (any → spare it) --------------------
                if _is_dunder(name):
                    continue
                if meta["decorated"]:
                    # decorators imply external invocation (route/task/fixture/…)
                    stats["suppressed_conservative"] += 1
                    continue
                if name.startswith("test_"):
                    continue
                if meta["is_method"] and name in _FRAMEWORK_METHODS:
                    stats["suppressed_conservative"] += 1
                    continue
                if name in all_exports:
                    stats["suppressed_conservative"] += 1
                    continue
                if name in all_strings:
                    # any string literal mentions it → likely dynamic dispatch
                    stats["suppressed_conservative"] += 1
                    continue

                # --- the actual deadness test -------------------------------
                if name in all_referenced:
                    continue  # statically referenced somewhere → live

                # Dead candidate.
                stats["dead_symbols"] += 1
                if len(findings) < _MAX_FINDINGS:
                    findings.append(Finding(
                        kind=FindingKind.DEAD_SYMBOL,
                        severity=Severity.LOW if meta["is_method"] else Severity.INFO,
                        summary=f"Function never statically referenced: "
                                f"{name}() in {rel}",
                        paths=[rel],
                        evidence={
                            "symbol": name,
                            "defined_in": rel,
                            "reason": "no static reference found",
                            "key": f"{rel}::{name}",
                        },
                    ))

        # Rollup if we hit the cap.
        if stats["dead_symbols"] > _MAX_FINDINGS:
            findings.append(Finding(
                kind=FindingKind.DEAD_SYMBOL,
                severity=Severity.INFO,
                summary=f"{stats['dead_symbols'] - _MAX_FINDINGS} additional "
                        f"dead-symbol candidates suppressed (cap={_MAX_FINDINGS})",
                paths=[],
                evidence={"total": stats["dead_symbols"], "shown": _MAX_FINDINGS, "key": "rollup"},
            ))

    except Exception as e:  # absolute belt-and-suspenders: never raise
        return {
            "graph": {},
            "findings": [Finding(
                kind=FindingKind.DEAD_SYMBOL,
                severity=Severity.INFO,
                summary=f"dead_symbol analyzer internal error: {e}",
            )],
            "stats": {**stats, "error": str(e)},
        }

    return {"graph": {}, "findings": findings, "stats": stats}
