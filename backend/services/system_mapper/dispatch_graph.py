"""Dynamic-dispatch registry edges (B3).

The dependency_graph analyzer is *import-only*: it builds module → set(imported
modules) from AST. That misses every module reached by a string-keyed dispatch
table — Celery task-name strings, Flask blueprint auto-registration, plugin.json
route declarations, MCP/LLM tool tables. Such modules have zero static importers
and would be (wrongly) flagged DORMANT_MODULE.

This analyzer surveys those dynamic-dispatch surfaces and produces a *set of
module names that are reached via dispatch*. `codebase_map()` passes that set
into `dependency_graph.analyze()` (and post-filters as a belt-and-suspenders),
so dynamically-reached modules are suppressed from the dormant finding.

Design contract (mirrors the sibling analyzers):
  * `analyze(root, extra_excludes=...) -> {"graph", "findings", "stats"}`.
  * Never raises. Any internal failure degrades to an empty result + a note in
    stats; the dormant-suppression simply does nothing.
  * Purely static (the per-file facts + json). No imports of the live app, no
    subprocess.

It emits NO new findings of its own — its whole job is to REDUCE false-positive
dormant findings. (`graph["dynamic_modules"]` is the payload other analyzers read.)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import facts as facts_mod
from .core import source_files


def _module_name(rel: Path) -> str:
    """Convert backend/services/foo.py -> backend.services.foo (mirror dep graph)."""
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _python_facts(root: Path, extra_excludes: frozenset[str], corpus=None, under: Path | None = None):
    """(relative path, facts) for each surveyed Python file, from the corpus
    when one is given, else from the per-file facts cache."""
    if corpus is not None:
        prefix = "" if under is None else under.relative_to(corpus.root).as_posix() + "/"
        for rel in corpus.python_files():
            if rel.startswith(prefix):
                yield Path(rel), corpus.facts[rel]
        return
    for py in source_files(root, extra_excludes, pattern="*.py", under=under):
        try:
            rel = py.relative_to(root)
        except ValueError:
            continue
        f = facts_mod.file_facts(py, rel.as_posix())
        if f is not None and f.get("error") is None:
            yield rel, f


# ── Celery task-name strings ─────────────────────────────────────────────────

def celery_tasks(root: Path, extra_excludes: frozenset[str] = frozenset(), corpus=None) -> list[dict]:
    """Every Celery task the source defines: {name, module, file, function, line}.

    ``name`` is the explicit ``name=`` when given, else Celery's default,
    ``<module>.<function>``."""
    out: list[dict] = []
    for rel, f in _python_facts(root, extra_excludes, corpus):
        mod = _module_name(rel)
        for t in f.get("tasks", []):
            name = t.get("name") or (f"{mod}.{t['function']}" if t.get("function") else None)
            if not name:
                continue
            out.append({"name": name, "module": mod, "file": rel.as_posix(),
                        "function": t.get("function"), "line": t.get("line")})
    return out


def _celery_task_modules(root: Path, extra_excludes: frozenset[str], corpus=None) -> set[str]:
    """Modules that define a Celery task (decorated def OR a `name="..."` registration).

    A module that registers a task is reachable: Celery beat / the broker invoke
    it by task-name string, never via Python import from another of our modules.
    """
    return {t["module"] for t in celery_tasks(root, extra_excludes, corpus)}


# ── Flask blueprint auto-registration ────────────────────────────────────────

def _blueprint_modules(root: Path, extra_excludes: frozenset[str], corpus=None) -> set[str]:
    """Modules defining a Flask Blueprint, plus auto-discovered backend/api/*_api.py.

    Blueprints are wired by the app factory at boot (auto_register_blueprints
    walks backend/api and imports each *_api.py); nothing in the static import
    graph points at them, so they look dormant. They are not.
    """
    reached: set[str] = set()
    for rel, f in _python_facts(root, extra_excludes, corpus):
        rel_str = rel.as_posix()
        if "backend/api/" in rel_str and rel.name.endswith("_api.py"):
            reached.add(_module_name(rel))
        elif f.get("blueprint"):
            reached.add(_module_name(rel))
    return reached


# ── plugin.json route / endpoint declarations ────────────────────────────────

def _plugin_declared_modules(root: Path) -> set[str]:
    """Backend modules a plugin.json points at via a `*module*` / `*backend*` key.

    plugin.json files mostly declare HTTP `endpoints` (sidecar paths, not our
    Python modules). But some carry a backend module/handler reference; when they
    do, follow it so a plugin-only-reached handler isn't called dormant.
    """
    reached: set[str] = set()
    # No folder is skipped by name here: a plugin.json counts wherever it sits.
    for pj in source_files(root, pattern="plugin.json", exclude_dirs=frozenset()):
        try:
            data = json.loads(pj.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for key, val in data.items():
            kl = str(key).lower()
            if not any(tok in kl for tok in ("module", "backend", "handler", "entry")):
                continue
            if isinstance(val, str) and "." in val and "/" not in val:
                reached.add(val.rsplit(":", 1)[0])
    return reached


# ── MCP / LLM tool tables ─────────────────────────────────────────────────────

def _tool_modules(root: Path, extra_excludes: frozenset[str], corpus=None) -> set[str]:
    """Modules under backend/tools/ that define a tool class (a class with a
    class-level `name = "..."`). Tools are dispatched by string name through the
    registry (chat engine + MCP tools_adapter), not imported by callers.
    """
    tools_root = root / "backend" / "tools"
    if not tools_root.is_dir():
        return set()
    return {_module_name(rel) for rel, f in _python_facts(root, extra_excludes, corpus, under=tools_root)
            if f.get("tool_classes")}


def analyze(root: Path, extra_excludes: frozenset[str] = frozenset(), corpus=None) -> dict[str, Any]:
    """Compute the set of modules reached only via dynamic dispatch. Never raises.

    Returns {"graph": {"dynamic_modules": [...], "by_source": {...},
                       "celery_tasks": [...]},
             "findings": [], "stats": {...}}.
    """
    root = Path(root).resolve()
    stats: dict[str, Any] = {"applicable": True}
    by_source: dict[str, list[str]] = {}
    dynamic: set[str] = set()
    tasks: list[dict] = []
    try:
        tasks = celery_tasks(root, extra_excludes, corpus)
    except Exception as exc:  # never raises out of analyze()
        stats["celery_task_error"] = repr(exc)

    for label, fn in (
        ("celery_task", lambda: {t["module"] for t in tasks}),
        ("blueprint", lambda: _blueprint_modules(root, extra_excludes, corpus)),
        ("plugin_json", lambda: _plugin_declared_modules(root)),
        ("tool", lambda: _tool_modules(root, extra_excludes, corpus)),
    ):
        try:
            found = fn()
        except Exception as exc:  # never raises out of analyze()
            found = set()
            stats[f"{label}_error"] = repr(exc)
        by_source[label] = sorted(found)
        dynamic |= found

    stats["dynamic_module_count"] = len(dynamic)
    stats["by_source_counts"] = {k: len(v) for k, v in by_source.items()}

    stats["celery_tasks"] = len(tasks)

    return {
        "graph": {
            "dynamic_modules": sorted(dynamic),
            "by_source": by_source,
            "celery_tasks": tasks,
        },
        # This analyzer never emits findings — its purpose is to SUPPRESS
        # false-positive dormant findings, not add new ones.
        "findings": [],
        "stats": stats,
    }
