"""SystemMap data model + codebase_map() orchestrator.

The SystemMap is the canonical output: three sub-graphs (dependency, reachability,
tool) plus a flat list of Findings that downstream consumers (self-improvement,
LLM agent context, human readers) can iterate.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class Severity(str, Enum):
    HIGH = "high"      # broken/colliding code reachable in production
    MEDIUM = "medium"  # brittleness — works today, fragile under refactor
    LOW = "low"        # hygiene — dormant code, missing tests
    INFO = "info"      # observation, not a defect


class FindingKind(str, Enum):
    URL_PATH_COLLISION = "url-path-collision"
    URL_PREFIX_COLLISION = "url-prefix-collision"
    GHOST_ENDPOINT = "ghost-endpoint"          # backend route, no frontend caller
    GHOST_API_CALLER = "ghost-api-caller"      # frontend fetch, no backend route
    IMPORT_CYCLE = "import-cycle"
    OVER_COUPLED = "over-coupled"              # module appears in many cycles
    UNWIRED_TOOL = "unwired-tool"              # registered, not in CORE_TOOLS
    UNREGISTERED_TOOL = "unregistered-tool"    # in CORE_TOOLS, not registered
    UNTESTED_MODULE = "untested-module"
    DORMANT_MODULE = "dormant-module"          # no static importers
    BACKUP_ARTIFACT = "backup-artifact"        # .BACK / __BACKUP / _BACK files


@dataclass
class Finding:
    """One actionable observation. Consumed by self-improvement, surfaced to LLM."""
    kind: FindingKind
    severity: Severity
    summary: str                       # one-line human description
    paths: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["severity"] = self.severity.value
        return d


@dataclass
class SystemMap:
    """Complete x-ray of a codebase at one point in time."""
    root: str
    generated_at: float
    languages: list[str]                                 # ['python', 'javascript']
    file_count: int                                       # source files surveyed

    # Sub-graphs (each module is responsible for its own data shape)
    dependency_graph: dict = field(default_factory=dict)
    reachability: dict = field(default_factory=dict)
    tool_graph: dict = field(default_factory=dict)

    # Flat findings — the bridge to self-improvement
    findings: list[Finding] = field(default_factory=list)

    # Stats — useful for the markdown report header
    stats: dict = field(default_factory=dict)

    def findings_by_severity(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {s.value: [] for s in Severity}
        for f in self.findings:
            out[f.severity.value].append(f)
        return out

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "languages": self.languages,
            "file_count": self.file_count,
            "dependency_graph": self.dependency_graph,
            "reachability": self.reachability,
            "tool_graph": self.tool_graph,
            "findings": [f.to_dict() for f in self.findings],
            "stats": self.stats,
        }


# Default exclusions — anything in these directory names is skipped at any depth.
# Consumers can override via codebase_map(..., extra_excludes=...).
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    # Python venvs (any name pattern)
    "venv", ".venv", "venv-music", "venv-tts", "env", "site-packages",
    # Build / cache / VCS
    "__pycache__", "node_modules", ".git", ".next",
    "dist", "build", "htmlcov", ".pytest_cache", "coverage",
    ".cursor", ".vscode", ".ipynb_checkpoints",
    # Vendored ML / heavy libs (Guaardvark-specific)
    "ComfyUI", "voice",
    # Stale swarm worktrees
    ".swarm-worktrees",
    # Non-source data dirs (Guaardvark-specific but generally safe)
    "data", "logs", "backups", "pids", "plans", "audit", "outputs",
    # Migrations rarely have testable logic
    "migrations",
})


def is_excluded(path: Path, extra_excludes: frozenset[str] = frozenset()) -> bool:
    excludes = DEFAULT_EXCLUDE_DIRS | extra_excludes
    return any(part in excludes for part in path.parts)


def codebase_map(
    root_path: str | Path,
    extra_excludes: frozenset[str] = frozenset(),
) -> SystemMap:
    """Generate a SystemMap for the codebase at root_path.

    Imports each analyzer locally so a failure in one doesn't break the others —
    self-improvement consumers want partial results when one analyzer chokes.
    """
    from . import dependency_graph, reachability, tool_graph

    root = Path(root_path).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    smap = SystemMap(
        root=str(root),
        generated_at=time.time(),
        languages=["python", "javascript"],
        file_count=0,
        stats={},
    )

    # 1. Dependency graph (cheapest, must run first — others may use its file list)
    try:
        dep_result = dependency_graph.analyze(root, extra_excludes)
        smap.dependency_graph = dep_result["graph"]
        smap.findings.extend(dep_result["findings"])
        smap.file_count = dep_result["file_count"]
        smap.stats["dependency"] = dep_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.IMPORT_CYCLE,
            severity=Severity.INFO,
            summary=f"dependency_graph analyzer failed: {e}",
        ))

    # 2. Reachability (frontend ↔ backend)
    try:
        reach_result = reachability.analyze(root, extra_excludes)
        smap.reachability = reach_result["graph"]
        smap.findings.extend(reach_result["findings"])
        smap.stats["reachability"] = reach_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.GHOST_ENDPOINT,
            severity=Severity.INFO,
            summary=f"reachability analyzer failed: {e}",
        ))

    # 3. Tool graph (Guaardvark-specific; gracefully no-ops elsewhere)
    try:
        tool_result = tool_graph.analyze(root, extra_excludes)
        smap.tool_graph = tool_result["graph"]
        smap.findings.extend(tool_result["findings"])
        smap.stats["tool"] = tool_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.UNWIRED_TOOL,
            severity=Severity.INFO,
            summary=f"tool_graph analyzer failed: {e}",
        ))

    return smap
