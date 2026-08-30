"""
Adapter: Guaardvark ``BaseTool`` → MCP ``Tool``.

Walks the in-process ``ToolRegistry``, filters through the configured policy
(``config.py``), emits MCP tool descriptors with proper JSON Schemas, and
dispatches ``tools/call`` back through the tool registry — routed through
the existing ``ToolExecutionGuard`` so MCP callers share the same circuit
breaker as the in-process ReACT loop.

Targets the mcp 2.x SDK: handlers are built here and passed to the
``Server`` constructor (``on_list_tools`` / ``on_call_tool``) rather than
registered via the removed v1 decorator API.
"""

from __future__ import annotations

import json
import logging
from itertools import count
from typing import Any

import mcp.types as mcp_types

from backend.mcp.audit import audit_call
from backend.mcp.config import MCPConfig, tool_is_exposed
from backend.services.agent_tools import BaseTool, get_tool_registry
from backend.services.tool_execution_guard import ToolExecutionGuard

logger = logging.getLogger(__name__)


# Map our ToolParameter.type strings → JSON Schema types.
_JSON_SCHEMA_TYPES = {
    "string": "string",
    "str": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "list": "array",
    "array": "array",
    "dict": "object",
    "object": "object",
}


def _tool_input_schema(tool: BaseTool) -> dict[str, Any]:
    """Build a JSON Schema object for a BaseTool's parameters."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in (tool.parameters or {}).items():
        json_type = _JSON_SCHEMA_TYPES.get(param.type.lower() if param.type else "string", "string")
        prop: dict[str, Any] = {"type": json_type}
        if param.description:
            prop["description"] = param.description
        if param.default is not None:
            prop["default"] = param.default
        properties[param_name] = prop
        if param.required:
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _category_lookup() -> dict[str, str]:
    """Pull the live category map from tool_registry_init. Side-effect-free."""
    try:
        from backend.tools.tool_registry_init import _tool_categories  # type: ignore[attr-defined]
        return dict(_tool_categories)
    except (ImportError, AttributeError):
        return {}


def collect_exposed_tools(config: MCPConfig) -> list[tuple[BaseTool, mcp_types.Tool]]:
    """
    Walk the registry, apply policy, return (BaseTool, McpTool) pairs for every
    tool that should be visible to external MCP clients.
    """
    registry = get_tool_registry()
    categories = _category_lookup()
    out: list[tuple[BaseTool, mcp_types.Tool]] = []

    for name in registry.list_tools():
        tool = registry.get_tool(name)
        if tool is None:
            continue
        category = categories.get(name)
        is_dangerous = bool(getattr(tool, "is_dangerous", False))
        requires_approval = bool(getattr(tool, "requires_approval", False))
        if category and category not in (config.tools.deny_categories or []) and not is_dangerous and not requires_approval:
            # Per security audit (LOW): explicit warning for tools that lack flags but live in
            # potentially side-effecting categories. Helps catch missing annotations.
            logger = logging.getLogger(__name__)
            logger.warning(f"Tool '{name}' (cat={category}) lacks is_dangerous/requires_approval but may perform side effects")
        allowed, reason = tool_is_exposed(
            tool_name=name,
            category=category,
            is_dangerous=is_dangerous,
            requires_approval=requires_approval,
            policy=config.tools,
        )
        if not allowed:
            logger.debug("MCP: hiding tool %s (%s)", name, reason)
            continue

        annotations = mcp_types.ToolAnnotations(
            title=tool.name,
            read_only_hint=(category in {"web", "knowledge", "memory"} and not tool.is_dangerous) or None,
            destructive_hint=bool(tool.is_dangerous) or None,
        )
        mcp_tool = mcp_types.Tool(
            name=tool.name,
            description=(tool.description or "").strip() or tool.name,
            input_schema=_tool_input_schema(tool),
            annotations=annotations,
        )
        out.append((tool, mcp_tool))

    logger.info("MCP: exposing %d of %d registered tools", len(out), len(registry.list_tools()))
    return out


def _content_blocks_from_result(result: Any) -> list[mcp_types.ContentBlock]:
    """Serialize a ToolResult.output into MCP content blocks. Keep it simple."""
    if result is None:
        return [mcp_types.TextContent(type="text", text="(no output)")]
    if isinstance(result, (dict, list)):
        return [mcp_types.TextContent(type="text", text=json.dumps(result, default=str, indent=2))]
    return [mcp_types.TextContent(type="text", text=str(result))]


def _error_result(text: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        is_error=True,
    )


def build_tool_handlers(config: MCPConfig) -> tuple[Any, Any, int]:
    """
    Build the ``on_list_tools`` / ``on_call_tool`` handlers for the mcp 2.x
    ``Server`` constructor. Returns (on_list_tools, on_call_tool, tool_count).
    """
    exposed = collect_exposed_tools(config)
    by_name = {mcp_tool.name: (base_tool, mcp_tool) for base_tool, mcp_tool in exposed}
    # Per-session guard so repeated failures trip the breaker across a run.
    guard = ToolExecutionGuard(max_failures_per_tool=2, max_duplicate_calls=1)
    # An MCP call is one-shot: there is no ReACT loop to number it, so the guard's
    # iteration field carries call order within the session instead.
    call_seq = count(1)

    async def on_list_tools(
        _ctx: Any,
        _params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(
            tools=[mcp_tool for _base, mcp_tool in by_name.values()],
        )

    async def on_call_tool(
        _ctx: Any,
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        with audit_call(method="tools/call", target=name) as rec:
            rec["bytes_in"] = len(json.dumps(arguments, default=str))

            pair = by_name.get(name)
            if pair is None:
                rec["outcome"] = "error"
                rec["error_code"] = "tool_not_exposed"
                return _error_result(f"Tool '{name}' is not exposed by this MCP server.")

            base_tool, _ = pair

            ok, guard_reason = guard.check_call(name, arguments)
            if not ok:
                rec["outcome"] = "error"
                rec["error_code"] = "guard_blocked"
                suggestion = guard.suggest_fallback(name) or ""
                msg = f"Blocked by execution guard: {guard_reason}"
                if suggestion:
                    msg += f"\nSuggestion: {suggestion}"
                return _error_result(msg)

            try:
                tool_result = base_tool.execute(**arguments)
            except Exception as exc:
                guard.record_result(
                    name, arguments, success=False, error=str(exc),
                    iteration=next(call_seq),
                )
                rec["outcome"] = "error"
                rec["error_code"] = exc.__class__.__name__
                return _error_result(f"Tool raised {exc.__class__.__name__}: {exc}")

            success = bool(getattr(tool_result, "success", True))
            guard.record_result(
                name,
                arguments,
                success=success,
                error=getattr(tool_result, "error", None),
                iteration=next(call_seq),
            )
            if not success:
                rec["outcome"] = "error"
                rec["error_code"] = "tool_failed"

            blocks = _content_blocks_from_result(getattr(tool_result, "output", tool_result))
            rec["bytes_out"] = sum(len(getattr(b, "text", "")) for b in blocks)
            return mcp_types.CallToolResult(content=blocks, is_error=not success)

    return on_list_tools, on_call_tool, len(by_name)
