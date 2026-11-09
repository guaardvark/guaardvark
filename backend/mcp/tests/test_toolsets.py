"""Named toolsets: the declared data, how one is chosen, and what a narrowed server serves and refuses."""

import json

import mcp.types as mcp_types
import pytest

from backend.mcp import config as mcp_config
from backend.mcp import tools_adapter
from backend.mcp.config import (
    ALL_TOOLSET,
    MAX_TOOLSET_SIZE,
    TOOLSET_ENV,
    TOOLSETS,
    MCPConfig,
    UnknownToolset,
    load_config,
    toolset_members,
)
from backend.services.agent_tools import BaseTool, ToolResult


def test_every_named_toolset_stays_small_and_distinct():
    assert ALL_TOOLSET not in TOOLSETS
    for name, toolset in TOOLSETS.items():
        assert 0 < len(toolset.tools) <= MAX_TOOLSET_SIZE, name
        assert len(set(toolset.tools)) == len(toolset.tools), name
        assert toolset.summary, name


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    monkeypatch.setattr(mcp_config, "_config_path", lambda: path)
    monkeypatch.delenv(TOOLSET_ENV, raising=False)
    return path


def test_default_is_all(config_file):
    cfg = load_config()
    assert cfg.tools.toolset == ALL_TOOLSET
    assert toolset_members(cfg.tools) is None


def test_flag_beats_env_beats_file(config_file, monkeypatch):
    config_file.write_text(json.dumps({"server": {"tools": {"toolset": "code"}}}))
    assert load_config().tools.toolset == "code"
    monkeypatch.setenv(TOOLSET_ENV, "Media")
    assert load_config().tools.toolset == "media"
    cfg = load_config(toolset="ops")
    assert (cfg.tools.toolset, cfg.tools.toolset_source) == ("ops", "--toolset")
    monkeypatch.setenv(TOOLSET_ENV, "")
    assert load_config().tools.toolset == "code"


def test_unknown_name_is_refused_with_the_choices(config_file):
    cfg = load_config(toolset="medai")
    with pytest.raises(UnknownToolset) as raised:
        toolset_members(cfg.tools)
    message = str(raised.value)
    assert "'medai'" in message and "--toolset" in message
    for name in [*TOOLSETS, ALL_TOOLSET]:
        assert name in message


class _Named(BaseTool):
    description = "fake"
    read_only = True

    def __init__(self, name):
        self.name = name
        super().__init__()
        self.calls = 0

    def execute(self, **_kwargs):
        self.calls += 1
        return ToolResult(success=True, output="ran")


def _pair(tool):
    return tool, mcp_types.Tool(name=tool.name, description=tool.description,
                                input_schema={"type": "object", "properties": {}})


@pytest.mark.asyncio
async def test_narrowed_server_serves_its_set_and_names_the_home_of_the_rest(monkeypatch):
    in_set = _Named(TOOLSETS["media"].tools[0])
    elsewhere = _Named(TOOLSETS["knowledge"].tools[0])
    nowhere = _Named("tool_in_no_toolset")
    pairs = [_pair(in_set), _pair(elsewhere), _pair(nowhere)]
    monkeypatch.setattr(tools_adapter, "collect_exposed_tools", lambda _cfg: pairs)
    cfg = MCPConfig()
    cfg.tools.toolset = "media"
    on_list, on_call, count = tools_adapter.build_tool_handlers(cfg)

    listed = await on_list(None, None)
    assert count == 1 and [t.name for t in listed.tools] == [in_set.name]

    ok = await on_call(None, mcp_types.CallToolRequestParams(name=in_set.name, arguments={}))
    assert not ok.is_error and in_set.calls == 1

    refused = await on_call(None, mcp_types.CallToolRequestParams(name=elsewhere.name, arguments={}))
    assert refused.is_error and elsewhere.calls == 0
    assert "toolset 'media'" in refused.content[0].text and "--toolset knowledge" in refused.content[0].text

    only_all = await on_call(None, mcp_types.CallToolRequestParams(name=nowhere.name, arguments={}))
    assert only_all.is_error and f"--toolset {ALL_TOOLSET}" in only_all.content[0].text
