"""
Guaardvark MCP (Model Context Protocol) server.

Exposes Guaardvark's tool registry, generated outputs, and (eventually) RAG/
memory stores to external MCP clients — Claude Desktop, Claude Code, Cursor,
Zed, etc. — over stdio.

This is Phase 1: stdio-only, no auth (trusted local pipe), read-only
resources, default-deny safety gating on desktop/screen/system tools.

Entry point: ``python -m backend.mcp``
"""

from backend.mcp.config import MCPConfig, load_config

MCP_NAME = "guaardvark"
MCP_TITLE = "Guaardvark"
# Pulled lazily from backend.app so an import here doesn't drag the whole
# Flask world in when we're running as a bare stdio subprocess.
_VERSION: str | None = None


def get_version() -> str:
    """
    Resolve Guaardvark version without importing ``backend.app`` (which would
    boot CUDA, the DB client, logger config, and the full service world —
    fine for the Flask process, absurd for a bare stdio subprocess).
    Parses the literal out of ``backend/app.py`` as text.
    """
    global _VERSION
    if _VERSION is not None:
        return _VERSION
    import re
    from pathlib import Path
    app_py = Path(__file__).resolve().parent.parent / "app.py"
    try:
        match = re.search(r"""^__version__\s*=\s*['"]([^'"]+)['"]""", app_py.read_text(), re.M)
        _VERSION = match.group(1) if match else "0.0.0-unknown"
    except OSError:
        _VERSION = "0.0.0-unknown"
    return _VERSION


__all__ = ["MCPConfig", "load_config", "MCP_NAME", "MCP_TITLE", "get_version"]
