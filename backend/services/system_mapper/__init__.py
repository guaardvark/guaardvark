"""System Mapper — x-ray vision for any codebase.

Public entry point:

    from backend.services.system_mapper import codebase_map
    smap = codebase_map("/path/to/repo")
    smap.findings  # list[Finding]
    smap.dependency_graph
    smap.reachability
    smap.tool_graph

CLI:

    python -m backend.services.system_mapper /path/to/repo --out /tmp/out

Designed for three audiences:
  1. Humans reading the markdown report.
  2. Self-improvement service consuming `findings` as fix candidates.
  3. The LLM agent answering "what does this codebase do?" with the JSON.
"""
from .core import codebase_map, SystemMap, Finding, FindingKind, Severity

__all__ = ["codebase_map", "SystemMap", "Finding", "FindingKind", "Severity"]
