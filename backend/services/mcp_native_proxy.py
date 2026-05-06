"""Wrap MCP server tools as native Guaardvark BaseTool instances.

When a server like the filesystem MCP advertises tools (`list_directory`,
`read_text_file`, `write_file`, …), this module generates a `BaseTool`
subclass per tool that delegates to `MCPClientService.call_tool` and
registers them in the global tool registry. The LLM can then pick
`filesystem_list_directory` directly — no `mcp_execute` shim, no guessing
parameter names from a generic schema.

Lifecycle:
  - On `connect_server` success → `register_native_proxies(server, tools)`
  - On `disconnect_server` → `unregister_native_proxies(server)`

Naming: each proxy is named `<server>_<mcp_name>` to avoid colliding with
Guaardvark-native tools and to keep server affinity explicit when multiple
MCP servers are connected.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from backend.services.agent_tools import (
    BaseTool,
    ToolParameter,
    ToolResult,
    get_tool_registry,
)

logger = logging.getLogger(__name__)


# Map JSON-schema types → Guaardvark ToolParameter types.
_JSON_TO_GVK_TYPE = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "list",
    "object": "dict",
}


def _make_proxy_class(server: str, mcp_tool: Dict[str, Any]) -> type:
    """Generate a BaseTool subclass that proxies to one MCP tool."""
    mcp_name = mcp_tool.get("name") or "unknown"
    # Renamed off "description" to dodge Python's class-body scoping rule:
    # inside `class X: description = description[:300]`, the RHS lookup binds
    # to the (yet-undefined) class-level `description`, not the enclosing scope.
    mcp_description = (mcp_tool.get("description") or f"MCP tool '{mcp_name}' on '{server}'").strip()

    # Map MCP's JSON inputSchema → Guaardvark's parameters dict.
    schema = mcp_tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    parameters: Dict[str, ToolParameter] = {}
    for pname, pdef in properties.items():
        if not isinstance(pdef, dict):
            continue
        ptype = _JSON_TO_GVK_TYPE.get(pdef.get("type", "string"), "string")
        parameters[pname] = ToolParameter(
            name=pname,
            type=ptype,
            required=pname in required,
            description=(pdef.get("description") or "")[:200],
        )

    proxy_name = f"{server}_{mcp_name}"

    # Capture closure vars so the generated execute() doesn't read class state.
    _server = server
    _mcp_name = mcp_name

    class _MCPProxy(BaseTool):
        name = proxy_name
        # Truncate noisy MCP descriptions; LLM doesn't need 500-char essays.
        description = mcp_description[:300]

        def execute(self, **kwargs: Any) -> ToolResult:
            from backend.services.mcp_client_service import (
                MCP_ENABLED,
                get_mcp_service,
                run_mcp_async,
            )
            if not MCP_ENABLED:
                return ToolResult(success=False, error="MCP is disabled")
            try:
                service = get_mcp_service()
                result = run_mcp_async(service.call_tool(_server, _mcp_name, kwargs))
            except Exception as exc:
                return ToolResult(
                    success=False,
                    error=f"{proxy_name} delegate failed: {exc}",
                )
            if not result.get("success"):
                return ToolResult(success=False, error=result.get("error") or "MCP call failed")

            # MCP tool results are typically {"content": [{"type": "text", "text": "..."}, ...]}
            tool_result = result.get("result", {})
            if isinstance(tool_result, dict):
                content = tool_result.get("content", [])
                if isinstance(content, list) and content:
                    parts: List[str] = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                parts.append(item.get("text", ""))
                            elif item.get("type") == "image":
                                parts.append(f"[Image: {item.get('mimeType', 'image')}]")
                            else:
                                parts.append(str(item))
                        else:
                            parts.append(str(item))
                    output = "\n".join(p for p in parts if p)
                else:
                    output = str(tool_result)
            else:
                output = str(tool_result)

            return ToolResult(success=True, output=output, metadata=result)

    _MCPProxy.parameters = parameters
    _MCPProxy.__name__ = f"MCPProxy_{server}_{mcp_name}"
    _MCPProxy.__qualname__ = _MCPProxy.__name__
    return _MCPProxy


# server name → list of proxy tool names we registered for that server.
# Tracked so disconnect can clean up exactly what connect added (and not
# accidentally remove a name an unrelated piece of code happened to register).
_REGISTERED: Dict[str, List[str]] = {}


def register_native_proxies(server: str, tools: List[Dict[str, Any]]) -> List[str]:
    """Register a BaseTool proxy for each MCP tool. Idempotent per server.

    Returns the list of proxy names that ended up in the registry.
    """
    if server in _REGISTERED:
        unregister_native_proxies(server)

    registry = get_tool_registry()
    names: List[str] = []
    for mcp_tool in tools or []:
        try:
            cls = _make_proxy_class(server, mcp_tool)
            registry.register(cls())
            names.append(cls.name)
        except Exception as exc:
            logger.warning(
                f"Skipping MCP proxy for {server}/{mcp_tool.get('name', '?')}: {exc}"
            )

    _REGISTERED[server] = names
    _sync_chat_engine_list()
    if names:
        logger.info(f"Registered {len(names)} MCP-native proxies for '{server}'")
    return names


def unregister_native_proxies(server: str) -> int:
    """Remove all proxies a previous connect added for this server."""
    names = _REGISTERED.pop(server, [])
    if not names:
        return 0
    registry = get_tool_registry()
    for name in names:
        try:
            registry.unregister(name)
        except Exception:
            pass
    _sync_chat_engine_list()
    logger.info(f"Unregistered {len(names)} MCP-native proxies for '{server}'")
    return len(names)


def list_native_proxies() -> List[str]:
    """Flat list of every registered MCP-native proxy across all servers."""
    out: List[str] = []
    for names in _REGISTERED.values():
        out.extend(names)
    return out


def _sync_chat_engine_list() -> None:
    """Push the current set of native proxies into unified_chat_engine.MCP_NATIVE_TOOLS.

    The chat engine reads this list when the keyword router fires. We mutate
    the existing list in place — the TOOL_CONTEXT_KEYWORDS entry holds a
    reference to it, so updates are visible to the selector immediately.
    """
    try:
        from backend.services import unified_chat_engine
        target = unified_chat_engine.MCP_NATIVE_TOOLS
        target.clear()
        target.extend(list_native_proxies())
    except Exception as exc:
        # Chat engine not loaded yet (e.g. during startup ordering) — fine.
        logger.debug(f"Could not sync MCP_NATIVE_TOOLS: {exc}")
