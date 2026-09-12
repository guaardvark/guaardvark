"""
Adapter: Guaardvark ``BaseTool`` → MCP ``Tool``.

Walks the in-process ``ToolRegistry``, filters through the configured policy
(``config.py``), emits MCP tool descriptors with proper JSON Schemas, and
dispatches ``tools/call`` to the tool on a worker thread behind a
``ToolExecutionGuard`` kept per client session.

Targets the mcp 2.x SDK: handlers are built here and passed to the
``Server`` constructor (``on_list_tools`` / ``on_call_tool``) rather than
registered via the removed v1 decorator API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from functools import partial
from itertools import count
from typing import Any

import mcp.types as mcp_types
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from backend.mcp.audit import audit_call
from backend.mcp.config import WAIT_TIMEOUT_SECONDS, MCPConfig, tool_is_exposed
from backend.services.agent_tools import BaseTool, get_tool_registry
from backend.services.tool_execution_guard import ToolExecutionGuard

logger = logging.getLogger(__name__)

# An argument a client may add to any call that changes state. A retry carrying
# the same key waits for, or returns, the first run instead of starting a
# second one. The adapter consumes it; tools never see it.
IDEMPOTENCY_KEY = "idempotency_key"
# Keyed calls remembered per session, oldest dropped first.
MAX_KEYED_CALLS = 256
# A tool that keeps failing is paused this long, then tried once more.
BREAKER_COOLDOWN_S = 60.0
GUARD_HISTORY = 200


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


def _json_type(type_name: str | None) -> str:
    return _JSON_SCHEMA_TYPES.get((type_name or "string").lower(), "string")


def _tool_input_schema(
    tool: BaseTool,
    defaults: dict[str, Any] | None = None,
    accepts_idempotency_key: bool = False,
) -> dict[str, Any]:
    """Build a JSON Schema object for a BaseTool's parameters.

    ``defaults`` are this server's argument overrides, so the published default
    is the value a call gets when the client omits the key.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    defaults = defaults or {}

    for param_name, param in (tool.parameters or {}).items():
        json_type = _json_type(param.type)
        prop: dict[str, Any] = {"type": json_type}
        if param.description:
            prop["description"] = param.description
        default = defaults.get(param_name, param.default)
        if default is not None:
            prop["default"] = default
        if getattr(param, "enum", None):
            prop["enum"] = list(param.enum)
        if getattr(param, "minimum", None) is not None:
            prop["minimum"] = param.minimum
        if getattr(param, "maximum", None) is not None:
            prop["maximum"] = param.maximum
        if json_type == "array" and getattr(param, "items", None):
            prop["items"] = {"type": _json_type(param.items)}
        properties[param_name] = prop
        if param.required:
            required.append(param_name)

    if accepts_idempotency_key and IDEMPOTENCY_KEY not in properties:
        properties[IDEMPOTENCY_KEY] = {
            "type": "string",
            "description": (
                "Optional. Send the same key when retrying this call: the retry waits for or "
                "returns the first run instead of starting a second one."
            ),
        }

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _annotations(tool: BaseTool) -> mcp_types.ToolAnnotations:
    """Hints from what the tool declares; nothing is inferred from its category."""
    read_only = getattr(tool, "read_only", None)
    destructive = getattr(tool, "destructive", None)
    if destructive is None and getattr(tool, "is_dangerous", False):
        destructive = True
    return mcp_types.ToolAnnotations(
        title=tool.name,
        read_only_hint=read_only,
        destructive_hint=None if read_only else destructive,
        idempotent_hint=True if getattr(tool, "idempotent", False) else None,
    )


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

        read_only = getattr(tool, "read_only", None)
        if read_only is None:
            # Clients decide what to confirm from these hints, so an exposed
            # tool should declare them next to its definition.
            logger.warning("MCP: tool '%s' declares no read_only; clients get no hint", name)

        description = (tool.description or "").strip() or tool.name
        defaults = (config.tools.argument_defaults or {}).get(tool.name)
        if defaults:
            rendered = ", ".join(f"{k}={json.dumps(v)}" for k, v in defaults.items())
            description += f" Over this MCP server, omitted arguments default to {rendered}."
        mcp_tool = mcp_types.Tool(
            name=tool.name,
            description=description,
            input_schema=_tool_input_schema(tool, defaults, accepts_idempotency_key=read_only is not True),
            annotations=_annotations(tool),
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


def _call_timeout(config: MCPConfig, arguments: dict[str, Any]) -> float:
    """The per-call ceiling: the configured timeout, or the wait ceiling when
    the caller asked a generation tool to block until the render finishes."""
    wait = arguments.get("wait_for_result")
    if str(wait).lower() in ("1", "true", "yes"):
        return float(max(config.timeout_seconds, WAIT_TIMEOUT_SECONDS))
    return float(config.timeout_seconds)


def _timeout_message(name: str, timeout: float, read_only: bool = False, key: str | None = None) -> str:
    head = (
        f"Tool '{name}' did not finish within {timeout:.0f} s. It is still running on the "
        "Guaardvark server and was not cancelled."
    )
    if read_only:
        return head + (
            " It changes nothing, so calling it again is safe; for longer work raise "
            "GUAARDVARK_MCP_TIMEOUT or data/config/mcp.json server.timeout_seconds."
        )
    if key:
        return head + (
            f" Call again with the same {IDEMPOTENCY_KEY} ('{key}') to wait for this run; "
            "that will not start a second one."
        )
    return head + (
        " Calling it again would start a second run. A render lands in Studio and "
        "data/outputs when it completes; for a queued generation, poll "
        "get_generation_status with its batch id. Send an "
        f"{IDEMPOTENCY_KEY} next time so a retry waits for the first run."
    )


def _error_result(text: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        is_error=True,
    )


def _argument_error(validator: Draft202012Validator | None, arguments: dict[str, Any]) -> str | None:
    """The most relevant schema violation in ``arguments``, or None.
    A null value counts as an omitted optional argument."""
    if validator is None:
        return None
    present = {k: v for k, v in arguments.items() if v is not None}
    error = best_match(validator.iter_errors(present))
    if error is None:
        return None
    where = ".".join(str(part) for part in error.absolute_path)
    return f"{where}: {error.message}" if where else error.message


@dataclass
class _KeyedCall:
    tool: str
    args_hash: str
    task: asyncio.Future


class _SessionState:
    """The guard and keyed calls of one client session."""

    def __init__(self) -> None:
        self.guard = ToolExecutionGuard(
            max_failures_per_tool=2,
            max_duplicate_calls=1,
            dedupe_completed=False,
            breaker_cooldown_s=BREAKER_COOLDOWN_S,
            history_limit=GUARD_HISTORY,
        )
        self.keyed: OrderedDict[str, _KeyedCall] = OrderedDict()

    def remember(self, key: str, call: _KeyedCall) -> None:
        self.keyed[key] = call
        self.keyed.move_to_end(key)
        while len(self.keyed) > MAX_KEYED_CALLS:
            self.keyed.popitem(last=False)


def _record_outcome(guard: ToolExecutionGuard, name: str, arguments: dict[str, Any],
                    sequence: Any, task: asyncio.Future) -> None:
    """Tell the guard how a run ended, whether or not a caller still waits for it."""
    if task.cancelled():
        success, error = False, "cancelled"
    elif task.exception() is not None:
        success, error = False, str(task.exception())
    else:
        result = task.result()
        success = bool(getattr(result, "success", True))
        error = getattr(result, "error", None)
    guard.record_result(name, arguments, success=success, error=error, iteration=next(sequence))


async def _await_result(task: asyncio.Future, name: str, timeout: float, read_only: bool,
                        key: str | None, rec: dict[str, Any]) -> mcp_types.CallToolResult:
    """Wait for a run and turn its ToolResult into an MCP result. The run itself
    is shielded: a timeout or a cancelled request leaves it going."""
    if task.cancelled():
        rec["outcome"] = "error"
        rec["error_code"] = "cancelled"
        return _error_result(f"Tool '{name}' was cancelled before it finished.")
    try:
        # A BaseTool is synchronous. Running it on a worker thread keeps the
        # server answering other calls while a render runs, and lets this
        # timeout fire instead of the client's.
        tool_result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        rec["outcome"] = "error"
        rec["error_code"] = "timeout"
        return _error_result(_timeout_message(name, timeout, read_only, key))
    except asyncio.CancelledError:
        if not task.cancelled():
            raise
        rec["outcome"] = "error"
        rec["error_code"] = "cancelled"
        return _error_result(f"Tool '{name}' was cancelled before it finished.")
    except Exception as exc:
        rec["outcome"] = "error"
        rec["error_code"] = exc.__class__.__name__
        return _error_result(f"Tool raised {exc.__class__.__name__}: {exc}")

    success = bool(getattr(tool_result, "success", True))
    if not success:
        rec["outcome"] = "error"
        rec["error_code"] = "tool_failed"

    payload = getattr(tool_result, "output", tool_result)
    if not success and payload in (None, ""):
        # A failed ToolResult carries its reason in ``error``; without
        # this the client saw "(no output)" and nothing to act on.
        payload = getattr(tool_result, "error", None) or "Tool failed without a message."
    blocks = _content_blocks_from_result(payload)
    rec["bytes_out"] = sum(len(getattr(b, "text", "")) for b in blocks)
    return mcp_types.CallToolResult(content=blocks, is_error=not success)


def build_tool_handlers(config: MCPConfig) -> tuple[Any, Any, int]:
    """
    Build the ``on_list_tools`` / ``on_call_tool`` handlers for the mcp 2.x
    ``Server`` constructor. Returns (on_list_tools, on_call_tool, tool_count).
    """
    exposed = collect_exposed_tools(config)
    by_name = {mcp_tool.name: (base_tool, mcp_tool) for base_tool, mcp_tool in exposed}
    validators: dict[str, Draft202012Validator | None] = {}
    for name, (_base, mcp_tool) in by_name.items():
        try:
            validators[name] = Draft202012Validator(mcp_tool.input_schema)
        except Exception as exc:  # noqa: BLE001 - a bad schema must not take the server down
            logger.warning("MCP: no argument validation for %s: %s", name, exc)
            validators[name] = None

    # Guard state belongs to a client session: one stdio client, or one HTTP
    # session. Entries go away with their session object.
    sessions: weakref.WeakKeyDictionary[Any, _SessionState] = weakref.WeakKeyDictionary()
    sessionless = _SessionState()
    # An MCP call is one-shot: there is no ReACT loop to number it, so the guard's
    # iteration field carries call order instead.
    call_seq = count(1)

    def _state_for(ctx: Any) -> _SessionState:
        session = getattr(ctx, "session", None)
        if session is None:
            return sessionless
        try:
            state = sessions.get(session)
            if state is None:
                state = sessions[session] = _SessionState()
            return state
        except TypeError:
            return sessionless

    async def on_list_tools(
        _ctx: Any,
        _params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(
            tools=[mcp_tool for _base, mcp_tool in by_name.values()],
        )

    async def on_call_tool(
        ctx: Any,
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        name = params.name
        arguments = dict(params.arguments or {})
        with audit_call(method="tools/call", target=name) as rec:
            rec["bytes_in"] = len(json.dumps(arguments, default=str))

            pair = by_name.get(name)
            if pair is None:
                rec["outcome"] = "error"
                rec["error_code"] = "tool_not_exposed"
                return _error_result(f"Tool '{name}' is not exposed by this MCP server.")

            base_tool, _ = pair
            read_only = getattr(base_tool, "read_only", None) is True
            key = arguments.pop(IDEMPOTENCY_KEY, None)
            key = str(key) if key not in (None, "") else None

            # Defaults first, so validation and the guard see the call that will run.
            for default_key, value in (config.tools.argument_defaults or {}).get(name, {}).items():
                arguments.setdefault(default_key, value)
            problem = _argument_error(validators.get(name), arguments)
            if problem:
                rec["outcome"] = "error"
                rec["error_code"] = "invalid_arguments"
                return _error_result(f"Invalid arguments for '{name}': {problem}")

            state = _state_for(ctx)
            timeout = _call_timeout(config, arguments)
            args_hash = ToolExecutionGuard._hash_call(name, arguments)

            if key is not None:
                earlier = state.keyed.get(key)
                if earlier is not None:
                    if (earlier.tool, earlier.args_hash) != (name, args_hash):
                        rec["outcome"] = "error"
                        rec["error_code"] = "idempotency_key_reused"
                        return _error_result(
                            f"{IDEMPOTENCY_KEY} '{key}' was already used for a different call."
                        )
                    return await _await_result(earlier.task, name, timeout, read_only, key, rec)

            # A status poll repeats on purpose, so idempotent tools skip duplicate
            # detection; the circuit breaker still applies to them.
            ok, guard_reason = state.guard.check_call(
                name, arguments, dedupe=not getattr(base_tool, "idempotent", False),
            )
            if not ok:
                rec["outcome"] = "error"
                rec["error_code"] = "guard_blocked"
                suggestion = state.guard.suggest_fallback(name) or ""
                msg = f"Blocked by execution guard: {guard_reason}"
                if suggestion:
                    msg += f"\nSuggestion: {suggestion}"
                return _error_result(msg)

            # Tools run inside this MCP server process, not inside the backend.
            # Tell them so: a tool that needs the backend calls it over HTTP.
            base_tool.set_context({"transport": "mcp"})
            task = asyncio.ensure_future(asyncio.to_thread(base_tool.execute, **arguments))
            task.add_done_callback(partial(_record_outcome, state.guard, name, dict(arguments), call_seq))
            if key is not None:
                state.remember(key, _KeyedCall(name, args_hash, task))
            return await _await_result(task, name, timeout, read_only, key, rec)

    return on_list_tools, on_call_tool, len(by_name)
