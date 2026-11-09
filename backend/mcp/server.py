"""
MCP server assembly.

Builds a low-level ``mcp.server.lowlevel.Server`` instance, wires tool +
resource handlers, and hands control to whichever transport the entrypoint
picked (stdio in Phase 1).

This module doesn't touch Flask / Socket.IO / the DB. It's safe to import
inside a bare ``python -m backend.mcp`` subprocess without booting the
whole backend.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.lowlevel import Server

from backend.mcp import MCP_NAME, MCP_TITLE, get_version
from backend.mcp.config import (
    ALL_TOOLSET,
    TOOLSET_ENV,
    TOOLSETS,
    MCPConfig,
    load_config,
    toolset_members,
    toolset_names,
)
from backend.mcp.resources_adapter import build_resource_handlers
from backend.mcp.tools_adapter import build_tool_handlers

logger = logging.getLogger(__name__)


def _ensure_tools_initialized() -> None:
    """
    Tools are registered lazily by ``initialize_all_tools()`` at Flask startup.
    When we're running as a bare stdio subprocess nobody booted Flask, so the
    registry is empty. Trigger registration here — it's idempotent.
    """
    from backend.tools.tool_registry_init import initialize_all_tools
    initialize_all_tools()


class MCPServerDisabled(RuntimeError):
    """The configuration switches the server off; nothing is built or served."""


_SUPPORT = "Docs: https://guaardvark.com/mcp/ · Support: support@guaardvark.com"


def _instructions(cfg: MCPConfig, tool_count: int) -> str:
    """What the client is told about this server, naming a narrowed toolset."""
    name = cfg.tools.toolset
    if name == ALL_TOOLSET:
        return (
            f"{MCP_TITLE} — local-first AI platform. Exposes generation, RAG, "
            f"memory, and web tools plus generated outputs as MCP resources. {_SUPPORT}"
        )
    others = ", ".join(other for other in toolset_names() if other != name)
    return (
        f"{MCP_TITLE} — local-first AI platform. This server runs the '{name}' toolset: "
        f"{TOOLSETS[name].summary} ({tool_count} tools), plus generated outputs as MCP "
        f"resources. Other toolsets: {others}; choose one with --toolset or {TOOLSET_ENV}. "
        f"{_SUPPORT}"
    )


def build_server(config: MCPConfig | None = None) -> tuple[Server, dict[str, Any]]:
    """
    Construct the MCP server with all adapters wired up.

    Returns (server, stats) where stats is
    ``{"tools": N, "resources": M, "toolset": name}``.
    Callers print the stats banner before handing off to a transport.
    Raises ``MCPServerDisabled`` when the configuration turns the server off,
    and ``UnknownToolset`` when it names a toolset that does not exist.
    """
    cfg = config or load_config()
    if not cfg.enabled:
        raise MCPServerDisabled(
            "The Guaardvark MCP server is switched off by "
            f"{cfg.disabled_by or 'its configuration'}. Set it to true, or remove it, "
            "to let MCP clients connect."
        )
    toolset_members(cfg.tools)
    version = get_version()

    _ensure_tools_initialized()

    # mcp 2.x: handlers are constructor arguments, not decorators.
    on_list_tools, on_call_tool, tool_count = build_tool_handlers(cfg)
    on_list_resources, on_read_resource, resource_count = build_resource_handlers(cfg)

    server: Server = Server(
        name=MCP_NAME,
        version=version,
        instructions=_instructions(cfg, tool_count),
        website_url="https://guaardvark.com",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_read_resource=on_read_resource,
    )

    return server, {"tools": tool_count, "resources": resource_count, "toolset": cfg.tools.toolset}


async def run_stdio(
    config: MCPConfig | None = None,
    prebuilt: tuple[Server, dict[str, Any]] | None = None,
) -> None:
    """
    Run the server on stdin/stdout. Blocks until the peer disconnects.
    ``prebuilt`` lets callers warm up the server inside a stdout-quarantine
    so import-time noise never races the JSON-RPC pipe.
    """
    from mcp.server.stdio import stdio_server

    server, stats = prebuilt or build_server(config)
    logger.info(
        "MCP server ready: %s v%s — %d tools (toolset %s), %d resources",
        MCP_NAME, get_version(), stats["tools"], stats.get("toolset", ALL_TOOLSET), stats["resources"],
    )
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run_http(
    host: str = "127.0.0.1",
    port: int = 8788,
    config: MCPConfig | None = None,
    prebuilt: tuple[Server, dict[str, Any]] | None = None,
) -> None:
    """
    Serve MCP over streamable HTTP at ``http://{host}:{port}/mcp``.

    Phase 1 has no auth, so the default bind is loopback-only. The SDK
    auto-enables DNS-rebinding protection for localhost binds; anything
    wider is a deliberate operator choice, and http_app still refuses a
    request addressed to a name that is not this machine's.
    """
    import uvicorn

    server, stats = prebuilt or build_server(config)
    logger.info(
        "MCP server ready (http): %s v%s — %d tools (toolset %s), %d resources on http://%s:%d/mcp",
        MCP_NAME, get_version(), stats["tools"], stats.get("toolset", ALL_TOOLSET), stats["resources"],
        host, port,
    )
    uvicorn.run(http_app(server, host), host=host, port=port, log_level="info")


def http_app(server: Server, host: str):
    """The streamable HTTP app behind the backend's Host check
    (backend/utils/host_check.py). The SDK checks Host only when bound to a
    loopback address; the backend's rule applies on any bind."""
    from backend.utils.host_check import HostCheckASGIMiddleware

    return HostCheckASGIMiddleware(server.streamable_http_app(host=host))
