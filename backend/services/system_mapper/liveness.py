"""Liveness consensus (B4) — reconcile STATIC reachability against RUNTIME hits.

Gemma4's three-signal model, with Gemini's hard caveat baked in: a runtime
tracing window that simply didn't span a rare handler must NEVER lead to an
auto-delete. So every finding this module emits is ADVISORY ONLY and its kind is
deliberately excluded from actions.DISPATCHABLE_KINDS.

Per-symbol consensus confidence:

    confidence = 0.6 * runtime + 0.3 * registry + 0.1 * static

where each term is 1.0 if the signal is present, else 0.0:
    runtime   — the symbol fired within the last N days (SymbolHit.last_fired_at)
    registry  — the symbol is in a live registry (tool_graph / route / task)
    static    — the symbol's module is statically active/auto-loaded or imported

Findings:
    RUNTIME_ZOMBIE        (HIGH-ish, NON-dispatchable) — static_reachability True
                          but NOT fired within N days. "The map says it's wired,
                          the runtime says it never runs." Advisory: a human
                          decides whether it's dead or just a rare handler.
                          LOW when the task is started only from a route
                          handler (dispatch_graph's ``started_by``): a task a
                          person starts by hand is idle until someone asks.
    CONTEXTUAL_DISCOVERY  (INFO) — fired at runtime but has no static importer.
                          The dispatch layer reached it; the static map missed it.
    HOT_PATH_SPIKE        (INFO, optional) — fired far more than its peers in the
                          window (a hot path worth knowing about).

Evidence on every finding carries the consensus detail so a reviewer can see the
math: {confidence, runtime, registry, static, last_fired_at, window_days}.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from .core import Finding, FindingKind, Severity

# Default tracing window: a symbol that fired within this many days counts as
# "runtime-live". Wide on purpose — a narrow window manufactures zombies.
DEFAULT_WINDOW_DAYS = 30

# Consensus weights (Gemma4's model). Runtime dominates; static is a tiebreaker.
W_RUNTIME = 0.6
W_REGISTRY = 0.3
W_STATIC = 0.1

# Hot-path spike: fired at least this multiple of the median peer hit-count.
_HOT_PATH_MULTIPLE = 10
_HOT_PATH_MIN_HITS = 100


def _fired_within(last_fired_at, now: datetime, window_days: int) -> bool:
    if last_fired_at is None:
        return False
    if isinstance(last_fired_at, str):
        try:
            last_fired_at = datetime.fromisoformat(last_fired_at)
        except ValueError:
            return False
    return last_fired_at >= (now - timedelta(days=window_days))


def _registry_names(system_map: dict | Any) -> set[str]:
    """Pull the live-registry symbol identifiers from a system map dict.

    Registry-live = appears in the tool graph (registered tools), or is a known
    route/task. We accept either a SystemMap-like object or its to_dict() form.
    """
    if hasattr(system_map, "to_dict"):
        system_map = system_map.to_dict()
    names: set[str] = set()
    tg = (system_map or {}).get("tool_graph") or {}
    for entry in tg.get("registered_tools", []) or []:
        if isinstance(entry, dict) and entry.get("name"):
            names.add(str(entry["name"]))
            names.add(f"tool:{entry['name']}")
    for nm in tg.get("core_tools", []) or []:
        names.add(str(nm))
    # Routes (reachability graph) and their endpoints.
    reach = (system_map or {}).get("reachability") or {}
    for r in reach.get("routes", []) or []:
        fn = r.get("function")
        if fn:
            names.add(str(fn))
            names.add(f"route:{fn}")
    # Celery tasks the dispatch graph found registered.
    for t in (system_map or {}).get("celery_tasks") or []:
        if isinstance(t, dict) and t.get("name"):
            names.add(str(t["name"]))
            names.add(f"task:{t['name']}")
    return names


def _static_modules(system_map: dict | Any) -> set[str]:
    """Modules the static map considers active/auto-loaded or imported."""
    if hasattr(system_map, "to_dict"):
        system_map = system_map.to_dict()
    out: set[str] = set()
    node_meta = (system_map or {}).get("node_meta") or {}
    for mod, meta in node_meta.items():
        lifecycle = (meta or {}).get("lifecycle")
        importers = (meta or {}).get("importers") or 0
        if lifecycle in ("active", "auto-loaded") or importers > 0:
            out.add(mod)
    return out


def _hand_started_tasks(system_map: dict | Any) -> dict[str, list[str]]:
    """Task name -> the routes that start it, for tasks nothing else starts.

    A task on a schedule, started by other code, or with no start found is
    left out: going quiet means something there."""
    if hasattr(system_map, "to_dict"):
        system_map = system_map.to_dict()
    out: dict[str, list[str]] = {}
    for t in (system_map or {}).get("celery_tasks") or []:
        if isinstance(t, dict) and t.get("name") and t.get("started_by") == ["route"]:
            out[str(t["name"])] = list(t.get("start_routes") or [])
    return out


def compute_consensus(hit, registry_names: set[str], static_modules: set[str],
                      now: datetime, window_days: int) -> dict[str, Any]:
    """Compute the three-signal consensus for a single SymbolHit-like row.

    `hit` may be an ORM row or any object/dict exposing symbol_id, symbol_kind,
    display_name, module, last_fired_at, static_reachability, hit_count.
    """
    def _g(attr, default=None):
        if isinstance(hit, dict):
            return hit.get(attr, default)
        return getattr(hit, attr, default)

    last_fired_at = _g("last_fired_at")
    module = _g("module")
    display_name = _g("display_name")
    symbol_id = _g("symbol_id")
    static_reach = _g("static_reachability")

    runtime = 1.0 if _fired_within(last_fired_at, now, window_days) else 0.0
    registry = 1.0 if (
        (display_name and display_name in registry_names)
        or (symbol_id and symbol_id in registry_names)
    ) else 0.0
    static = 1.0 if (
        static_reach is True or (module and module in static_modules)
    ) else 0.0

    confidence = round(W_RUNTIME * runtime + W_REGISTRY * registry + W_STATIC * static, 4)
    lfa = last_fired_at.isoformat() if hasattr(last_fired_at, "isoformat") else last_fired_at
    return {
        "confidence": confidence,
        "runtime": runtime,
        "registry": registry,
        "static": static,
        "last_fired_at": lfa,
        "window_days": window_days,
    }


def analyze(hits: Iterable, system_map: dict | Any,
            window_days: int = DEFAULT_WINDOW_DAYS,
            now: datetime | None = None) -> dict[str, Any]:
    """Compute liveness-consensus findings from SymbolHit rows + the static map.

    Never raises. Returns {"findings": [...], "stats": {...}}. The findings carry
    consensus detail in `evidence`. None of the emitted kinds are dispatchable.
    """
    now = now or datetime.now()
    findings: list[Finding] = []
    stats: dict[str, Any] = {
        "window_days": window_days,
        "symbols_examined": 0,
        "runtime_zombies": 0,
        "hand_started_idle": 0,
        "contextual_discoveries": 0,
        "hot_path_spikes": 0,
    }

    try:
        registry_names = _registry_names(system_map)
        static_modules = _static_modules(system_map)
        hand_started = _hand_started_tasks(system_map)

        hits = list(hits)
        # Hot-path baseline: median hit_count across the window.
        counts = sorted(int(_get(h, "hit_count", 0) or 0) for h in hits)
        median = counts[len(counts) // 2] if counts else 0

        for h in hits:
            stats["symbols_examined"] += 1
            consensus = compute_consensus(h, registry_names, static_modules, now, window_days)

            symbol_id = _get(h, "symbol_id")
            display_name = _get(h, "display_name") or symbol_id
            module = _get(h, "module")
            static_reach = _get(h, "static_reachability")
            hit_count = int(_get(h, "hit_count", 0) or 0)
            paths = [module.replace(".", "/") + ".py"] if module else []

            # RUNTIME_ZOMBIE: static map says reachable, runtime says never fired
            # in the window. HIGH-ish severity (worth a human look) but ADVISORY —
            # NOT dispatchable (a missed-window rare handler must not auto-delete).
            task_name = symbol_id[5:] if isinstance(symbol_id, str) and symbol_id.startswith("task:") else None
            if static_reach is True and consensus["runtime"] == 0.0 and task_name in hand_started:
                stats["hand_started_idle"] += 1
                start_routes = hand_started[task_name]
                via = f" ({', '.join(start_routes)})" if start_routes else ""
                findings.append(Finding(
                    kind=FindingKind.RUNTIME_ZOMBIE,
                    severity=Severity.LOW,
                    summary=(f"Started by hand{via} and not run in {window_days}d: "
                             f"{display_name}. Expected for a task that waits for a person"),
                    paths=paths,
                    evidence={"symbol_id": symbol_id, "started_by": "route",
                              "start_routes": start_routes, **consensus},
                ))
            elif static_reach is True and consensus["runtime"] == 0.0:
                stats["runtime_zombies"] += 1
                findings.append(Finding(
                    kind=FindingKind.RUNTIME_ZOMBIE,
                    severity=Severity.HIGH,
                    summary=(f"Statically reachable but not fired in {window_days}d: "
                             f"{display_name} — verify it's not a rare handler before acting"),
                    paths=paths,
                    evidence={"symbol_id": symbol_id, **consensus},
                ))

            # CONTEXTUAL_DISCOVERY: fired at runtime but the static map has no
            # importer / inactive module. The dispatch layer reached it.
            elif consensus["runtime"] == 1.0 and consensus["static"] == 0.0:
                stats["contextual_discoveries"] += 1
                findings.append(Finding(
                    kind=FindingKind.CONTEXTUAL_DISCOVERY,
                    severity=Severity.INFO,
                    summary=(f"Fired at runtime but no static importer: "
                             f"{display_name} (reached via dynamic dispatch)"),
                    paths=paths,
                    evidence={"symbol_id": symbol_id, **consensus},
                ))

            # HOT_PATH_SPIKE (optional): fired far above the peer median.
            if (hit_count >= _HOT_PATH_MIN_HITS and median > 0
                    and hit_count >= median * _HOT_PATH_MULTIPLE):
                stats["hot_path_spikes"] += 1
                findings.append(Finding(
                    kind=FindingKind.HOT_PATH_SPIKE,
                    severity=Severity.INFO,
                    summary=(f"Hot path: {display_name} fired {hit_count}× "
                             f"(≥{_HOT_PATH_MULTIPLE}× the median of {median})"),
                    paths=paths,
                    evidence={"symbol_id": symbol_id, "hit_count": hit_count,
                              "median": median, **consensus},
                ))

    except Exception as e:  # belt-and-suspenders: liveness never breaks the map
        stats["error"] = str(e)

    return {"findings": findings, "stats": stats}


def _get(obj, attr, default=None):
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)
