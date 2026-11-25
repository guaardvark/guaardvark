"""The MCP server's own configuration: its on/off switch and per-call ceiling
(kept apart from the backend's MCP client settings, which share ``.env``), how
``data/config/mcp.json`` is read, and what the tools adapter does with the
arguments and waits of a call."""

import asyncio
import json
import logging
import time
from pathlib import Path

import pytest

from backend.tests._mcp_sdk import use_mcp_sdk

use_mcp_sdk()  # mcp below must be the SDK; see backend/tests/_mcp_sdk.py

import mcp.types as mcp_types  # noqa: E402

from backend.mcp import config as mcp_config
from backend.mcp import tools_adapter
from backend.mcp.config import MCPConfig
from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

_ENV = ("GUAARDVARK_MCP_SERVER_ENABLED", "GUAARDVARK_MCP_SERVER_TIMEOUT",
        "GUAARDVARK_MCP_TIMEOUT", "GUAARDVARK_MCP_ENABLED")


@pytest.fixture
def mcp_json(tmp_path, monkeypatch):
    """``load_config`` reads a file in ``tmp_path`` and none of the MCP
    variables are set. Returns a function that writes the file."""
    path = tmp_path / "mcp.json"
    monkeypatch.setattr(mcp_config, "_config_path", lambda: path)
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)

    def write(document):
        path.write_text(document if isinstance(document, str) else json.dumps(document))

    return write


# ---- the on/off switch ------------------------------------------------------------------------
def test_the_clients_switch_does_not_stop_the_server(mcp_json, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_MCP_ENABLED", "false")

    assert mcp_config.load_config().enabled is True


def test_the_creator_profile_leaves_the_server_on(mcp_json, monkeypatch):
    """The profile turns the backend's MCP client off, and the server process
    loads the same profile."""
    profile = Path(mcp_config.__file__).resolve().parents[1] / "profiles" / "creator.json"
    env = json.loads(profile.read_text())["env"]
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))

    assert env["GUAARDVARK_MCP_ENABLED"] == "false"
    assert mcp_config.load_config().enabled is True


@pytest.mark.parametrize("value,enabled", [("false", False), ("0", False), ("off", False),
                                           ("true", True), ("1", True), ("maybe", True)])
def test_the_servers_own_switch(mcp_json, monkeypatch, value, enabled):
    monkeypatch.setenv("GUAARDVARK_MCP_SERVER_ENABLED", value)

    cfg = mcp_config.load_config()
    assert cfg.enabled is enabled
    assert cfg.disabled_by == ("" if enabled else "GUAARDVARK_MCP_SERVER_ENABLED")


def test_mcp_json_switches_the_server_off_and_the_environment_overrides_it(mcp_json, monkeypatch):
    mcp_json({"server": {"enabled": False}})
    cfg = mcp_config.load_config()
    assert cfg.enabled is False and "mcp.json" in cfg.disabled_by

    monkeypatch.setenv("GUAARDVARK_MCP_SERVER_ENABLED", "true")
    cfg = mcp_config.load_config()
    assert cfg.enabled is True and cfg.disabled_by == ""


def test_a_switched_off_server_is_not_built():
    from backend.mcp.server import MCPServerDisabled, build_server

    with pytest.raises(MCPServerDisabled, match="switched off by GUAARDVARK_MCP_SERVER_ENABLED"):
        build_server(MCPConfig(enabled=False, disabled_by="GUAARDVARK_MCP_SERVER_ENABLED"))


def test_the_refusal_goes_to_stderr_and_exits_non_zero(capsys, monkeypatch):
    # Importing the entrypoint marks the process as the MCP server; keep that
    # mark from outliving this test.
    monkeypatch.setenv("GUAARDVARK_MCP_PROCESS", "1")
    from backend.mcp.__main__ import _refuse_to_start

    assert _refuse_to_start(RuntimeError("switched off")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "switched off" in captured.err


# ---- the per-call ceiling ---------------------------------------------------------------------
@pytest.mark.parametrize("env,expected", [
    ({}, 120),
    ({"GUAARDVARK_MCP_TIMEOUT": "45"}, 45),
    ({"GUAARDVARK_MCP_SERVER_TIMEOUT": "600"}, 600),
    ({"GUAARDVARK_MCP_SERVER_TIMEOUT": "600", "GUAARDVARK_MCP_TIMEOUT": "30"}, 600),
    ({"GUAARDVARK_MCP_SERVER_TIMEOUT": "abc", "GUAARDVARK_MCP_TIMEOUT": "45"}, 45),
])
def test_the_servers_variable_wins_and_the_shared_one_is_the_fallback(mcp_json, monkeypatch, env, expected):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert mcp_config.load_config().timeout_seconds == expected


def test_the_timeout_text_names_the_servers_variable():
    text = tools_adapter._timeout_message("search_code", 120, read_only=True)

    assert "GUAARDVARK_MCP_SERVER_TIMEOUT" in text
    # The first run is still going, and a retry waits for it rather than stacking a second.
    assert "waits for this run instead of starting a second one" in text


@pytest.mark.parametrize("value", [0, -5, "0", True])
def test_a_timeout_below_the_floor_is_refused(mcp_json, value):
    mcp_json({"server": {"timeout_seconds": value}})

    assert mcp_config.load_config().timeout_seconds == MCPConfig().timeout_seconds


@pytest.mark.parametrize("name", ["GUAARDVARK_MCP_SERVER_TIMEOUT", "GUAARDVARK_MCP_TIMEOUT"])
def test_a_zero_timeout_in_the_environment_keeps_the_files_value(mcp_json, monkeypatch, name):
    mcp_json({"server": {"timeout_seconds": 300}})
    monkeypatch.setenv(name, "0")

    assert mcp_config.load_config().timeout_seconds == 300


def test_the_floor_itself_is_accepted(mcp_json, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_MCP_SERVER_TIMEOUT", str(mcp_config.MIN_TIMEOUT_SECONDS))

    assert mcp_config.load_config().timeout_seconds == mcp_config.MIN_TIMEOUT_SECONDS


# ---- data/config/mcp.json ---------------------------------------------------------------------
@pytest.mark.parametrize("document", [
    {"server": {"tools": None}},
    {"server": {"tools": {"deny_categories": None}}},
    {"server": []},
    [],
    {"server": {"resources": None}},
    "{ not json",
])
def test_a_section_of_the_wrong_type_falls_back_to_defaults_and_says_so(mcp_json, caplog, document):
    mcp_json(document)

    with caplog.at_level(logging.WARNING, logger=mcp_config.logger.name):
        cfg = mcp_config.load_config()

    assert cfg == MCPConfig()
    assert [record for record in caplog.records if record.name == mcp_config.logger.name]


def test_a_single_name_is_one_name_not_its_characters(mcp_json):
    mcp_json({"server": {"tools": {"allow": "system_command", "deny": "web_search"}}})

    cfg = mcp_config.load_config()
    assert cfg.tools.allow == ["system_command"] and cfg.tools.deny == ["web_search"]


def test_one_bad_value_does_not_drop_the_settings_around_it(mcp_json):
    mcp_json({"server": {
        "timeout_seconds": "90.5",
        "tools": {"deny": ["web_search"]},
        "resources": {"max_inline_bytes": "lots", "outputs_root_files": False},
    }})

    cfg = mcp_config.load_config()
    assert cfg.timeout_seconds == MCPConfig().timeout_seconds
    assert cfg.tools.deny == ["web_search"] and cfg.resources.outputs_root_files is False
    assert cfg.resources.max_inline_bytes == MCPConfig().resources.max_inline_bytes


def test_an_unreadable_safety_flag_keeps_the_safe_default(mcp_json):
    mcp_json({"server": {"tools": {"hide_dangerous": "", "hide_approval_required": None}}})

    cfg = mcp_config.load_config()
    assert cfg.tools.hide_dangerous is True and cfg.tools.hide_approval_required is True


def test_argument_defaults_merge_with_the_built_in_map(mcp_json):
    built_in = {"generate_image": {"wait_for_result": False}}
    assert MCPConfig().tools.argument_defaults == built_in

    mcp_json({"server": {"tools": {"argument_defaults": {"generate_video": {"audio": False}}}}})
    assert mcp_config.load_config().tools.argument_defaults == {**built_in, "generate_video": {"audio": False}}

    mcp_json({"server": {"tools": {"argument_defaults": {"generate_image": {"wait_for_result": True}}}}})
    assert mcp_config.load_config().tools.argument_defaults == {"generate_image": {"wait_for_result": True}}
    assert MCPConfig().tools.argument_defaults == built_in


# ---- arguments a client may not supply --------------------------------------------------------
class _Echo(BaseTool):
    name, description, read_only = "echo", "records what it is given", True
    parameters = {"text": ToolParameter(name="text", type="string", required=True)}

    def __init__(self):
        super().__init__()
        self.seen = None

    def execute(self, **kwargs):
        self.seen = kwargs
        return ToolResult(success=True, output="ok")


def _call(monkeypatch, tool, arguments, config=None):
    published = mcp_types.Tool(
        name=tool.name, description=tool.description,
        input_schema=tools_adapter._tool_input_schema(tool), annotations=tools_adapter._annotations(tool))
    monkeypatch.setattr(tools_adapter, "collect_exposed_tools", lambda _cfg: [(tool, published)])
    _on_list, on_call, _count = tools_adapter.build_tool_handlers(config or MCPConfig())
    return asyncio.run(on_call(None, mcp_types.CallToolRequestParams(name=tool.name, arguments=arguments)))


def test_underscore_keys_from_a_client_do_not_reach_the_tool(monkeypatch):
    tool = _Echo()

    result = _call(monkeypatch, tool, {
        "text": "hi", "_agent_context": {"project_id": 7, "workspace_root": "/x"}, "_anything": 1, "extra": 2})

    assert not result.is_error
    assert tool.seen == {"text": "hi", "extra": 2}


def test_an_underscore_key_the_tool_publishes_is_kept():
    arguments = {"_mode": "a", "_agent_context": {"project_id": 7}}

    dropped = tools_adapter._drop_internal_arguments(arguments, {"properties": {"_mode": {"type": "string"}}})

    assert dropped == ["_agent_context"] and arguments == {"_mode": "a"}


# ---- a waiting call outlasts the tool's own wait ----------------------------------------------
class _Waits(BaseTool):
    """Like generate_video: work first, then its own bounded wait, then the
    answer that carries the batch id."""
    name, description, read_only = "waits", "waits for a render", False
    parameters = {"wait_for_result": ToolParameter(name="wait_for_result", type="bool", required=False)}
    MAX_WAIT_S = 0.2
    BEFORE_THE_WAIT_S = 0.15

    def execute(self, **_kwargs):
        time.sleep(self.BEFORE_THE_WAIT_S + self.MAX_WAIT_S)
        return ToolResult(success=True, output="still running (batch b-123)")


def test_the_wait_ceiling_stays_above_a_tools_own_wait():
    cfg = MCPConfig()
    waiting = {"wait_for_result": True}

    class Long(BaseTool):
        name, description, MAX_WAIT_S = "long", "waits long", 5000

    assert tools_adapter._call_timeout(cfg, waiting, Long()) == 5000 + mcp_config.WAIT_HEADROOM_SECONDS
    assert tools_adapter._call_timeout(cfg, waiting, _Waits()) == mcp_config.WAIT_TIMEOUT_SECONDS
    assert tools_adapter._call_timeout(cfg, waiting) == mcp_config.WAIT_TIMEOUT_SECONDS
    assert tools_adapter._call_timeout(cfg, {}, Long()) == cfg.timeout_seconds


def test_generate_video_and_generate_image_answer_before_the_adapter_does():
    from backend.tools.image_tools import ImageGeneratorTool, VideoGeneratorTool
    from backend.utils import backend_http

    cfg = MCPConfig()
    waiting = {"wait_for_result": True}
    # generate_video forwards to the backend with its wait plus 60 s as the read timeout.
    assert tools_adapter._call_timeout(cfg, waiting, VideoGeneratorTool) > VideoGeneratorTool.MAX_WAIT_S + 60
    assert (tools_adapter._call_timeout(cfg, waiting, ImageGeneratorTool)
            > ImageGeneratorTool.MAX_WAIT_S + backend_http.DEFAULT_READ_TIMEOUT)


def test_a_tools_own_still_running_answer_reaches_the_client(monkeypatch):
    # Scaled down: the tool's wait equals the wait ceiling, as generate_video's does.
    monkeypatch.setattr(tools_adapter, "WAIT_TIMEOUT_SECONDS", _Waits.MAX_WAIT_S)
    monkeypatch.setattr(tools_adapter, "WAIT_HEADROOM_SECONDS", 1.0)

    result = _call(monkeypatch, _Waits(), {"wait_for_result": True}, MCPConfig(timeout_seconds=0.05))

    assert not result.is_error and "b-123" in result.content[0].text
