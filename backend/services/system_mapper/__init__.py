"""System Mapper — x-ray vision for any codebase.

Public entry point:

    from backend.services.system_mapper import codebase_map
    smap = codebase_map("/path/to/repo")
    smap.findings  # list[Finding]
    smap.dependency_graph, smap.lazy_imports, smap.node_meta
    smap.reachability, smap.tool_graph, smap.celery_tasks, smap.subsystems

Stored maps, freshness and background builds: store.py. Questions about the
code ("where is X", "what calls F"): query.py, behind the system_map_query tool.

CLI:

    python -m backend.services.system_mapper /path/to/repo --out /tmp/out

Designed for three audiences:
  1. Humans reading the markdown report.
  2. Self-improvement service consuming `findings` as fix candidates.
  3. The chat model, through map_codebase and system_map_query.
"""
from .core import codebase_map, SystemMap, Finding, FindingKind, Severity

__all__ = ["codebase_map", "SystemMap", "Finding", "FindingKind", "Severity"]
