"""Settings → Access: GET /api/auth/status, signing a browser in and out with
an HttpOnly cookie, and creating, replacing and removing the key.

Drives the real auth blueprint and the tools blueprint behind the real auth
hook through Flask's test client (one client per browser, each with its own
cookie jar), with a stand-in tool registry and a temporary .env; no backend,
GPU or network.
"""

import stat
from types import SimpleNamespace

import pytest
from flask import Flask

from backend import profiles as P
from backend.api.auth_api import auth_bp
from backend.api.tools_api import tools_bp
from backend.services.agent_tools import ToolResult
from backend.utils import api_session, auth_guard

REMOTE = "192.0.2.10"  # TEST-NET-1: never one of this machine's addresses
LOCAL = "127.0.0.1"
EXEC = {"tool_name": "echo", "parameters": {}}
ENV_NAMES = (
    "GUAARDVARK_API_KEY", "GUAARDVARK_PROTECT_TOOL_ENDPOINTS", "GUAARDVARK_DOCKER",
    "VITE_FRONTEND_URL", "VITE_PORT", "VITE_ALLOWED_HOSTS", "GUAARDVARK_CORS_ORIGINS",
)


@pytest.fixture
def env_root(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    env = root / ".env"
    env.write_text("DATABASE_URL=postgresql://x\n")
    env.chmod(0o600)
    monkeypatch.setattr(P, "repo_root", lambda: root)
    return root


@pytest.fixture
def browser(env_root, monkeypatch):
    """A factory: each call is a new browser with its own cookie jar."""
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    for name in ENV_NAMES:
        # setenv first so teardown removes whatever the routes put in
        # os.environ during the test; delenv alone records nothing when the
        # variable is absent.
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    app = Flask(__name__)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(auth_bp)
    app.register_blueprint(tools_bp)
    app.tool_registry = SimpleNamespace(
        get_tool=lambda name: SimpleNamespace(parameters={}),
        execute_tool=lambda name, **params: ToolResult(success=True, output="ran"),
    )
    return app.test_client


def _call(client, method, path, addr, key=None, headers=None, **kwargs):
    headers = dict(headers or {})
    if key is not None:
        headers["X-API-Key"] = key
    return client.open(path, method=method, environ_base={"REMOTE_ADDR": addr}, headers=headers, **kwargs)


def _status(client, addr, key=None):
    return _call(client, "GET", "/api/auth/status", addr, key).get_json()


def _execute(client, addr, key=None, headers=None):
    return _call(client, "POST", "/api/tools/execute", addr, key, headers, json=EXEC)


def _create(client):
    response = _call(client, "POST", "/api/auth/key", LOCAL, json={})
    assert response.status_code == 201
    return response.get_json()["key"]


def _sign_in(client, key, addr=REMOTE):
    return _call(client, "POST", "/api/auth/session", addr, json={"key": key})


def _session_cookie(response):
    """(value, attributes) of the sign-in cookie a response sets, or None."""
    for header in response.headers.getlist("Set-Cookie"):
        name, _, rest = header.partition("=")
        if name == api_session.cookie_name():
            value, *attrs = [p.strip() for p in rest.split(";")]
            return value, [a.split("=")[0].lower() for a in attrs], header
    return None


def _env_keys(root):
    return [line for line in (root / ".env").read_text().splitlines() if line.startswith("GUAARDVARK_API_KEY=")]


def test_status_says_where_protected_actions_work_without_a_key(browser):
    here = _call(browser(), "GET", "/api/auth/status", LOCAL)
    assert here.headers["Cache-Control"] == "no-store"
    body = here.get_json()
    assert body["key_required"] is False and body["this_machine"] is True
    assert body["key_ok"] is False and body["session_ok"] is False
    assert body["can_run_protected"] is True and body["can_manage_key"] is True
    assert "Running tools directly (Tools page) and their jobs" in body["protected"]
    elsewhere = _status(browser(), REMOTE)
    assert elsewhere["this_machine"] is False
    assert elsewhere["can_run_protected"] is False and elsewhere["can_manage_key"] is False


def test_create_writes_env_signs_this_browser_in_and_applies_at_once(browser, env_root):
    here = browser()
    response = _call(here, "POST", "/api/auth/key", LOCAL, json={})
    key = response.get_json()["key"]
    assert len(key) >= 43
    assert _env_keys(env_root) == [f"GUAARDVARK_API_KEY={key}"]
    assert "DATABASE_URL=postgresql://x" in (env_root / ".env").read_text()
    assert stat.S_IMODE((env_root / ".env").stat().st_mode) == 0o600
    value, attrs, header = _session_cookie(response)
    assert key not in header and value == api_session.session_token(key)
    assert {"httponly", "path", "samesite", "max-age"} <= set(attrs) and "secure" not in attrs
    assert "SameSite=Strict" in header and "Path=/" in header
    # This browser is signed in; another browser on this machine is not.
    assert _execute(here, LOCAL).status_code == 200
    refused = _execute(browser(), LOCAL)
    assert refused.status_code == 401 and refused.get_json()["code"] == auth_guard.API_KEY_CODE
    assert _execute(browser(), REMOTE, key).status_code == 200


def test_status_never_carries_the_key(browser):
    key = _create(browser())
    response = _call(browser(), "GET", "/api/auth/status", LOCAL)
    assert key not in response.get_data(as_text=True)
    body = response.get_json()
    assert body["key_required"] is True and body["key_ok"] is False and body["can_run_protected"] is False
    typed = _status(browser(), REMOTE, key)
    assert typed["key_ok"] is True and typed["can_manage_key"] is True
    assert _status(browser(), REMOTE, "wrong")["key_ok"] is False


def test_sign_in_needs_json_a_key_and_the_right_key(browser):
    other = browser()
    no_key_yet = _sign_in(other, "anything")
    assert no_key_yet.status_code == 409 and no_key_yet.get_json()["code"] == "no_key"
    key = _create(browser())
    assert _call(other, "POST", "/api/auth/session", REMOTE, data="key=x").status_code == 415
    assert _sign_in(other, "  ").get_json()["code"] == "key_missing"
    wrong = _sign_in(other, "nope")
    assert wrong.status_code == 401 and wrong.get_json()["code"] == "wrong_key"
    assert _session_cookie(wrong) is None
    right = _sign_in(other, key)
    assert right.status_code == 200 and right.headers["Cache-Control"] == "no-store"
    assert _session_cookie(right)[0] == api_session.session_token(key)
    status = _status(other, REMOTE)
    assert status["session_ok"] is True and status["can_run_protected"] is True
    assert _execute(other, REMOTE).status_code == 200


def test_a_cookie_sent_from_another_origins_page_is_refused(browser, monkeypatch):
    key = _create(browser())
    other = browser()
    _sign_in(other, key)
    for site in ("same-origin", "none"):
        assert _execute(other, REMOTE, headers={"Sec-Fetch-Site": site}).status_code == 200
    # Another app on the same host: SameSite lets the cookie through.
    same_site = _execute(other, REMOTE, headers={"Sec-Fetch-Site": "same-site", "Origin": "http://192.168.1.5:8080"})
    assert same_site.status_code == 401 and same_site.get_json()["credential_rejected"] is True
    cross = _execute(other, REMOTE, headers={"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"})
    assert cross.status_code == 401
    # This install's own frontend on a separate origin.
    monkeypatch.setenv("VITE_FRONTEND_URL", "http://192.168.1.5:5173")
    own = _execute(other, REMOTE, headers={"Sec-Fetch-Site": "same-site", "Origin": "http://192.168.1.5:5173"})
    assert own.status_code == 200


def test_sign_out_clears_the_cookie(browser):
    key = _create(browser())
    other = browser()
    _sign_in(other, key)
    out = _call(other, "DELETE", "/api/auth/session", REMOTE)
    value, attrs, _ = _session_cookie(out)
    assert out.status_code == 200 and value == "" and "httponly" in attrs
    refused = _execute(other, REMOTE)
    assert refused.status_code == 401 and refused.get_json()["credential_rejected"] is False


def test_create_answers_only_this_machine_and_only_json(browser):
    refused = _call(browser(), "POST", "/api/auth/key", REMOTE, json={})
    assert refused.status_code == 403 and refused.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE
    # A form post needs no preflight, so a page on another site could send it.
    assert _call(browser(), "POST", "/api/auth/key", LOCAL, data="x").status_code == 415


def test_create_twice_is_refused(browser):
    here = browser()
    _create(here)
    assert _call(browser(), "POST", "/api/auth/key", LOCAL, json={}).status_code == 401
    again = _call(here, "POST", "/api/auth/key", LOCAL, json={})
    assert again.status_code == 409 and again.get_json()["code"] == "key_exists"


def test_replace_signs_every_other_browser_out(browser, env_root):
    here = browser()
    old = _create(here)
    other = browser()
    _sign_in(other, old)
    assert _call(browser(), "PUT", "/api/auth/key", REMOTE, json={}).status_code == 401
    replaced = _call(here, "PUT", "/api/auth/key", LOCAL, json={})
    assert replaced.status_code == 200 and replaced.headers["Cache-Control"] == "no-store"
    new = replaced.get_json()["key"]
    assert new != old and _env_keys(env_root) == [f"GUAARDVARK_API_KEY={new}"]
    assert _session_cookie(replaced)[0] == api_session.session_token(new)
    assert _execute(here, LOCAL).status_code == 200
    stale = _execute(other, REMOTE)
    assert stale.status_code == 401 and stale.get_json()["credential_rejected"] is True
    assert _status(other, REMOTE)["session_rejected"] is True
    assert _execute(browser(), REMOTE, old).status_code == 401
    assert _sign_in(other, old).status_code == 401
    assert _sign_in(other, new).status_code == 200 and _execute(other, REMOTE).status_code == 200


def test_remove_clears_the_cookie_and_returns_to_this_machine_only(browser, env_root):
    here = browser()
    key = _create(here)
    other = browser()
    _sign_in(other, key)
    assert _call(browser(), "DELETE", "/api/auth/key", LOCAL).status_code == 401
    removed = _call(here, "DELETE", "/api/auth/key", LOCAL)
    assert removed.status_code == 200 and removed.get_json()["removed"] is True
    assert _session_cookie(removed)[0] == ""
    assert _env_keys(env_root) == []
    assert "DATABASE_URL=postgresql://x" in (env_root / ".env").read_text()
    leftover = _execute(other, REMOTE)
    assert leftover.status_code == 403 and leftover.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE
    assert leftover.get_json()["credential_rejected"] is True
    assert _execute(browser(), LOCAL).status_code == 200
    assert _call(browser(), "DELETE", "/api/auth/key", LOCAL).get_json()["removed"] is False


def test_the_cookie_is_secure_when_the_browser_used_https(browser):
    here = browser()
    through_local_proxy = _call(here, "POST", "/api/auth/key", LOCAL, headers={"X-Forwarded-Proto": "https"}, json={})
    assert "secure" in _session_cookie(through_local_proxy)[1]
    key = through_local_proxy.get_json()["key"]
    # Only the proxy on this machine is believed about the scheme.
    claimed = _call(browser(), "POST", "/api/auth/session", REMOTE, headers={"X-Forwarded-Proto": "https"}, json={"key": key})
    assert "secure" not in _session_cookie(claimed)[1]
    direct = browser().open("/api/auth/session", method="POST", base_url="https://localhost",
                            environ_base={"REMOTE_ADDR": REMOTE}, json={"key": key})
    assert "secure" in _session_cookie(direct)[1]


def test_each_install_has_its_own_cookie_name(tmp_path):
    assert api_session.cookie_name(tmp_path / "a") != api_session.cookie_name(tmp_path / "b")
    assert api_session.cookie_name(tmp_path / "a").startswith(api_session.COOKIE_PREFIX)


def test_a_key_set_outside_env_is_changed_where_it_was_set(browser, env_root, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "from-the-environment")
    body = _status(browser(), LOCAL, "from-the-environment")
    assert body["key_ok"] is True and body["can_manage_key"] is False
    assert "outside Settings" in body["manage_note"]
    refused = _call(browser(), "PUT", "/api/auth/key", LOCAL, "from-the-environment", json={})
    assert refused.status_code == 409 and refused.get_json()["code"] == "key_not_manageable"
    assert _env_keys(env_root) == []


def test_docker_names_the_compose_env(browser, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_DOCKER", "1")
    monkeypatch.setenv("GUAARDVARK_API_KEY", "compose-key")
    body = _status(browser(), REMOTE, "compose-key")
    assert body["docker"] is True and body["can_manage_key"] is False
    assert "docker-compose.yml" in body["manage_note"]


def test_a_key_in_env_that_is_not_loaded_asks_for_a_restart(browser, env_root):
    (env_root / ".env").write_text("GUAARDVARK_API_KEY=written-by-hand\n")
    body = _status(browser(), LOCAL)
    assert body["key_required"] is False and body["restart_needed"] is True
    assert body["can_manage_key"] is False and "Restart" in body["manage_note"]
    assert _call(browser(), "POST", "/api/auth/key", LOCAL, json={}).status_code == 409


def test_the_cluster_proxy_does_not_pass_this_installs_key_or_sign_in_on():
    from backend.services.cluster_proxy import HttpProxyForwarder

    incoming = {
        "X-API-Key": "this-install",
        "Cookie": f"theme=dark; {api_session.cookie_name()}=token",
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }
    out = HttpProxyForwarder()._sanitize_headers(incoming, SimpleNamespace(api_key="node-key"), None)
    assert "X-API-Key" not in out and "Connection" not in out
    assert out["Cookie"] == "theme=dark"
    assert out["Content-Type"] == "application/json"
    assert out["X-Guaardvark-API-Key"] == "node-key"
