"""The MCP tools adapter: declared hints, published schemas, validation,
per-session guards and idempotent retries. Fake tools, real handlers."""

import asyncio
import time

import mcp.types as mcp_types
import pytest

from backend.mcp import tools_adapter
from backend.mcp.config import MCPConfig
from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult


class _Session:
    """Stands in for the SDK's ServerSession: hashable and weak-referenceable."""


class _Ctx:
    def __init__(self):
        self.session = _Session()


def _handlers(monkeypatch, *tools, config=None):
    cfg = config or MCPConfig()
    pairs = []
    for tool in tools:
        defaults = (cfg.tools.argument_defaults or {}).get(tool.name)
        pairs.append((tool, mcp_types.Tool(
            name=tool.name,
            description=tool.description,
            input_schema=tools_adapter._tool_input_schema(
                tool, defaults, accepts_idempotency_key=tool.read_only is not True),
            annotations=tools_adapter._annotations(tool),
        )))
    monkeypatch.setattr(tools_adapter, "collect_exposed_tools", lambda _cfg: pairs)
    _on_list, on_call, _count = tools_adapter.build_tool_handlers(cfg)
    return on_call


def _params(name, arguments):
    return mcp_types.CallToolRequestParams(name=name, arguments=arguments)


# ── hints and schemas ─────────────────────────────────────────────────────────

def test_annotations_come_from_what_the_tool_declares():
    class Writer(BaseTool):
        name, description, read_only, destructive = "w", "writes", False, False

    class Reader(BaseTool):
        name, description, read_only, idempotent = "r", "reads", True, True

    class Undeclared(BaseTool):
        name, description, is_dangerous = "u", "unknown", True

    w, r, u = tools_adapter._annotations(Writer()), tools_adapter._annotations(Reader()), tools_adapter._annotations(Undeclared())
    assert (w.read_only_hint, w.destructive_hint) == (False, False)
    assert (r.read_only_hint, r.destructive_hint, r.idempotent_hint) == (True, None, True)
    assert (u.read_only_hint, u.destructive_hint) == (None, True)


def test_the_published_schema_carries_server_defaults_and_constraints():
    class Gen(BaseTool):
        name, description, read_only = "generate_image", "gen", False
        parameters = {
            "wait_for_result": ToolParameter(name="wait_for_result", type="bool", required=False, default=True),
            "status": ToolParameter(name="status", type="string", required=False, enum=["a", "b"]),
            "limit": ToolParameter(name="limit", type="int", required=False, minimum=1, maximum=50),
            "tags": ToolParameter(name="tags", type="list", required=False, items="string"),
        }

    schema = tools_adapter._tool_input_schema(Gen(), {"wait_for_result": False}, accepts_idempotency_key=True)
    props = schema["properties"]
    assert props["wait_for_result"]["default"] is False
    assert props["status"]["enum"] == ["a", "b"]
    assert (props["limit"]["minimum"], props["limit"]["maximum"]) == (1, 50)
    assert props["tags"]["items"] == {"type": "string"}
    assert tools_adapter.IDEMPOTENCY_KEY in props


def test_read_only_tools_do_not_offer_an_idempotency_key():
    class Reader(BaseTool):
        name, description, read_only = "r", "reads", True

    assert tools_adapter.IDEMPOTENCY_KEY not in tools_adapter._tool_input_schema(Reader())["properties"]


def test_every_exposed_tool_declares_read_only():
    from backend.mcp.server import _ensure_tools_initialized
    _ensure_tools_initialized()
    undeclared = [base.name for base, _tool in tools_adapter.collect_exposed_tools(MCPConfig())
                  if getattr(base, "read_only", None) is None]
    assert undeclared == []


# ── validation and exposure ───────────────────────────────────────────────────

class _Recording(BaseTool):
    name, description, read_only = "recording", "records calls", False
    parameters = {
        "status": ToolParameter(name="status", type="string", required=True, enum=["drafted", "posted"]),
        "limit": ToolParameter(name="limit", type="int", required=False, minimum=1, maximum=50),
    }

    def __init__(self):
        super().__init__()
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(success=True, output="ok")


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments,fragment", [
    ({"status": "nonsense"}, "is not one of"),
    ({"status": "drafted", "limit": 500}, "limit"),
    ({}, "status"),
])
async def test_invalid_arguments_are_rejected_before_the_tool_runs(monkeypatch, arguments, fragment):
    tool = _Recording()
    on_call = _handlers(monkeypatch, tool)
    result = await on_call(_Ctx(), _params("recording", arguments))
    assert result.is_error and fragment in result.content[0].text
    assert tool.calls == []


@pytest.mark.asyncio
async def test_a_null_optional_argument_counts_as_omitted(monkeypatch):
    tool = _Recording()
    on_call = _handlers(monkeypatch, tool)
    result = await on_call(_Ctx(), _params("recording", {"status": "drafted", "limit": None}))
    assert not result.is_error


@pytest.mark.asyncio
async def test_a_hidden_tool_cannot_be_called_by_name(monkeypatch):
    class Hidden(BaseTool):
        name, description, is_dangerous = "delete_everything", "no", True

        def execute(self, **kwargs):
            raise AssertionError("must not run")

    class Registry:
        def list_tools(self):
            return ["delete_everything"]

        def get_tool(self, _name):
            return Hidden()

    monkeypatch.setattr(tools_adapter, "get_tool_registry", lambda: Registry())
    monkeypatch.setattr(tools_adapter, "_category_lookup", lambda: {"delete_everything": "memory"})
    _on_list, on_call, count = tools_adapter.build_tool_handlers(MCPConfig())
    assert count == 0
    result = await on_call(_Ctx(), _params("delete_everything", {}))
    assert result.is_error and "not exposed" in result.content[0].text


# ── sessions, duplicates, retries ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_tripped_breaker_in_one_session_does_not_block_another(monkeypatch):
    class Flaky(BaseTool):
        name, description, read_only = "flaky", "always fails", True
        parameters = {"i": ToolParameter(name="i", type="int", required=False)}

        def execute(self, **kwargs):
            return ToolResult(success=False, error="down")

    on_call = _handlers(monkeypatch, Flaky())
    first, second = _Ctx(), _Ctx()
    for i in range(2):
        await on_call(first, _params("flaky", {"i": i}))
    blocked = await on_call(first, _params("flaky", {"i": 9}))
    assert "paused" in blocked.content[0].text
    other = await on_call(second, _params("flaky", {"i": 9}))
    assert other.content[0].text == "down"


class _SlowWrite(BaseTool):
    name, description, read_only = "slow_write", "a slow write", False
    parameters = {"a": ToolParameter(name="a", type="int", required=False)}

    def __init__(self, seconds=0.4):
        super().__init__()
        self.seconds = seconds
        self.runs = []

    def execute(self, **kwargs):
        self.runs.append(kwargs)
        time.sleep(self.seconds)
        return ToolResult(success=True, output="done")


@pytest.mark.asyncio
async def test_a_retry_with_the_same_key_waits_for_the_first_run(monkeypatch):
    tool = _SlowWrite()
    cfg = MCPConfig()
    cfg.timeout_seconds = 0.1
    on_call = _handlers(monkeypatch, tool, config=cfg)
    ctx = _Ctx()

    first = await on_call(ctx, _params("slow_write", {"idempotency_key": "k1"}))
    assert first.is_error and "same idempotency_key" in first.content[0].text

    cfg.timeout_seconds = 5
    second = await on_call(ctx, _params("slow_write", {"idempotency_key": "k1"}))
    assert not second.is_error and second.content[0].text == "done"
    assert len(tool.runs) == 1
    assert "idempotency_key" not in tool.runs[0]


@pytest.mark.asyncio
async def test_a_key_reused_for_a_different_call_is_refused(monkeypatch):
    tool = _SlowWrite(seconds=0)
    on_call = _handlers(monkeypatch, tool)
    ctx = _Ctx()
    await on_call(ctx, _params("slow_write", {"a": 1, "idempotency_key": "k"}))
    result = await on_call(ctx, _params("slow_write", {"a": 2, "idempotency_key": "k"}))
    assert result.is_error and "different call" in result.content[0].text
    assert len(tool.runs) == 1


@pytest.mark.asyncio
async def test_an_identical_call_sent_while_the_first_runs_is_blocked(monkeypatch):
    tool = _SlowWrite()
    on_call = _handlers(monkeypatch, tool)
    ctx = _Ctx()
    first = asyncio.create_task(on_call(ctx, _params("slow_write", {"a": 1})))
    await asyncio.sleep(0.1)
    second = await on_call(ctx, _params("slow_write", {"a": 1}))
    assert second.is_error and "still running" in second.content[0].text
    assert not (await first).is_error
    assert len(tool.runs) == 1


@pytest.mark.asyncio
async def test_a_finished_call_may_be_sent_again(monkeypatch):
    tool = _SlowWrite(seconds=0)
    on_call = _handlers(monkeypatch, tool)
    ctx = _Ctx()
    for _ in range(3):
        assert not (await on_call(ctx, _params("slow_write", {"a": 1}))).is_error
    assert len(tool.runs) == 3


def test_timeout_messages_do_not_advise_resubmitting_a_write():
    write = tools_adapter._timeout_message("generate_video", 120)
    assert "second run" in write and "get_generation_status" in write
    keyed = tools_adapter._timeout_message("generate_video", 120, key="k9")
    assert "'k9'" in keyed and "will not start a second one" in keyed
    read = tools_adapter._timeout_message("search_code", 120, read_only=True)
    assert "safe" in read
