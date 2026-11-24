"""Tests for the MCP client service, config, policy and tool proxies.

Uses a real stdio MCP server (tests/fixtures/mcp_echo_server.py) so protocol
behaviour (handshake, concurrency, timeouts, crashes) is exercised end to end.
"""

import json
import os
import sys
import threading
import time

import pytest

os.environ.setdefault("GUAARDVARK_MODE", "test")
os.environ["GUAARDVARK_MCP_ENABLED"] = "true"

def _import_mcp_sdk():
    """Import the installed `mcp` SDK, not backend/mcp (Guaardvark's own MCP
    server), which shadows it when backend/ is on sys.path, as the backend
    test conftest arranges. Same approach as backend/mcp/tests/conftest.py."""
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    loaded = sys.modules.get("mcp")
    if loaded is not None and (getattr(loaded, "__file__", "") or "").startswith(backend_dir + os.sep):
        for name in [m for m in sys.modules if m == "mcp" or m.startswith("mcp.")]:
            del sys.modules[name]
    saved = list(sys.path)
    try:
        sys.path = [p for p in sys.path if os.path.abspath(p or ".") != backend_dir]
        return pytest.importorskip("mcp.client.stdio")
    finally:
        sys.path = saved


_import_mcp_sdk()

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mcp_echo_server.py")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mcp_service(tmp_path, monkeypatch):
    """A fresh MCPClientService pointed at a temp config with the echo fixture."""
    from backend import config as app_config
    from backend.services import mcp_client_service as mod

    cfg_file = tmp_path / "mcp_servers.json"
    cfg_file.write_text(json.dumps({"mcpServers": {
        "fx": {"command": sys.executable, "args": [FIXTURE], "timeout": 4,
               "keywords": ["fixture"]},
    }}))
    monkeypatch.setattr(app_config, "MCP_CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(app_config, "MCP_SERVERS_CONFIG", "")
    monkeypatch.setattr(app_config, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(mod, "MCP_ENABLED", True)

    old = mod.MCPClientService._instance
    mod.MCPClientService._instance = None
    service = mod.MCPClientService.get_instance()
    yield service
    service.shutdown_sync()
    mod.MCPClientService._instance = old


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------
class TestConfig:
    def test_claude_desktop_shape(self):
        from backend.services.mcp_config import parse_config_document

        servers, errors = parse_config_document({"mcpServers": {
            "fs": {"command": "npx", "args": ["-y", "pkg", "/tmp"], "autoConnect": True,
                   "denyTools": ["write_*"]},
        }}, "test")
        assert errors == []
        fs = servers["fs"]
        assert fs.command == "npx" and fs.args == ["-y", "pkg", "/tmp"]
        assert fs.auto_connect is True and fs.deny_tools == ["write_*"]

    def test_legacy_list_command_shape(self):
        from backend.services.mcp_config import parse_config_document

        servers, errors = parse_config_document({"old": {"command": ["node", "server.js", "--x"]}}, "t")
        assert errors == []
        assert servers["old"].command == "node" and servers["old"].args == ["server.js", "--x"]

    def test_remote_servers_rejected(self):
        from backend.services.mcp_config import MCPConfigError, parse_server

        for raw in ({"url": "https://example.com/mcp"},
                    {"transport": "http", "url": "https://example.com/mcp"},
                    {"command": "x", "headers": {"Authorization": "Bearer t"}}):
            with pytest.raises(MCPConfigError, match="remote MCP servers are not supported"):
                parse_server("remote", raw)

    @pytest.mark.parametrize("name,raw", [
        ("bad name!", {"command": "x"}),
        ("nocmd", {}),
        ("remote", {"transport": "http", "url": "https://x"}),
        ("badargs", {"command": "x", "args": [{"a": 1}]}),
    ])
    def test_invalid_entries_reported_not_raised(self, name, raw):
        from backend.services.mcp_config import parse_config_document

        servers, errors = parse_config_document({"mcpServers": {name: raw}}, "t")
        assert name not in servers
        assert len(errors) == 1

    def test_child_env_withholds_secrets(self):
        from backend.services.mcp_config import build_child_env, parse_server

        cfg = parse_server("s", {"command": "x", "env": {"GITHUB_TOKEN": "${MY_GH}", "PLAIN": "1"}})
        env = build_child_env(cfg, {"PATH": "/bin", "DATABASE_URL": "pg://secret",
                                    "GUAARDVARK_API_KEY": "k", "MY_GH": "ghp_x",
                                    "NODE_OPTIONS": "--x", "NODE_AUTH_TOKEN": "t"})
        assert env["PATH"] == "/bin" and env["NODE_OPTIONS"] == "--x"
        assert env["GITHUB_TOKEN"] == "ghp_x" and env["PLAIN"] == "1"
        for leaked in ("DATABASE_URL", "GUAARDVARK_API_KEY", "MY_GH", "NODE_AUTH_TOKEN"):
            assert leaked not in env


    def test_fixed_args_round_trip(self):
        from backend.services.mcp_config import parse_server

        cfg = parse_server("zg", {"command": "zg", "fixedArgs": {"root": "${GUAARDVARK_ROOT}"}})
        assert cfg.fixed_args == {"root": "${GUAARDVARK_ROOT}"}
        assert cfg.to_json()["fixedArgs"] == {"root": "${GUAARDVARK_ROOT}"}
        assert cfg.to_editable()["fixedArgs"] == {"root": "${GUAARDVARK_ROOT}"}
        assert cfg.to_public()["fixed_arg_names"] == ["root"]
        assert parse_server("zg", cfg.to_json()).fixed_args == cfg.fixed_args

    def test_fixed_args_expand_the_checkout_root(self):
        from backend import config
        from backend.services.mcp_config import parse_server, resolve_fixed_args

        cfg = parse_server("zg", {"command": "zg", "fixedArgs": {"root": "${GUAARDVARK_ROOT}",
                                                                 "limit": "5"}})
        assert resolve_fixed_args(cfg, {}) == {"root": str(config.GUAARDVARK_ROOT), "limit": "5"}
        assert resolve_fixed_args(cfg, {"GUAARDVARK_ROOT": "/srv/other"})["root"] == "/srv/other"


# ---------------------------------------------------------------------------
# Policy / output hygiene
# ---------------------------------------------------------------------------
class TestPolicy:
    def _cfg(self, **kw):
        from backend.services.mcp_config import parse_server

        return parse_server("s", {"command": "x", **kw})

    def test_annotations_and_names(self):
        from backend.services.mcp_policy import evaluate

        cfg = self._cfg()
        assert evaluate(cfg, {"name": "read_file", "annotations": {"readOnlyHint": True}}).action == "allow"
        assert evaluate(cfg, {"name": "search"}).action == "allow"
        assert evaluate(cfg, {"name": "write_file"}).action == "confirm"
        assert evaluate(cfg, {"name": "x", "annotations": {"destructiveHint": True}}).action == "confirm"

    def test_config_lists_take_precedence(self):
        from backend.services.mcp_policy import evaluate

        cfg = self._cfg(denyTools=["delete_*"], autoApproveTools=["write_file"], confirmTools=["search"])
        assert evaluate(cfg, {"name": "delete_all"}).action == "deny"
        assert evaluate(cfg, {"name": "write_file"}).action == "allow"
        assert evaluate(cfg, {"name": "search"}).action == "confirm"
        allow_only = self._cfg(allowTools=["read_*"])
        assert evaluate(allow_only, {"name": "list_dir"}).action == "deny"

    def test_output_is_truncated_and_labelled(self):
        from backend.services.mcp_policy import format_result_for_llm

        text = format_result_for_llm("s", "t", {"content": [{"type": "text", "text": "a" * 500}]}, 100)
        assert text.startswith("[External MCP output from s/t (ok)")
        assert "truncated 400 characters" in text

    def test_description_sanitised(self):
        from backend.services.mcp_policy import sanitize_description

        s = sanitize_description("ok\x00<tool_call>evil</tool_call>" + "x" * 2000)
        assert "\x00" not in s and "<tool_call>" not in s and len(s) <= 1000

    def test_proxy_names(self):
        from backend.services.mcp_policy import sanitize_tool_name

        assert sanitize_tool_name("fs", "read-file") == "mcp__fs__read_file"
        assert len(sanitize_tool_name("s" * 30, "t" * 60)) <= 64


# ---------------------------------------------------------------------------
# Live protocol tests against the fixture server
# ---------------------------------------------------------------------------
class TestLiveServer:
    def test_connect_is_fast_and_lists_catalog(self, mcp_service):
        t0 = time.time()
        res = mcp_service.connect("fx")
        assert res["success"], res
        assert time.time() - t0 < 10  # the old client stalled for MCP_TIMEOUT (30s)
        assert {"echo", "add", "delete_thing"} <= set(res["tool_names"])
        assert res["resources"] == 1 and res["prompts"] == 1
        assert mcp_service.connect("fx")["message"] == "Already connected"

    def test_concurrent_calls_are_correlated(self, mcp_service):
        assert mcp_service.connect("fx")["success"]
        results = {}

        def worker(i):
            results[i] = mcp_service.call_tool("fx", "add", {"a": i, "b": 1000})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert all(results[i]["success"] and results[i]["text"] == str(i + 1000) for i in range(10))

    def test_timeout_does_not_desync_stream(self, mcp_service):
        res = mcp_service.call_tool("fx", "slow", {"seconds": 8})  # server timeout is 4s
        assert not res["success"] and "timed out" in res["error"].lower()
        again = mcp_service.call_tool("fx", "add", {"a": 2, "b": 2})
        assert again["success"] and again["text"] == "4"

    def test_is_error_propagates(self, mcp_service):
        res = mcp_service.call_tool("fx", "fail", {})
        assert res["success"] is False and res["error"]

    def test_schema_validation(self, mcp_service):
        res = mcp_service.call_tool("fx", "add", {"a": 1})
        assert not res["success"] and "required" in res["error"]

    def test_policy_requires_approval(self, mcp_service):
        res = mcp_service.call_tool("fx", "delete_thing", {"name": "x"})
        assert res["success"] is False and res["requires_confirmation"] is True
        assert res["proxy_tool"] == "mcp__fx__delete_thing"
        ok = mcp_service.call_tool("fx", "delete_thing", {"name": "x"}, approved=True)
        assert ok["success"] and ok["text"] == "deleted x"

    def test_env_is_scrubbed(self, mcp_service, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://secret")
        monkeypatch.setenv("SOME_API_KEY", "secret")
        res = mcp_service.call_tool("fx", "dump_env", {})
        names = set(res["text"].split("\n"))
        assert "DATABASE_URL" not in names and "SOME_API_KEY" not in names
        assert "PATH" in names

    def test_resources_and_prompts(self, mcp_service):
        assert mcp_service.read_resource("fx", "fixture://greeting")["text"] == "hello from fixture"
        prompt = mcp_service.get_prompt("fx", "greet", {"name": "Ada"})
        assert "Ada" in prompt["messages"][0]["content"]["text"]

    def test_crash_detected_and_reconnects_on_demand(self, mcp_service):
        assert mcp_service.connect("fx")["success"]
        res = mcp_service.call_tool("fx", "crash", {})
        assert not res["success"]
        deadline = time.time() + 10
        while time.time() < deadline and mcp_service.get_server("fx")["server"]["status"] == "connected":
            time.sleep(0.1)
        assert mcp_service.get_server("fx")["server"]["status"] == "error"
        assert mcp_service.call_tool("fx", "add", {"a": 1, "b": 1})["text"] == "2"

    def test_disconnect_and_stderr_log(self, mcp_service):
        mcp_service.call_tool("fx", "echo", {"text": "hi"})
        detail = mcp_service.get_server("fx")["server"]
        assert "echo called" in detail["stderr_tail"]
        assert mcp_service.disconnect("fx")["success"]
        assert mcp_service.get_state()["servers_connected"] == 0

    def test_audit_log_records_calls(self, mcp_service):
        mcp_service.call_tool("fx", "add", {"a": 1, "b": 2}, caller="rest")
        entry = mcp_service.get_audit_log(1)[0]
        assert entry["tool"] == "add" and entry["caller"] == "rest" and entry["success"]

    def test_list_configured_servers_is_redacted(self, mcp_service):
        server = mcp_service.list_configured_servers()["servers"][0]
        assert server["command"] == os.path.basename(sys.executable)
        assert "args" not in server

    def test_upsert_and_remove_persist(self, mcp_service):
        res = mcp_service.upsert_server("extra", {"command": "node", "args": ["x.js"],
                                                  "env": {"TOKEN": "s3cret"}})
        assert res["success"], res
        on_disk = json.load(open(mcp_service.config_file))["mcpServers"]
        assert on_disk["extra"]["env"]["TOKEN"] == "s3cret"
        # "***" keeps the stored secret
        assert mcp_service.upsert_server("extra", {"command": "node", "env": {"TOKEN": "***"}})["success"]
        assert json.load(open(mcp_service.config_file))["mcpServers"]["extra"]["env"]["TOKEN"] == "s3cret"
        assert mcp_service.remove_server("extra")["success"]
        assert "extra" not in json.load(open(mcp_service.config_file))["mcpServers"]
        assert mcp_service.upsert_server("bad name", {"command": "x"})["success"] is False

    def test_shutdown_leaves_no_children(self, mcp_service):
        import subprocess

        assert mcp_service.connect("fx")["success"]
        mcp_service.shutdown_sync()
        time.sleep(0.5)
        out = subprocess.run(["pgrep", "-f", FIXTURE], capture_output=True, text=True)
        assert out.stdout.strip() == ""


# ---------------------------------------------------------------------------
# First-class proxy tools and chat tool selection
# ---------------------------------------------------------------------------
@pytest.fixture
def proxied(mcp_service):
    from backend.services.agent_tools import get_tool_registry
    from backend.tools import mcp_tools

    from backend.tools.tool_registry_init import register_mcp_tools

    register_mcp_tools()
    mcp_tools.install_proxy_sync()
    assert mcp_service.connect("fx")["success"]
    yield get_tool_registry()
    mcp_service.disconnect("fx")


class TestProxyTools:
    def test_proxies_registered_with_real_schema(self, proxied):
        add = proxied.get_tool("mcp__fx__add")
        assert add is not None
        assert set(add.parameters) == {"a", "b"}
        assert add.parameters["a"].type == "int" and add.parameters["a"].required
        assert add.get_json_schema()["input_schema"]["required"] == ["a", "b"]
        assert add.requires_confirmation is False
        assert proxied.get_tool("mcp__fx__delete_thing").requires_confirmation is True

    def test_proxy_executes_through_registry(self, proxied):
        res = proxied.execute_tool("mcp__fx__add", a=2, b=3)
        assert res.success
        assert "[External MCP output from fx/add (ok)" in res.output and "\n5\n" in res.output

    def test_destructive_proxy_needs_approval(self, proxied):
        from backend.services.tool_confirmation import trusted_caller

        tool = proxied.get_tool("mcp__fx__delete_thing")
        assert tool.requires_approval is True  # raises the chat's approval card
        assert proxied.get_tool("mcp__fx__add").requires_approval is False
        denied = proxied.execute_tool("mcp__fx__delete_thing", name="a")
        assert not denied.success and denied.metadata.get("requires_approval")
        with trusted_caller("chat_approval"):
            assert proxied.execute_tool("mcp__fx__delete_thing", name="a").success

    def test_mcp_execute_cannot_sidestep_approval(self, proxied):
        from backend.services.tool_confirmation import trusted_caller

        with trusted_caller("chat_approval"):
            res = proxied.execute_tool("mcp_execute", server="fx", tool="delete_thing",
                                       arguments={"name": "b"})
        assert not res.success and "mcp__fx__delete_thing" in res.error
        ok = proxied.execute_tool("mcp_execute", server="fx", tool="add", arguments='{"a": 1, "b": 4}')
        assert ok.success and "\n5\n" in ok.output  # JSON-string arguments are decoded

    def test_denied_tools_not_exposed(self, mcp_service, monkeypatch):
        from backend.services.agent_tools import get_tool_registry
        from backend.tools import mcp_tools

        mcp_tools.install_proxy_sync()
        mcp_service._runtimes["fx"].config.deny_tools = ["delete_*"]
        assert mcp_service.connect("fx")["success"]
        reg = get_tool_registry()
        assert reg.get_tool("mcp__fx__add") and reg.get_tool("mcp__fx__delete_thing") is None
        res = mcp_service.call_tool("fx", "delete_thing", {"name": "x"}, approved=True)
        assert not res["success"] and "blocked" in res["error"]

    def test_fixed_args_are_hidden_from_the_model_and_sent(self, mcp_service):
        from backend.services.agent_tools import get_tool_registry
        from backend.tools import mcp_tools

        mcp_tools.install_proxy_sync()
        mcp_service._runtimes["fx"].config.fixed_args = {"b": "4"}
        assert mcp_service.connect("fx")["success"]
        add = get_tool_registry().get_tool("mcp__fx__add")
        assert set(add.parameters) == {"a"}  # the model is asked for a only
        res = get_tool_registry().execute_tool("mcp__fx__add", a=1)
        assert res.success and "\n5\n" in res.output  # b arrived as the integer 4
        res = get_tool_registry().execute_tool("mcp__fx__add", a=1, b=100)
        assert res.success and "\n5\n" in res.output  # a model-sent value never wins

    def test_proxies_removed_on_disconnect(self, proxied, mcp_service):
        mcp_service.disconnect("fx")
        assert proxied.get_tool("mcp__fx__add") is None

    def test_list_changed_resyncs(self, proxied, mcp_service):
        import asyncio

        rt = mcp_service._runtimes["fx"]
        rt.tools = [t for t in rt.tools if t["name"] != "add"]
        mcp_service._notify("tools_changed", "fx")
        assert proxied.get_tool("mcp__fx__add") is None
        # a real notification triggers a catalog refresh + resync
        handler = mcp_service._make_message_handler(rt)

        class _N:
            method = "notifications/tools/list_changed"

        asyncio.run_coroutine_threadsafe(handler(_N()), mcp_service._loop).result(5)
        deadline = time.time() + 5
        while proxied.get_tool("mcp__fx__add") is None and time.time() < deadline:
            time.sleep(0.05)
        assert proxied.get_tool("mcp__fx__add") is not None

    def test_chat_selection_finds_mcp_tools(self, proxied):
        from backend.services.unified_chat_engine import merge_forced_tools, select_mcp_tools_for_message

        assert select_mcp_tools_for_message("what's the weather", proxied) == []
        picked = select_mcp_tools_for_message("use mcp to add 2 and 3", proxied)
        assert picked and picked[0] == "mcp__fx__add"
        assert "mcp__fx__echo" in select_mcp_tools_for_message("ask fx to echo hi", proxied)
        assert select_mcp_tools_for_message("run the fixture thing", proxied)  # configured keyword
        merged = merge_forced_tools(["web_search", "a", "b"], ["mcp__fx__add"], max_tools=3)
        assert merged[:2] == ["web_search", "mcp__fx__add"]


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@pytest.fixture
def api(mcp_service):
    from flask import Flask

    from backend.api.automation_api import automation_bp
    from backend.utils.auth_guard import check_endpoint_auth

    app = Flask(__name__)
    app.register_blueprint(automation_bp)
    app.before_request(check_endpoint_auth)
    return app.test_client()


class TestRestApi:
    def test_config_writes_blocked_for_remote_hosts(self, api, monkeypatch):
        monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
        monkeypatch.delenv("GUAARDVARK_PROTECT_TOOL_ENDPOINTS", raising=False)
        remote = {"REMOTE_ADDR": "203.0.113.5"}
        entry = {"command": "sh", "args": ["-c", "true"]}
        assert api.put("/api/automation/mcp/servers/evil", json=entry, environ_base=remote).status_code == 403
        assert api.delete("/api/automation/mcp/servers/fx", environ_base=remote).status_code == 403
        assert api.post("/api/automation/mcp/reload-config", environ_base=remote).status_code == 403
        # Reads are closed to other hosts too, unless the install opts out.
        assert api.get("/api/automation/mcp/servers", environ_base=remote).status_code == 403

    def test_opting_out_reopens_reads_but_not_config_writes(self, api, monkeypatch):
        monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
        monkeypatch.setenv("GUAARDVARK_PROTECT_TOOL_ENDPOINTS", "false")
        remote = {"REMOTE_ADDR": "203.0.113.5"}
        entry = {"command": "sh", "args": ["-c", "true"]}
        # The views are redacted.
        assert api.get("/api/automation/mcp/servers", environ_base=remote).status_code == 200
        assert api.put("/api/automation/mcp/servers/evil", json=entry, environ_base=remote).status_code == 403
        assert api.delete("/api/automation/mcp/servers/fx", environ_base=remote).status_code == 403
        assert api.post("/api/automation/mcp/reload-config", environ_base=remote).status_code == 403

    def test_config_writes_need_the_key_when_configured(self, api, monkeypatch):
        monkeypatch.setenv("GUAARDVARK_API_KEY", "k1")
        assert api.post("/api/automation/mcp/reload-config").status_code == 401
        assert api.post("/api/automation/mcp/reload-config", headers={"X-API-Key": "k1"}).status_code == 200

    def test_connect_list_execute_audit(self, api):
        assert api.post("/api/automation/mcp/connect", json={"server": "fx"}).get_json()["success"]
        tools = api.get("/api/automation/mcp/tools?server=fx").get_json()["tools"]
        by_name = {t["name"]: t for t in tools}
        assert by_name["delete_thing"]["policy"] == "confirm"
        assert by_name["add"]["proxyName"] == "mcp__fx__add"
        res = api.post("/api/automation/mcp/execute",
                       json={"server": "fx", "tool": "delete_thing", "arguments": {"name": "z"}})
        assert res.status_code == 200 and res.get_json()["text"] == "deleted z"
        bad = api.post("/api/automation/mcp/execute", json={"server": "fx", "tool": "add", "arguments": []})
        assert bad.status_code == 400
        audit = api.get("/api/automation/mcp/audit-log?limit=5").get_json()["entries"]
        assert audit[0]["tool"] == "delete_thing" and audit[0]["caller"] == "rest"

    def test_remote_caller_cannot_run_gated_tools(self, api, monkeypatch):
        monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
        monkeypatch.delenv("GUAARDVARK_PROTECT_TOOL_ENDPOINTS", raising=False)
        remote = {"REMOTE_ADDR": "203.0.113.5"}
        assert api.post("/api/automation/mcp/connect", json={"server": "fx"}).get_json()["success"]
        call = {"server": "fx", "tool": "add", "arguments": {"a": 1, "b": 2}}
        assert api.post("/api/automation/mcp/execute", environ_base=remote, json=call).status_code == 403
        # The opt-out for tool endpoints does not open a route that runs code
        # to other hosts (auth_guard.RUNS_CODE_PATHS): with no key set it still
        # answers this machine only.
        monkeypatch.setenv("GUAARDVARK_PROTECT_TOOL_ENDPOINTS", "false")
        for tool, arguments in (("delete_thing", {"name": "z"}), ("add", {"a": 1, "b": 2})):
            refused = api.post("/api/automation/mcp/execute", environ_base=remote,
                               json={"server": "fx", "tool": tool, "arguments": arguments})
            assert refused.status_code == 403 and refused.get_json()["code"] == "local_only"
        ok = api.post("/api/automation/mcp/execute", json=call)
        assert ok.get_json()["success"]

    def test_unknown_server_404(self, api):
        assert api.post("/api/automation/mcp/connect", json={"server": "nope"}).status_code == 404
        assert api.get("/api/automation/mcp/servers/nope").status_code == 404

    def test_resources_and_prompts(self, api):
        api.post("/api/automation/mcp/connect", json={"server": "fx"})
        res = api.post("/api/automation/mcp/resources/read", json={"server": "fx", "uri": "fixture://greeting"})
        assert res.get_json()["text"] == "hello from fixture"
        prompts = api.get("/api/automation/mcp/prompts?server=fx").get_json()["prompts"]["fx"]
        assert prompts[0]["name"] == "greet"
        got = api.post("/api/automation/mcp/prompts/get",
                       json={"server": "fx", "name": "greet", "arguments": {"name": "Q"}})
        assert "Q" in got.get_json()["messages"][0]["content"]["text"]

    def test_server_crud(self, api):
        put = api.put("/api/automation/mcp/servers/new1", json={"command": "node", "args": ["a.js"],
                                                                 "autoConnect": True})
        assert put.status_code == 200, put.get_json()
        detail = api.get("/api/automation/mcp/servers/new1").get_json()["server"]
        assert detail["auto_connect"] is True and detail["status"] == "disconnected"
        bad = api.put("/api/automation/mcp/servers/new1", json={"transport": "http", "url": "nope"})
        assert bad.status_code == 400
        assert api.delete("/api/automation/mcp/servers/new1").status_code == 200
        assert api.get("/api/automation/mcp/servers/new1").status_code == 404


def test_editable_definition_masks_secrets(mcp_service):
    assert mcp_service.upsert_server("ed", {"command": "/usr/bin/node", "args": ["srv.js", "--port", "1"],
                                            "env": {"TOKEN": "s3cret"}})["success"]
    definition = mcp_service.get_server("ed")["server"]["definition"]
    assert definition["command"] == "/usr/bin/node" and definition["args"] == ["srv.js", "--port", "1"]
    assert definition["env"] == {"TOKEN": "***"}
    # round-tripping the editable definition keeps the stored secret
    assert mcp_service.upsert_server("ed", definition)["success"]
    assert json.load(open(mcp_service.config_file))["mcpServers"]["ed"]["env"]["TOKEN"] == "s3cret"
