"""SystemMap data model + codebase_map() orchestrator.

The SystemMap is the canonical output: the import graph (load-time and
inside-function edges), per-module metadata, reachability between frontend and
backend, the tool inventory, Celery tasks and subsystems, plus a flat list of
Findings that downstream consumers (self-improvement, the chat model, human
readers) can iterate.
"""
from __future__ import annotations

import fnmatch
import os
import subprocess
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
    UNWIRED_TOOL = "unwired-tool"              # registered, nothing in chat can offer it
    UNREGISTERED_TOOL = "unregistered-tool"    # named in a tool list, not registered
    UNTESTED_MODULE = "untested-module"
    DORMANT_MODULE = "dormant-module"          # nothing outside tests imports or starts it
    BACKUP_ARTIFACT = "backup-artifact"        # .BACK / __BACKUP / _BACK files
    DEAD_SYMBOL = "dead-symbol"                # function defined, statically referenced nowhere
    # B4: liveness-consensus kinds. NONE of these are dispatchable (see
    # actions.DISPATCHABLE_KINDS) — a tracing window that misses a once-a-month
    # handler must never auto-delete it. They are advisory drift signals only.
    RUNTIME_ZOMBIE = "runtime-zombie"          # statically reachable but not fired in N days
    HOT_PATH_SPIKE = "hot-path-spike"          # unusually high recent hit rate
    CONTEXTUAL_DISCOVERY = "contextual-discovery"  # fired at runtime but no static importer
    NO_SUBSYSTEM = "no-subsystem"              # source files subsystems.json does not claim


@dataclass
class Finding:
    """One actionable observation. Consumed by self-improvement, surfaced to LLM."""
    kind: FindingKind
    severity: Severity
    summary: str                       # one-line human description
    paths: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable id across re-runs: same defect → same id, so dismissals and
        dispatched-task state survive re-analysis.

        Built from the kind, the paths (in any order) and ``evidence["key"]``,
        the analyzer's name for the defect (a route, a tool, a module). The
        summary is not part of it: summaries carry counts ("participates in 7
        cycles") that change while the defect stays the same. A finding
        without a key falls back to its summary. Severity is never part of
        it, so re-tuning severities keeps ids."""
        import hashlib
        key = self.evidence.get("key") if isinstance(self.evidence, dict) else None
        stable = f"key:{key}" if key is not None else self.summary
        basis = "|".join([self.kind.value, stable, *sorted(self.paths)])
        return hashlib.sha1(basis.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["severity"] = self.severity.value
        d["id"] = self.fingerprint()
        return d


@dataclass
class SystemMap:
    """Complete x-ray of a codebase at one point in time."""
    root: str
    generated_at: float
    languages: list[str]                                 # ['python', 'javascript']
    file_count: int                                       # source files surveyed

    # Sub-graphs (each module is responsible for its own data shape)
    dependency_graph: dict = field(default_factory=dict)  # module -> modules it imports at load time
    lazy_imports: dict = field(default_factory=dict)      # module -> modules it imports inside functions
    node_meta: dict = field(default_factory=dict)   # module -> {path, lang, lifecycle, importers, subsystem, ...}
    reachability: dict = field(default_factory=dict)
    tool_graph: dict = field(default_factory=dict)
    celery_tasks: list = field(default_factory=list)      # [{name, module, file, function, line}]
    subsystems: dict = field(default_factory=dict)        # {name: {description, files, ...}}

    # Flat findings — the bridge to self-improvement
    findings: list[Finding] = field(default_factory=list)

    # Stats — useful for the markdown report header
    stats: dict = field(default_factory=dict)

    # The query layer's index (index.build); kept out of to_dict(), which is
    # what the page and the findings feed load.
    index: dict | None = field(default=None, repr=False, compare=False)

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
            "lazy_imports": self.lazy_imports,
            "node_meta": self.node_meta,
            "reachability": self.reachability,
            "tool_graph": self.tool_graph,
            "celery_tasks": self.celery_tasks,
            "subsystems": self.subsystems,
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


def is_excluded(
    path: Path,
    extra_excludes: frozenset[str] = frozenset(),
    root: Path | None = None,
) -> bool:
    """True when a folder on ``path`` has an excluded name.

    With ``root``, only the parts below ``root`` are tested, so a checkout that
    itself sits under a folder named data, build or env is still mapped.
    Without it every part of ``path`` is tested, which suits a path that is
    already relative to the mapped root.
    """
    return _has_excluded_part(path, DEFAULT_EXCLUDE_DIRS | extra_excludes, root)


def _has_excluded_part(path: Path, excludes: frozenset[str], root: Path | None) -> bool:
    parts = path.parts
    if root is not None:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            pass
    return any(part in excludes for part in parts)


# How long the mapper waits for git to list a checkout before it walks the tree
# itself. Measured 2026-09-30 on one workstation: `git ls-files --cached
# --others --exclude-standard` answers in 0.004 s for a checkout of 2,235
# tracked files that also holds about 49,000 ignored .py files (scratch folders
# and worktree copies). 30 s is the limit the code tools use for the same
# listing (llama_code_tools._source_files); it is only reached on a stalled disk.
GIT_LIST_TIMEOUT_S = 30.0

# Set by git for hooks and by callers that point it at another repository.
# Left in place they would make the listing describe that repository.
_GIT_LOCATION_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def _git_listed_files(start: Path) -> list[str] | None:
    """Paths below ``start``, relative to it, as git sees them: tracked files
    plus untracked files it does not ignore. None when ``start`` is not inside
    a git work tree, git is not installed or refuses the repository, or the
    listing does not finish in GIT_LIST_TIMEOUT_S."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_ENV}
    try:
        proc = subprocess.run(
            ["git", "-C", str(start), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True, timeout=GIT_LIST_TIMEOUT_S, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [os.fsdecode(entry) for entry in proc.stdout.split(b"\0") if entry]


def _walked_files(start: Path, exclude_dirs: frozenset[str]) -> list[Path]:
    """Every file below ``start``, not descending into folders named in
    ``exclude_dirs`` or into symlinked folders."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        found.extend(Path(dirpath, name) for name in filenames)
    return found


def source_files(
    root: Path,
    extra_excludes: frozenset[str] = frozenset(),
    *,
    pattern: str = "*",
    under: Path | None = None,
    exclude_dirs: frozenset[str] | None = None,
) -> list[Path]:
    """The files the mapper surveys, as sorted absolute paths.

    The one place the analyzers get their file lists from. ``pattern`` is a
    glob on the file name ("*.py", "plugin.json"); ``under`` narrows the
    listing to a folder inside ``root``.

    Inside a git work tree the listing is git's: tracked files plus untracked
    files git does not ignore. Ignored local data (scratch folders, worktree
    copies, virtualenvs) is therefore not mapped, a nested repository is not
    entered, and a root that git ignores maps as empty. Anywhere else, or when
    git cannot be asked, the tree is walked.

    Either way a file is left out when a folder or file name on its path below
    ``root`` is in DEFAULT_EXCLUDE_DIRS or ``extra_excludes``. ``exclude_dirs``
    replaces that set for an analyzer with its own rule.
    """
    start = under if under is not None else root
    excludes = (DEFAULT_EXCLUDE_DIRS | extra_excludes) if exclude_dirs is None else exclude_dirs
    listed = _git_listed_files(start)
    if listed is None:
        candidates = _walked_files(start, excludes)
    else:
        # A trailing slash marks a nested repository.
        candidates = [start / rel for rel in listed if not rel.endswith("/")]

    found: list[Path] = []
    for path in candidates:
        if not fnmatch.fnmatchcase(path.name, pattern):
            continue
        if _has_excluded_part(path, excludes, root):
            continue
        # git also lists tracked files deleted from the working tree, and a
        # symlink to a folder as one entry.
        if listed is not None and not path.is_file():
            continue
        found.append(path)
    return sorted(found)


def filter_findings(
    findings: list[dict],
    *,
    severities: set[str] | None = None,
    kinds: set[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Filter a list of finding dicts by severity / kind, then optionally cap.

    Operates on the dict form (as returned by Finding.to_dict() / cached in the
    snapshot). `severities` and `kinds`, when given, are inclusive allow-sets;
    `None` means "no filter on that axis". `limit` caps the result length.
    Order is preserved (callers rank before filtering)."""
    out = findings
    if severities:
        out = [f for f in out if f.get("severity") in severities]
    if kinds:
        out = [f for f in out if f.get("kind") in kinds]
    if limit is not None and limit >= 0:
        out = out[:limit]
    return list(out)


def _path_to_module(rel_str: str) -> str:
    """Convert a rel path like 'backend/services/foo.py' to 'backend.services.foo'.

    Mirrors dependency_graph._module_name so dormant post-filtering can match the
    dispatch_graph dynamic-module set by dotted name."""
    rel = Path(rel_str)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def codebase_map(
    root_path: str | Path,
    extra_excludes: frozenset[str] = frozenset(),
    *,
    with_index: bool = False,
) -> SystemMap:
    """Generate a SystemMap for the codebase at root_path.

    The tree is listed and each file read once (corpus.Corpus); every analyzer
    works from those facts. Each analyzer runs under its own guard so a
    failure in one doesn't break the others — consumers want partial results
    when one analyzer chokes. With ``with_index`` the symbol, call and route
    index the query layer reads is attached as ``smap.index`` (not part of
    ``to_dict()``).
    """
    from . import dependency_graph, reachability, tool_graph, dead_symbol, dispatch_graph, subsystems
    from .corpus import Corpus

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
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    corpus = Corpus.build(root, extra_excludes)
    timings["read"] = time.perf_counter() - t0

    # 0. Dynamic-dispatch graph (B3): which modules are reached only by string-keyed
    #    dispatch (Celery task names, blueprint auto-reg, plugin.json, tool tables).
    #    Used to suppress false-positive dormant findings. Never raises.
    dynamic_modules: set[str] = set()
    t0 = time.perf_counter()
    try:
        dispatch_result = dispatch_graph.analyze(root, extra_excludes, corpus=corpus)
        dynamic_modules = set(dispatch_result["graph"].get("dynamic_modules", []))
        smap.celery_tasks = dispatch_result["graph"].get("celery_tasks", [])
        smap.stats["dispatch"] = dispatch_result["stats"]
    except Exception as e:
        smap.stats["dispatch"] = {"error": str(e)}
    timings["dispatch"] = time.perf_counter() - t0

    # 1. Dependency graph
    t0 = time.perf_counter()
    try:
        dep_result = dependency_graph.analyze(root, extra_excludes,
                                              dynamic_modules=dynamic_modules, corpus=corpus)
        smap.dependency_graph = dep_result["graph"]
        smap.lazy_imports = dep_result.get("lazy_graph", {})
        smap.node_meta = dep_result.get("node_meta", {})
        smap.findings.extend(dep_result["findings"])
        smap.file_count = dep_result["file_count"]
        smap.stats["dependency"] = dep_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.IMPORT_CYCLE,
            severity=Severity.INFO,
            summary=f"dependency_graph analyzer failed: {e}",
            evidence={"key": "analyzer-error"},
        ))
    timings["dependency"] = time.perf_counter() - t0

    # 2. Reachability (frontend ↔ backend)
    t0 = time.perf_counter()
    try:
        reach_result = reachability.analyze(root, extra_excludes, corpus=corpus)
        smap.reachability = reach_result["graph"]
        smap.findings.extend(reach_result["findings"])
        smap.stats["reachability"] = reach_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.GHOST_ENDPOINT,
            severity=Severity.INFO,
            summary=f"reachability analyzer failed: {e}",
            evidence={"key": "analyzer-error"},
        ))
    timings["reachability"] = time.perf_counter() - t0

    # 3. Tool graph (Guaardvark-specific; gracefully no-ops elsewhere)
    t0 = time.perf_counter()
    try:
        tool_result = tool_graph.analyze(root, extra_excludes, corpus=corpus)
        smap.tool_graph = tool_result["graph"]
        smap.findings.extend(tool_result["findings"])
        smap.stats["tool"] = tool_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.UNWIRED_TOOL,
            severity=Severity.INFO,
            summary=f"tool_graph analyzer failed: {e}",
            evidence={"key": "analyzer-error"},
        ))
    timings["tool"] = time.perf_counter() - t0

    # 4. Dead-symbol (function-level static dead-code; advisory only, NEVER
    #    auto-dispatched — see DISPATCHABLE_KINDS, deliberately excludes this kind).
    t0 = time.perf_counter()
    try:
        dead_result = dead_symbol.analyze(root, extra_excludes, corpus=corpus)
        smap.findings.extend(dead_result["findings"])
        smap.stats["dead_symbol"] = dead_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.DEAD_SYMBOL,
            severity=Severity.INFO,
            summary=f"dead_symbol analyzer failed: {e}",
            evidence={"key": "analyzer-error"},
        ))
    timings["dead_symbol"] = time.perf_counter() - t0

    # 5. Subsystems: the named areas of the product, from the mapped tree's
    #    own subsystems.json. Files no subsystem claims are reported.
    t0 = time.perf_counter()
    try:
        sub_result = subsystems.analyze(corpus, smap.node_meta)
        smap.subsystems = sub_result["graph"]
        smap.findings.extend(sub_result["findings"])
        smap.stats["subsystems"] = sub_result["stats"]
    except Exception as e:
        smap.stats["subsystems"] = {"error": str(e)}
    timings["subsystems"] = time.perf_counter() - t0

    if with_index:
        from . import index as index_mod
        t0 = time.perf_counter()
        smap.index = index_mod.build(corpus, smap)
        timings["index"] = time.perf_counter() - t0

    smap.stats["timings_s"] = {k: round(v, 3) for k, v in timings.items()}
    return smap
