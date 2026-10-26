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
    from .store import cache_dir
    return cache_dir()


def _slug(root: str | Path) -> str:
    from .store import slug
    return slug(root)


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
    from .store import write_json_atomic
    ids = load_dismissed(root)
    ids.add(finding_id)
    write_json_atomic(_dismissed_path(root), sorted(ids))
    return ids


def undismiss(root: str | Path, finding_id: str) -> set[str]:
    from .store import write_json_atomic
    ids = load_dismissed(root)
    ids.discard(finding_id)
    write_json_atomic(_dismissed_path(root), sorted(ids))
    return ids


# ── Dispatch ledger: a finding is handed to self-improvement once ────────────
#
# PendingFix rows do not carry the finding they came from, so the ledger
# records each dispatch (finding id, time, paths). A finding counts as open
# while a PendingFix created after its dispatch, for one of its files, is still
# proposed, triaged or approved, and for DISPATCH_GRACE_S after the dispatch
# while the run that will stage it may still be going.
DISPATCH_GRACE_S = 30 * 60
_OPEN_FIX_STATUSES = ("proposed", "triaged", "approved")


def _dispatched_path(root: str | Path) -> Path:
    return _store_dir() / f"{_slug(root)}.dispatched.json"


def _load_ledger(root: str | Path) -> dict:
    p = _dispatched_path(root)
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def record_dispatch(root: str | Path, finding: dict, info: dict | None = None) -> None:
    from .store import write_json_atomic
    import time as _time
    ledger = _load_ledger(root)
    ledger[finding.get("id")] = {"at": _time.time(), "paths": list(finding.get("paths") or [])[:10],
                                 **(info or {})}
    write_json_atomic(_dispatched_path(root), ledger)


def open_dispatch(root: str | Path, finding: dict) -> dict | None:
    """Why ``finding`` must not be dispatched again, or None when it may be:
    {"pending_fix_ids": [...]} for open PendingFix rows from its dispatch, or
    {"in_progress": seconds} while the dispatch is recent."""
    import time as _time
    from datetime import datetime

    entry = _load_ledger(root).get(finding.get("id"))
    if not entry:
        return None
    at = float(entry.get("at") or 0)
    paths = [p for p in (entry.get("paths") or finding.get("paths") or []) if p]
    try:
        from backend.models import PendingFix
        rows = PendingFix.query.filter(
            PendingFix.status.in_(_OPEN_FIX_STATUSES),
            PendingFix.created_at >= datetime.fromtimestamp(at),
        ).all()
        fid = str(finding.get("id"))
        ids = [r.id for r in rows
               if fid in (r.fix_description or "")
               or any((r.file_path or "").replace("\\", "/").endswith("/" + p) or r.file_path == p for p in paths)]
        if ids:
            return {"pending_fix_ids": ids}
    except Exception:
        pass  # no database here: fall back to the time window alone
    age = _time.time() - at
    if age < DISPATCH_GRACE_S:
        return {"in_progress": int(age)}
    return None


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


def mechanical_proposal(finding: dict, root: str | Path | None = None) -> dict[str, Any] | None:
    """An exact old/new text change derived from the finding's own evidence,
    for kinds whose remedy needs no judgment; staged as-is by
    submit_directed_task so no model is asked to invent it. None sends the
    finding to the agent.

    No kind qualifies today. The one that did, unwired-tool, appended the tool
    to CORE_TOOLS, which puts it in the prompt of every chat turn; a tool the
    chat cannot reach needs a person to choose the list, pin or route it
    belongs in."""
    return None


def dispatch_finding(finding: dict, priority: str = "medium", root: str | Path | None = None) -> dict[str, Any]:
    """Hand a finding to the self-improvement service as a directed task. A
    finding whose remedy is mechanical is staged as a PendingFix directly;
    the rest go to the agent, which investigates and proposes a fix. A
    finding already dispatched whose fix is still open is not sent again."""
    from backend.services.self_improvement_service import get_self_improvement_service
    if root is None:
        try:
            from backend.config import GUAARDVARK_ROOT
            root = GUAARDVARK_ROOT
        except Exception:
            root = Path(__file__).resolve().parents[3]
    held = open_dispatch(root, finding)
    if held:
        return {"success": False, "skipped": True, **held,
                "reason": _held_reason(finding, held)}
    svc = get_self_improvement_service()
    record_dispatch(root, finding)
    return svc.submit_directed_task(
        description=describe(finding),
        target_files=list(finding.get("paths") or []),
        priority=priority,
        proposal=mechanical_proposal(finding, root),
    )


def _held_reason(finding: dict, held: dict) -> str:
    if held.get("pending_fix_ids"):
        ids = ", ".join(f"#{i}" for i in held["pending_fix_ids"])
        return (f"Finding {finding.get('id')} was already dispatched and PendingFix {ids} "
                "is still open; review it in Settings instead of dispatching again.")
    return (f"Finding {finding.get('id')} was dispatched {held.get('in_progress', 0)} s ago and its "
            "run may still be staging a fix.")


def describe(finding: dict) -> str:
    """Human/agent-readable task description from a finding dict."""
    ev = finding.get("evidence") or {}
    lines = [
        f"System-map finding {finding.get('id')} [{finding.get('kind')} · {finding.get('severity')}]: "
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
