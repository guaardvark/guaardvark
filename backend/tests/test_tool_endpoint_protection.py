"""Tool execution, tool jobs and automation answer only this machine by default.

Drives the real tools blueprint behind the real auth hook through Flask's test
client, with a stand-in tool registry; no backend, GPU or network.
"""

from types import SimpleNamespace

import pytest
from flask import Flask

from backend.api.tools_api import tools_bp
from backend.services.agent_tools import ToolResult
from backend.utils import auth_guard

REMOTE = "192.0.2.10"  # TEST-NET-1: never one of this machine's addresses
LOCAL = "127.0.0.1"
CALL = {"tool_name": "echo", "parameters": {}}
UNKNOWN_JOB = "/api/tools/jobs/tooljob_000000_0123456789ab"


@pytest.fixture
def app(monkeypatch):
    # Pin the machine's own addresses so no interface probe runs.
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    monkeypatch.delenv(auth_guard.TOOL_ENDPOINTS_ENV, raising=False)
    flask_app = Flask(__name__)
    flask_app.before_request(auth_guard.check_endpoint_auth)
    flask_app.register_blueprint(tools_bp)
    flask_app.tool_registry = SimpleNamespace(
        get_tool=lambda name: SimpleNamespace(parameters={}),
        execute_tool=lambda name, **params: ToolResult(success=True, output="ran"),
    )
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _execute(client, addr, headers=None):
    return client.post("/api/tools/execute", json=CALL, environ_base={"REMOTE_ADDR": addr}, headers=headers or {})


def _protected(app, path, method="GET"):
    with app.test_request_context(path, method=method):
        return auth_guard._is_protected()


def test_execute_is_refused_to_other_hosts_and_says_where_it_works(client):
    refused = _execute(client, REMOTE)
    assert refused.status_code == 403
    assert refused.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())
    assert "Network access" in auth_guard.LOCAL_ONLY_MESSAGE and "API key" in auth_guard.LOCAL_ONLY_MESSAGE


def test_execute_runs_for_this_machine(client):
    ran = _execute(client, LOCAL)
    assert ran.status_code == 200 and ran.get_json()["result"]["output"] == "ran"


def test_a_lan_device_through_the_local_proxy_counts_as_remote(client):
    assert _execute(client, LOCAL, headers={"X-Forwarded-For": REMOTE}).status_code == 403


def test_tool_jobs_follow_execute(client):
    assert client.get(UNKNOWN_JOB, environ_base={"REMOTE_ADDR": REMOTE}).status_code == 403
    # This machine gets the route's own answer for a job it does not know.
    assert client.get(UNKNOWN_JOB, environ_base={"REMOTE_ADDR": LOCAL}).status_code == 404


@pytest.mark.parametrize("path,method", [
    ("/api/automation/status", "GET"),
    ("/api/automation/mcp/status", "GET"),
    ("/api/automation/mcp/servers", "GET"),
    ("/api/automation/mcp/execute", "POST"),
    ("/api/automation/browser/navigate", "POST"),
    ("/api/automation/desktop/audit-log", "GET"),
])
def test_automation_routes_are_protected(app, path, method):
    assert _protected(app, path, method) is True


@pytest.mark.parametrize("path", [
    "/api/tools",
    "/api/tools/schemas",
    "/api/tools/categories",
    # A tool's schema, for the tools whose names start with "execute".
    "/api/tools/execute_python",
    "/api/tools/execute_javascript",
])
def test_reading_the_tool_list_and_schemas_stays_open(app, path):
    assert _protected(app, path) is False


def test_with_an_api_key_set_every_host_needs_it(client, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "k-test")
    assert _execute(client, REMOTE).status_code == 401
    assert _execute(client, REMOTE, headers={"X-API-Key": "wrong"}).status_code == 401
    assert _execute(client, REMOTE, headers={"X-API-Key": "k-test"}).status_code == 200
    unkeyed = _execute(client, LOCAL)
    assert unkeyed.status_code == 401 and unkeyed.get_json()["error"] == auth_guard.API_KEY_MESSAGE.format(machine=auth_guard.machine_name())
    assert _execute(client, LOCAL, headers={"X-API-Key": "k-test"}).status_code == 200


def test_refusals_carry_the_code_the_web_ui_reads(client, monkeypatch):
    # The web UI turns these codes into "Settings → Access" advice.
    assert _execute(client, REMOTE).get_json()["code"] == auth_guard.LOCAL_ONLY_CODE
    monkeypatch.setenv("GUAARDVARK_API_KEY", "k-test")
    assert _execute(client, REMOTE).get_json()["code"] == auth_guard.API_KEY_CODE


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE", " Off "])
def test_the_opt_out_opens_the_routes_to_other_hosts(client, app, monkeypatch, value):
    monkeypatch.setenv(auth_guard.TOOL_ENDPOINTS_ENV, value)
    assert _execute(client, REMOTE).status_code == 200
    assert client.get(UNKNOWN_JOB, environ_base={"REMOTE_ADDR": REMOTE}).status_code == 404
    assert _protected(app, "/api/automation/mcp/status") is False
    # Writing the MCP server config never opens.
    assert _protected(app, "/api/automation/mcp/servers/new", "PUT") is True
    assert _protected(app, "/api/automation/mcp/reload-config", "POST") is True


@pytest.mark.parametrize("value", ["", "true", "1", "yes", "on", "maybe"])
def test_any_other_value_keeps_the_routes_closed(client, monkeypatch, value):
    monkeypatch.setenv(auth_guard.TOOL_ENDPOINTS_ENV, value)
    assert _execute(client, REMOTE).status_code == 403
