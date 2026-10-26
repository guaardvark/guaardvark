"""Named subsystems: which part of the product each source file belongs to.

The definitions live in the mapped tree itself, at
backend/services/system_mapper/subsystems.json: a name, one plain line of
description and path globs per subsystem. A tree without that file (another
repository, a folder inside the checkout) has no subsystems and nothing is
reported.

A glob is matched with fnmatch against the path relative to the root, so a
``*`` also crosses folders. When several subsystems claim a file, the glob
with the longest literal prefix wins: an exact path beats
``backend/services/*``.
"""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any

from .core import Finding, FindingKind, Severity

DEFINITIONS = "backend/services/system_mapper/subsystems.json"
_UNCLAIMED_SAMPLE = 60


def load(root: Path) -> list[dict] | None:
    """The subsystem definitions of the tree at ``root``, or None without them."""
    path = Path(root) / DEFINITIONS
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out = []
    for item in data.get("subsystems", []):
        if isinstance(item, dict) and item.get("name") and item.get("globs"):
            out.append({"name": str(item["name"]), "description": str(item.get("description", "")),
                        "globs": [str(g) for g in item["globs"]]})
    return out


def _literal_prefix(glob: str) -> int:
    m = re.search(r"[*?\[]", glob)
    return len(glob) if m is None else m.start()


def assign(rel: str, definitions: list[dict]) -> str | None:
    """The subsystem that claims ``rel``, or None."""
    best: tuple[int, str] | None = None
    for sub in definitions:
        for glob in sub["globs"]:
            if fnmatch.fnmatchcase(rel, glob):
                score = _literal_prefix(glob)
                if best is None or score > best[0]:
                    best = (score, sub["name"])
    return best[1] if best else None


def find(name: str, definitions: list[dict]) -> dict | None:
    """The subsystem a person most likely means by ``name`` ("chat engine",
    "chat", "film-crew"): exact name, then a name containing every word."""
    want = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if not want:
        return None
    for sub in definitions:
        if re.sub(r"[^a-z0-9]+", " ", sub["name"].lower()).strip() == want:
            return sub
    words = want.split()
    hits = [s for s in definitions
            if all(w in re.sub(r"[^a-z0-9]+", " ", s["name"].lower()).split() for w in words)]
    if not hits:
        hits = [s for s in definitions if all(w in s["name"].lower() for w in words)]
    return hits[0] if hits else None


def analyze(corpus, node_meta: dict) -> dict[str, Any]:
    """Assign every surveyed source file to a subsystem; annotate node_meta
    with it and report the files none claims."""
    from .corpus import node_id

    definitions = load(corpus.root)
    if definitions is None:
        return {"graph": {}, "findings": [], "stats": {"applicable": False}}

    graph: dict[str, dict] = {
        s["name"]: {"description": s["description"], "globs": s["globs"], "files": 0, "lines": 0}
        for s in definitions
    }
    unclaimed: list[str] = []
    for rel in corpus.files:
        name = assign(rel, definitions)
        meta = node_meta.get(node_id(rel))
        if meta is not None:
            meta["subsystem"] = name
        if name is None:
            unclaimed.append(rel)
            continue
        graph[name]["files"] += 1
        graph[name]["lines"] += (corpus.facts.get(rel) or {}).get("lines", 0)

    findings: list[Finding] = []
    if unclaimed:
        findings.append(Finding(
            kind=FindingKind.NO_SUBSYSTEM,
            severity=Severity.INFO,
            summary=f"{len(unclaimed)} source files belong to no subsystem in {DEFINITIONS}",
            paths=[],
            evidence={"count": len(unclaimed), "sample": unclaimed[:_UNCLAIMED_SAMPLE], "key": "unclaimed"},
        ))
    return {
        "graph": graph,
        "findings": findings,
        "stats": {
            "applicable": True,
            "subsystems": len(definitions),
            "claimed": len(corpus.files) - len(unclaimed),
            "unclaimed": len(unclaimed),
            "unclaimed_sample": unclaimed[:_UNCLAIMED_SAMPLE],
            "empty": sorted(n for n, g in graph.items() if g["files"] == 0),
        },
    }
