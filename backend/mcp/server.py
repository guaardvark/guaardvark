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
from backend.mcp.config import MCPConfig, load_config
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


def build_server(config: MCPConfig | None = None) -> tuple[Server, dict[str, int]]:
    """
    Construct the MCP server with all adapters wired up.

    Returns (server, stats) where stats is ``{"tools": N, "resources": M}``.
    Callers print the stats banner before handing off to a transport.
    """
    cfg = config or load_config()
    version = get_version()

    _ensure_tools_initialized()

    # mcp 2.x: handlers are constructor arguments, not decorators.
    on_list_tools, on_call_tool, tool_count = build_tool_handlers(cfg)
    on_list_resources, on_read_resource, resource_count = build_resource_handlers(cfg)

    server: Server = Server(
        name=MCP_NAME,
        version=version,
        instructions=(
            f"{MCP_TITLE} — local-first AI platform. Exposes generation, RAG, "
            "memory, and web tools plus generated outputs as MCP resources. "
            "Docs: https://guaardvark.com/mcp/ · Support: support@guaardvark.com"
        ),
        website_url="https://guaardvark.com",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_read_resource=on_read_resource,
    )

    return server, {"tools": tool_count, "resources": resource_count}


async def run_stdio(
    config: MCPConfig | None = None,
    prebuilt: tuple[Server, dict[str, int]] | None = None,
) -> None:
    """
    Run the server on stdin/stdout. Blocks until the peer disconnects.
    ``prebuilt`` lets callers warm up the server inside a stdout-quarantine
    so import-time noise never races the JSON-RPC pipe.
    """
    from mcp.server.stdio import stdio_server

    server, stats = prebuilt or build_server(config)
    logger.info(
        "MCP server ready: %s v%s — %d tools, %d resources",
        MCP_NAME, get_version(), stats["tools"], stats["resources"],
    )
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run_http(
    host: str = "127.0.0.1",
    port: int = 8788,
    config: MCPConfig | None = None,
    prebuilt: tuple[Server, dict[str, int]] | None = None,
) -> None:
    """
    Serve MCP over streamable HTTP at ``http://{host}:{port}/mcp``.

    Phase 1 has no auth, so the default bind is loopback-only. The SDK
    auto-enables DNS-rebinding protection for localhost binds; anything
    wider is a deliberate operator choice.
    """
    import uvicorn

    server, stats = prebuilt or build_server(config)
    logger.info(
        "MCP server ready (http): %s v%s — %d tools, %d resources on http://%s:%d/mcp",
        MCP_NAME, get_version(), stats["tools"], stats["resources"], host, port,
    )
    app = server.streamable_http_app(host=host)
    uvicorn.run(app, host=host, port=port, log_level="info")
