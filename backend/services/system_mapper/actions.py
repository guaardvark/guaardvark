"""Action loop for system_mapper findings — turns analysis into work.

This is the difference between a read-only map and a useful tool. Findings can be:
  * ranked (high → info) and filtered,
  * dismissed (persisted, so acknowledged findings stop nagging across re-runs),
  * dispatched to the existing self-improvement agent (submit_directed_task),
    which proposes a real fix staged as a PendingFix for human review.

No fabricated diffs live here — the mapper hands a real finding to the real fix
engine rather than pretending to auto-repair. Operates on the snapshot dict the
API already holds (findings carry a stable `id` from Finding.fingerprint()).
"""
from __future__ import annotations

import json
import re
import os
from pathlib import Path
from typing import Any

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}

# Finding kinds the agent can act on mechanically. Others (import cycles,
# over-coupling) need human architectural judgement and are surfaced but not
# pitched as one-click fixes.
#
# DELIBERATELY EXCLUDED — never add these:
#   * dead-symbol           — static dead-code is best-effort; a wrong call deletes
#                             real recovery logic (see dead_symbol.py docstring).
#   * runtime-zombie / contextual-discovery / hot-path-spike (B4 liveness kinds) —
#     these come from a RUNTIME TRACING WINDOW. A window that simply didn't span a
#     once-a-month handler must NEVER auto-delete it. Liveness findings are advisory
#     drift signals for a human, not auto-fix candidates. Adding them here would
#     turn a missed trace into a destructive prune. Keep them out, permanently.
DISPATCHABLE_KINDS = frozenset({
    "url-path-collision", "url-prefix-collision", "ghost-api-caller",
    "unwired-tool", "unregistered-tool", "backup-artifact",
})


def _store_dir() -> Path:
    storage = Path(
        os.environ.get("GUAARDVARK_STORAGE_DIR")
        or os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data")
    )
    d = storage / "cache" / "system_map"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(root: str | Path) -> str:
    parts = [p for p in Path(root).resolve().parts if p not in ("", "/", "\\")]
    return "_".join(parts[-3:]).replace("/", "_") or "default"


def _dismissed_path(root: str | Path) -> Path:
    return _store_dir() / f"{_slug(root)}.dismissed.json"


def load_dismissed(root: str | Path) -> set[str]:
    p = _dismissed_path(root)
    if not p.is_file():
        return set()
    try:
        return set(json.loads(p.read_text()))
    except (json.JSONDecodeError, OSError):
        return set()


def dismiss(root: str | Path, finding_id: str) -> set[str]:
    ids = load_dismissed(root)
    ids.add(finding_id)
    _dismissed_path(root).write_text(json.dumps(sorted(ids)))
    return ids


def undismiss(root: str | Path, finding_id: str) -> set[str]:
    ids = load_dismissed(root)
    ids.discard(finding_id)
    _dismissed_path(root).write_text(json.dumps(sorted(ids)))
    return ids


def ranked_findings(snapshot: dict, root: str | Path,
                    include_dismissed: bool = False) -> list[dict]:
    """Findings sorted high → info, annotated with `dismissed` + `dispatchable`."""
    dismissed = load_dismissed(root)
    out: list[dict] = []
    for d in snapshot.get("findings", []):
        is_dismissed = d.get("id") in dismissed
        if is_dismissed and not include_dismissed:
            continue
        out.append({
            **d,
            "dismissed": is_dismissed,
            "dispatchable": d.get("kind") in DISPATCHABLE_KINDS,
        })
    out.sort(key=lambda d: (_SEVERITY_RANK.get(d.get("severity"), 9),
                            d.get("kind", ""), d.get("summary", "")))
    return out


def find_finding(snapshot: dict, finding_id: str) -> dict | None:
    for d in snapshot.get("findings", []):
        if d.get("id") == finding_id:
            return d
    return None


# The file whose module-level *_TOOLS lists are the agent's wiring, and the
# list a newly wired tool goes into. tool_graph.py reads the same file.
WIRING_FILE = "backend/services/unified_chat_engine.py"
WIRING_LIST = "CORE_TOOLS"

_LIST_BLOCK = re.compile(r"^%s\s*=\s*\[\n(?P<body>(?:.*\n)*?)\]" % WIRING_LIST, re.M)


def mechanical_proposal(finding: dict, root: str | Path | None = None) -> dict[str, Any] | None:
    """A proposal derived from the finding's own evidence, for kinds whose
    remedy is fully determined by it. Today: unwired-tool, which is one line
    in one list. Returned as the exact old/new text stage_pending_fix takes,
    so the queue holds a real diff and no model is asked to invent one.
    Kinds that need judgment return None and go to the agent."""
    if finding.get("kind") != "unwired-tool":
        return None
    tool = ((finding.get("evidence") or {}).get("tool") or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tool):
        return None
    base = Path(root) if root else Path(__file__).resolve().parents[3]
    path = base / WIRING_FILE
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    m = _LIST_BLOCK.search(text)
    if not m:
        return None
    old_text = m.group(0)
    if re.search(r'^\s*"%s"\s*,' % re.escape(tool), m.group("body"), re.M):
        return None  # already wired; the finding is stale, let the map refresh
    body = m.group("body").rstrip("\n")
    new_text = (
        f"{WIRING_LIST} = [\n{body}\n"
        f'    "{tool}",  # wired by system-map finding {finding.get("id")}\n]'
    )
    ev = finding.get("evidence") or {}
    where = f"{ev.get('registry_line')}" if ev.get("registry_line") else "?"
    return {
        "path": WIRING_FILE,
        "old_text": old_text,
        "new_text": new_text,
        "description": (
            f"Add '{tool}' to {WIRING_LIST} in {WIRING_FILE} so the agent can reach it. "
            f"The tool is registered (tool_registry_init.py line {where}) but absent from "
            f"every *_TOOLS list, so no agent could call it. Mechanical proposal built from "
            f"system-map finding {finding.get('id')}; no model involved. Review whether "
            f"{WIRING_LIST} is the right list before applying."
        ),
        "severity": finding.get("severity") or "medium",
    }


def dispatch_finding(finding: dict, priority: str = "medium", root: str | Path | None = None) -> dict[str, Any]:
    """Hand a finding to the self-improvement service as a directed task. A
    finding whose remedy is mechanical is staged as a PendingFix directly;
    the rest go to the agent, which investigates and proposes a fix."""
    from backend.services.self_improvement_service import get_self_improvement_service
    svc = get_self_improvement_service()
    return svc.submit_directed_task(
        description=describe(finding),
        target_files=list(finding.get("paths") or []),
        priority=priority,
        proposal=mechanical_proposal(finding, root),
    )


def describe(finding: dict) -> str:
    """Human/agent-readable task description from a finding dict."""
    ev = finding.get("evidence") or {}
    lines = [
        f"System-map finding [{finding.get('kind')} · {finding.get('severity')}]: "
        f"{finding.get('summary')}"
    ]
    paths = finding.get("paths") or []
    if paths:
        lines.append("Affected files: " + ", ".join(paths))
    if ev:
        lines.append("Evidence: " + json.dumps(ev, default=str)[:600])
    lines.append(
        "Investigate and fix if appropriate. If this is intentional and no change "
        "is warranted, explain why instead of editing."
    )
    return "\n".join(lines)
