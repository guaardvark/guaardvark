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
import os
from pathlib import Path
from typing import Any

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}

# Finding kinds the agent can act on mechanically. Others (import cycles,
# over-coupling) need human architectural judgement and are surfaced but not
# pitched as one-click fixes.
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


def dispatch_finding(finding: dict, priority: str = "medium") -> dict[str, Any]:
    """Hand a finding to the self-improvement agent as a directed task. The agent
    investigates, proposes a fix (staged as a PendingFix), and runs verification."""
    from backend.services.self_improvement_service import get_self_improvement_service
    svc = get_self_improvement_service()
    return svc.submit_directed_task(
        description=describe(finding),
        target_files=list(finding.get("paths") or []),
        priority=priority,
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
