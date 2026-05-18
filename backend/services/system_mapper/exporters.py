"""SystemMap exporters: JSON (canonical) / Markdown (human) / Mermaid (graph viz)."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .core import SystemMap, Severity


def to_json(smap: SystemMap, path: str | Path) -> Path:
    """Write the canonical JSON. Self-improvement reads this."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(smap.to_dict(), indent=2, default=str))
    return p


def to_markdown(smap: SystemMap, path: str | Path) -> Path:
    """Write a human-readable report grouped by severity then kind."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append(f"# System Map — {Path(smap.root).name}")
    lines.append("")
    lines.append(f"Generated {datetime.fromtimestamp(smap.generated_at).strftime('%Y-%m-%d %H:%M')} · "
                 f"{smap.file_count} files surveyed · {len(smap.findings)} findings")
    lines.append("")

    # Headline
    lines.append("## Headline")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Modules | {smap.stats.get('dependency', {}).get('modules', 0)} |")
    lines.append(f"| Internal import edges | {smap.stats.get('dependency', {}).get('internal_edges', 0)} |")
    lines.append(f"| Import cycles | {smap.stats.get('dependency', {}).get('cycles', 0)} |")
    lines.append(f"| Backend routes | {smap.stats.get('reachability', {}).get('backend_routes', 0)} |")
    lines.append(f"| Frontend API callers | {smap.stats.get('reachability', {}).get('frontend_callers', 0)} |")
    lines.append(f"| Ghost endpoints (backend, no caller) | {smap.stats.get('reachability', {}).get('ghost_endpoints', 0)} |")
    lines.append(f"| Ghost callers (frontend, no route) | {smap.stats.get('reachability', {}).get('ghost_callers', 0)} |")
    lines.append(f"| URL prefix collisions | {smap.stats.get('reachability', {}).get('prefix_collisions', 0)} |")
    if smap.stats.get('tool', {}).get('applicable'):
        lines.append(f"| LLM tools registered | {smap.stats['tool']['registered_count']} |")
        lines.append(f"| Wired into CORE_TOOLS | {smap.stats['tool']['wired_count']} |")
        lines.append(f"| Registered but unwired | {smap.stats['tool']['unwired_count']} |")
    lines.append("")

    # Findings by severity
    by_sev = smap.findings_by_severity()
    severity_order = [Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
    severity_emoji = {
        Severity.HIGH: "🔴",
        Severity.MEDIUM: "🟡",
        Severity.LOW: "⚪",
        Severity.INFO: "ℹ️",
    }

    lines.append("## Findings")
    lines.append("")
    counts = Counter(f.severity.value for f in smap.findings)
    lines.append(" · ".join(
        f"{severity_emoji[s]} {s.value}: {counts.get(s.value, 0)}" for s in severity_order
    ))
    lines.append("")

    for sev in severity_order:
        items = by_sev[sev.value]
        if not items:
            continue
        lines.append(f"### {severity_emoji[sev]}  {sev.value.upper()} ({len(items)})")
        lines.append("")
        # Group by kind for readability
        by_kind: dict[str, list] = {}
        for f in items:
            by_kind.setdefault(f.kind.value, []).append(f)
        for kind, group in sorted(by_kind.items()):
            lines.append(f"**{kind}** — {len(group)}")
            lines.append("")
            for f in group[:15]:
                lines.append(f"- {f.summary}")
                if f.paths:
                    for fpath in f.paths[:4]:
                        lines.append(f"  - `{fpath}`")
                    if len(f.paths) > 4:
                        lines.append(f"  - … {len(f.paths) - 4} more")
            if len(group) > 15:
                lines.append(f"- … {len(group) - 15} more {kind} findings")
            lines.append("")

    # Reachability cross-section
    if smap.reachability:
        lines.append("## Reachability")
        lines.append("")
        edges = smap.reachability.get("edges", [])
        with_callers = sum(1 for e in edges if e.get("callers"))
        without_callers = sum(1 for e in edges if not e.get("callers"))
        lines.append(f"- {len(edges)} unique route paths")
        lines.append(f"- {with_callers} have at least one frontend caller")
        lines.append(f"- {without_callers} are ghost endpoints")
        lines.append("")

    # Top dependency hubs
    dep_stats = smap.stats.get("dependency", {})
    hubs = dep_stats.get("over_coupled_hubs", [])
    if hubs:
        lines.append("## Over-coupled modules (cycle hubs)")
        lines.append("")
        for h in hubs:
            lines.append(f"- `{h}`")
        lines.append("")

    # Tool graph snapshot
    if smap.tool_graph and smap.stats.get("tool", {}).get("applicable"):
        lines.append("## Tool wiring")
        lines.append("")
        wired = smap.tool_graph.get("wired", [])
        unwired = smap.tool_graph.get("unwired", [])
        unregistered = smap.tool_graph.get("unregistered", [])
        lines.append(f"**Wired ({len(wired)}):** " + ", ".join(f"`{t}`" for t in wired[:30]))
        if len(wired) > 30:
            lines.append(f"… and {len(wired) - 30} more")
        lines.append("")
        if unwired:
            lines.append(f"**Unwired ({len(unwired)}):** " + ", ".join(f"`{t}`" for t in unwired))
            lines.append("")
        if unregistered:
            lines.append(f"**Listed but unregistered ({len(unregistered)}):** " + ", ".join(f"`{t}`" for t in unregistered))
            lines.append("")

    p.write_text("\n".join(lines))
    return p


def to_mermaid(smap: SystemMap, path: str | Path, max_nodes: int = 80) -> Path:
    """Mermaid graph of the dependency cycles. Useful for visualizing the brittle parts."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Pull only modules that show up in cycles — full graph would be unreadable.
    cycle_modules: set[str] = set()
    for f in smap.findings:
        if f.kind.value == "import-cycle":
            for m in f.evidence.get("cycle", []):
                cycle_modules.add(m)

    cycle_modules = set(list(cycle_modules)[:max_nodes])
    graph = smap.dependency_graph

    lines = ["graph LR"]
    # Short-name aliases to avoid Mermaid choking on dotted names
    alias: dict[str, str] = {}
    for i, m in enumerate(sorted(cycle_modules)):
        alias[m] = f"n{i}"
        lines.append(f'  {alias[m]}["{m.split(".")[-1]}<br/><small>{m}</small>"]')

    for src in cycle_modules:
        for tgt in graph.get(src, []):
            if tgt in cycle_modules:
                lines.append(f"  {alias[src]} --> {alias[tgt]}")

    p.write_text("\n".join(lines))
    return p


def export_all(smap: SystemMap, out_dir: str | Path) -> dict[str, Path]:
    """Write JSON + Markdown + Mermaid to out_dir. Returns dict of paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return {
        "json": to_json(smap, out / "system_map.json"),
        "markdown": to_markdown(smap, out / "system_map.md"),
        "mermaid": to_mermaid(smap, out / "system_map.mmd"),
    }
