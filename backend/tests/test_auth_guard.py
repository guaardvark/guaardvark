import pytest
from flask import Flask
from importlib import util
from pathlib import Path


_AUTH_GUARD_PATH = Path(__file__).resolve().parents[1] / "utils" / "auth_guard.py"
_AUTH_GUARD_SPEC = util.spec_from_file_location("auth_guard_under_test", _AUTH_GUARD_PATH)
auth_guard = util.module_from_spec(_AUTH_GUARD_SPEC)
assert _AUTH_GUARD_SPEC and _AUTH_GUARD_SPEC.loader
_AUTH_GUARD_SPEC.loader.exec_module(auth_guard)


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.before_request(auth_guard.check_endpoint_auth)

    @test_app.route("/api/files/write", methods=["POST"])
    def files_write():
        return {"ok": True}

    @test_app.route("/api/files/browse", methods=["GET"])
    def files_browse():
        return {"ok": True}

    @test_app.route("/api/self-code/file", methods=["GET"])
    def self_code_file():
        return {"ok": True}

    @test_app.route("/api/memory/clear", methods=["DELETE"])
    def memory_clear():
        return {"ok": True}

    @test_app.route("/api/memory", methods=["GET"])
    def memory_list():
        return {"ok": True}

    @test_app.route("/api/meta/clear-pycache", methods=["POST"])
    def clear_pycache():
        return {"ok": True}

    @test_app.route("/api/meta/rebuild-index", methods=["POST"])
    def meta_mutation():
        return {"ok": True}

    @test_app.route("/api/meta/generation-history", methods=["GET", "DELETE"])
    def generation_history():
        return {"ok": True}

    @test_app.route("/api/audio-foundry/jobs", methods=["GET", "DELETE"])
    def audio_jobs():
        return {"ok": True}

    @test_app.route("/api/settings/profile", methods=["GET", "POST"])
    def settings_profile():
        return {"ok": True}

    @test_app.route("/api/connections", methods=["GET"])
    def connections_list():
        return {"ok": True}

    @test_app.route("/api/connections/publishes/<int:pid>/approve", methods=["POST"])
    def connections_approve(pid):
        return {"ok": True}

    @test_app.route("/api/connections/settings", methods=["GET", "POST"])
    def connections_settings():
        return {"ok": True}

    @test_app.route("/api/social-outreach/status", methods=["GET"])
    def outreach_status():
        return {"ok": True}

    @test_app.route("/api/wordpress/sites", methods=["GET", "POST"])
    def wordpress_sites():
        return {"ok": True}

    @test_app.route("/api/wordpress/pull/list", methods=["POST"])
    def wordpress_pull_list():
        return {"ok": True}

    @test_app.route("/api/wordpress/pages", methods=["GET"])
    def wordpress_pages():
        return {"ok": True}

    @test_app.route("/api/code-execution/python", methods=["POST"])
    def code_execution_python():
        return {"ok": True}

    @test_app.route("/api/tools/execute", methods=["POST"])
    def tools_execute():
        return {"ok": True}

    @test_app.route("/api/tools/execute_python", methods=["GET"])
    def tools_execute_python_schema():
        return {"ok": True}

    @test_app.route("/api/automation/mcp/execute", methods=["POST"])
    def automation_mcp_execute():
        return {"ok": True, "approved": auth_guard.caller_is_authorized()}

    @test_app.route("/api/code-execution/format", methods=["POST"])
    def code_execution_format():
        return {"ok": True}

    @test_app.route("/api/automation/mcp/servers/<name>", methods=["GET", "PUT", "DELETE"])
    def automation_mcp_server(name):
        return {"ok": True}

    @test_app.route("/api/automation/mcp/connect", methods=["POST"])
    def automation_mcp_connect():
        return {"ok": True}

    @test_app.route("/api/automation/mcp/reload-config", methods=["POST"])
    def automation_mcp_reload():
        return {"ok": True}

    @test_app.route("/api/self-improvement/pending-fixes/<int:fix_id>/apply", methods=["POST"])
    def pending_fix_apply(fix_id):
        return {"ok": True}

    @test_app.route("/api/self-improvement/pending-fixes/<int:fix_id>", methods=["GET"])
    def pending_fix_read(fix_id):
        return {"ok": True}

    @test_app.route("/api/tools/jobs/<job_id>", methods=["GET"])
    def tools_job(job_id):
        return {"ok": True}

    return test_app


CONNECTIONS_REQUESTS = [
    ("GET", "/api/connections"),
    ("POST", "/api/connections/publishes/7/approve"),
    ("POST", "/api/connections/settings"),
]


@pytest.mark.parametrize("method,path", CONNECTIONS_REQUESTS)
def test_connections_refuse_another_host_without_api_key(app, monkeypatch, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "192.168.1.20"})

    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


@pytest.mark.parametrize("method,path", CONNECTIONS_REQUESTS)
def test_connections_refuse_a_lan_device_through_the_local_proxy(app, monkeypatch, method, path):
    # The web UI reaches /api through the loopback Vite proxy, which forwards the
    # browser's address in X-Forwarded-For.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": "192.168.1.20"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


@pytest.mark.parametrize("method,path", CONNECTIONS_REQUESTS)
def test_connections_answer_this_machine_without_api_key(app, monkeypatch, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    direct = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    proxied = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert direct.status_code == 200
    assert proxied.status_code == 200


@pytest.mark.parametrize("method,path", CONNECTIONS_REQUESTS)
def test_connections_need_the_key_once_one_is_configured(app, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    client = app.test_client()

    def send(addr, **headers):
        return client.open(path, method=method, json={}, headers=headers, environ_base={"REMOTE_ADDR": addr})

    remote_without_key = send("192.168.1.20")
    local_without_key = send("127.0.0.1")
    wrong_key = send("192.168.1.20", **{"X-API-Key": "not-it"})

    assert remote_without_key.status_code == 401
    assert remote_without_key.get_json()["code"] == auth_guard.API_KEY_CODE
    assert local_without_key.status_code == 401
    assert wrong_key.status_code == 401
    assert wrong_key.get_json()["credential_rejected"] is True
    assert send("192.168.1.20", **{"X-API-Key": "secret"}).status_code == 200
    assert send("127.0.0.1", **{"X-API-Key": "secret"}).status_code == 200


@pytest.mark.parametrize("api_key", [None, "secret"])
@pytest.mark.parametrize("addr", ["192.168.1.20", "127.0.0.1"])
def test_connections_get_the_same_answer_as_social_outreach(app, monkeypatch, api_key, addr):
    if api_key:
        monkeypatch.setenv("GUAARDVARK_API_KEY", api_key)
    else:
        monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    outreach = client.get("/api/social-outreach/status", environ_base={"REMOTE_ADDR": addr})
    connections = client.get("/api/connections", environ_base={"REMOTE_ADDR": addr})

    assert connections.status_code == outreach.status_code
    assert connections.get_json() == outreach.get_json()


def test_every_connections_route_refuses_another_host_without_api_key(monkeypatch):
    """Each route the Connections blueprint serves, not only the sampled ones."""
    import re

    from backend.api.connections_api import connections_bp

    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    real_app = Flask(__name__)
    real_app.before_request(auth_guard.check_endpoint_auth)
    real_app.register_blueprint(connections_bp)
    client = real_app.test_client()

    checked = []
    for rule in real_app.url_map.iter_rules():
        if not rule.endpoint.startswith(f"{connections_bp.name}."):
            continue
        path = re.sub(r"<(?:\w+:)?\w+>", "1", rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            response = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
            assert response.status_code == 403, f"{method} {path}"
            checked.append((method, path))

    # Fewer would mean the blueprint lost routes or the walk above missed them.
    assert len(checked) >= 20


# WordPress sites hold each site's API key or Application Password, and pulling
# sends it to the site: the same rule as Connections.
WORDPRESS_REQUESTS = [
    ("GET", "/api/wordpress/sites"),
    ("POST", "/api/wordpress/sites"),
    ("POST", "/api/wordpress/pull/list"),
    ("GET", "/api/wordpress/pages"),
]


@pytest.mark.parametrize("method,path", WORDPRESS_REQUESTS)
def test_wordpress_refuses_another_host_without_api_key(app, monkeypatch, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    client = app.test_client()

    direct = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
    proxied = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": "192.168.1.20"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    for response in (direct, proxied):
        assert response.status_code == 403
        assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


@pytest.mark.parametrize("method,path", WORDPRESS_REQUESTS)
def test_wordpress_answers_this_machine_without_api_key(app, monkeypatch, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    client = app.test_client()

    direct = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    proxied = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert direct.status_code == 200
    assert proxied.status_code == 200


@pytest.mark.parametrize("method,path", WORDPRESS_REQUESTS)
def test_wordpress_needs_the_key_once_one_is_configured(app, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    client = app.test_client()

    def send(addr, **headers):
        return client.open(path, method=method, json={}, headers=headers, environ_base={"REMOTE_ADDR": addr})

    assert send("192.168.1.20").status_code == 401
    assert send("192.168.1.20").get_json()["code"] == auth_guard.API_KEY_CODE
    assert send("127.0.0.1").status_code == 401
    assert send("192.168.1.20", **{"X-API-Key": "not-it"}).status_code == 401
    assert send("192.168.1.20", **{"X-API-Key": "secret"}).status_code == 200
    assert send("127.0.0.1", **{"X-API-Key": "secret"}).status_code == 200


@pytest.mark.parametrize("network_access", [None, "1"])
@pytest.mark.parametrize("api_key", [None, "secret"])
@pytest.mark.parametrize("addr", ["192.168.1.20", "127.0.0.1", "8.8.8.8"])
def test_wordpress_gets_the_same_answer_as_connections(app, monkeypatch, network_access, api_key, addr):
    """Network access opens WordPress to the local network exactly as it opens
    the other protected routes, and an address outside it still needs the key."""
    for name, value in (("GUAARDVARK_NETWORK_ACCESS", network_access), ("GUAARDVARK_API_KEY", api_key)):
        if value:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    client = app.test_client()

    connections = client.get("/api/connections", environ_base={"REMOTE_ADDR": addr})
    wordpress = client.get("/api/wordpress/sites", environ_base={"REMOTE_ADDR": addr})

    assert wordpress.status_code == connections.status_code
    assert wordpress.get_json() == connections.get_json()


def test_every_wordpress_route_refuses_another_host_without_api_key(monkeypatch):
    """Each route the WordPress blueprint serves, not only the sampled ones."""
    import re

    from backend.api.wordpress_api import wordpress_bp

    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    real_app = Flask(__name__)
    real_app.before_request(auth_guard.check_endpoint_auth)
    real_app.register_blueprint(wordpress_bp)
    client = real_app.test_client()

    checked = []
    for rule in real_app.url_map.iter_rules():
        if not rule.endpoint.startswith(f"{wordpress_bp.name}."):
            continue
        path = re.sub(r"<(?:\w+:)?\w+>", "1", rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            response = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
            assert response.status_code == 403, f"{method} {path}"
            checked.append((method, path))

    # Fewer would mean the blueprint lost routes or the walk above missed them.
    assert len(checked) >= 17


def test_settings_access_lists_wordpress():
    assert any("WordPress" in item for item in auth_guard.protected_summary())


def test_profile_write_blocked_from_remote_host_but_readable(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()
    assert client.post("/api/settings/profile", json={"name": "creator"}, environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/api/settings/profile", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 200
    assert client.post("/api/settings/profile", json={"name": "creator"}, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


def test_delete_generation_history_blocked_from_remote_host(app, monkeypatch):
    # Deleting every generated image, video and audio file is not a "safe"
    # maintenance op like clear-pycache; it stays behind the /api/meta guard.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.delete("/api/meta/generation-history", environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 403

    # The counts stay readable so the LAN UI can show what would be deleted.
    response = client.get("/api/meta/generation-history", environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 200

    response = client.delete("/api/meta/generation-history", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200


def test_clear_audio_jobs_blocked_from_remote_host_but_listing_open(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    assert client.delete("/api/audio-foundry/jobs", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/api/audio-foundry/jobs", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 200
    assert client.delete("/api/audio-foundry/jobs", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


def test_clear_pycache_allowed_from_remote_host(app, monkeypatch):
    # Safe maintenance op: exempt from the host check even though /api/meta is
    # otherwise mutation-protected, so the operator can clear cache from the LAN UI.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/meta/clear-pycache",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_other_meta_mutation_still_blocked_from_remote_host(app, monkeypatch):
    # The carve-out must be narrow: other /api/meta mutations stay protected.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/meta/rebuild-index",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_lan_device_via_local_proxy_is_still_treated_as_remote(app, monkeypatch):
    # Request arrives from the loopback Vite proxy (REMOTE_ADDR=127.0.0.1) but
    # X-Forwarded-For carries the real LAN client → must still be blocked.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "192.168.1.20"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_forged_xff_from_direct_remote_peer_is_ignored(app, monkeypatch):
    # A LAN attacker connecting straight to the backend (non-loopback peer) cannot
    # spoof localhost by setting X-Forwarded-For — the header is only trusted from
    # a loopback peer (our own proxy).
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_forged_xff_through_the_proxy_is_ignored(app, monkeypatch):
    # A LAN device sends X-Forwarded-For: 127.0.0.1 to the UI port; the proxy
    # appends the address it really saw, and that last entry is the one trusted.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "127.0.0.1, 192.168.1.20"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_genuine_localhost_via_proxy_is_allowed(app, monkeypatch):
    # Operator on the box: browser → loopback proxy → backend, XFF also loopback.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_remote_file_mutation_is_blocked_without_api_key(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_remote_self_code_read_is_blocked_without_api_key(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.get(
        "/api/self-code/file",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_read_only_file_browser_get_remains_unprotected(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.get(
        "/api/files/browse",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_api_key_required_for_protected_file_endpoint(app, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")

    client = app.test_client()
    missing_key = client.post(
        "/api/files/write",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )
    with_key = client.post(
        "/api/files/write",
        headers={"X-API-Key": "secret"},
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert missing_key.status_code == 401
    assert with_key.status_code == 200


def test_remote_memory_clear_is_blocked_without_api_key(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.delete(
        "/api/memory/clear",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_remote_memory_read_remains_unprotected(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.get(
        "/api/memory",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


# Network access (Settings → Access) opens the protected routes to the local
# network, key or not, except the routes that run code a caller supplies.

RUNS_CODE_REQUESTS = [
    ("POST", "/api/code-execution/python"),
    ("POST", "/api/tools/execute"),
    ("POST", "/api/automation/mcp/execute"),
    ("PUT", "/api/automation/mcp/servers/evil"),
    ("DELETE", "/api/automation/mcp/servers/evil"),
    ("POST", "/api/automation/mcp/connect"),
    ("POST", "/api/automation/mcp/reload-config"),
    ("POST", "/api/self-improvement/pending-fixes/7/apply"),
]
# Protected routes network access still opens: formatting only runs a
# formatter over the text, and reading a server entry or a staged fix runs
# nothing.
OPENED_BY_NETWORK_ACCESS = [
    ("POST", "/api/files/write"),
    ("POST", "/api/code-execution/format"),
    ("GET", "/api/automation/mcp/servers/evil"),
    ("GET", "/api/self-improvement/pending-fixes/7"),
]
LAN_DEVICE = "192.168.1.20"


@pytest.fixture
def network_access_on(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")


@pytest.mark.parametrize("method,path", OPENED_BY_NETWORK_ACCESS)
def test_network_access_opens_a_protected_route_to_the_local_network(app, monkeypatch, network_access_on, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})

    assert response.status_code == 200


@pytest.mark.parametrize("method,path", RUNS_CODE_REQUESTS)
def test_this_machine_keeps_passing_under_network_access_once_a_key_is_set(app, monkeypatch, network_access_on, method, path):
    """A browser on the machine itself that never signed in: network access
    passed it before the code routes were set apart, and still does."""
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    client = app.test_client()

    direct = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    proxied = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert direct.status_code == 200
    assert proxied.status_code == 200


def test_this_machine_needs_the_key_on_code_routes_while_network_access_is_off(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    client = app.test_client()

    response = client.post("/api/code-execution/python", json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})

    assert response.status_code == 401


def test_the_tool_endpoint_opt_out_does_not_open_code_routes_to_other_hosts(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    monkeypatch.setenv("GUAARDVARK_PROTECT_TOOL_ENDPOINTS", "false")
    client = app.test_client()

    for method, path in (("POST", "/api/tools/execute"), ("POST", "/api/automation/mcp/execute"),
                         ("POST", "/api/automation/mcp/connect")):
        remote = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})
        assert remote.status_code == 403, f"{method} {path}"
        assert remote.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE
        assert client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    # The opt-out still opens what it opened before: tool jobs.
    assert client.get("/api/tools/jobs/j1", environ_base={"REMOTE_ADDR": LAN_DEVICE}).status_code == 200


@pytest.mark.parametrize("method,path", RUNS_CODE_REQUESTS)
def test_network_access_does_not_open_code_execution_to_a_lan_device(app, monkeypatch, network_access_on, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    direct = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})
    proxied = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": LAN_DEVICE},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    for response in (direct, proxied):
        assert response.status_code == 403
        assert response.get_json()["code"] == auth_guard.RUNS_CODE_CODE
        assert response.get_json()["error"] == auth_guard.RUNS_CODE_MESSAGE.format(machine=auth_guard.machine_name())


@pytest.mark.parametrize("method,path", RUNS_CODE_REQUESTS)
def test_code_execution_answers_this_machine_while_network_access_is_on(app, monkeypatch, network_access_on, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})

    assert response.status_code == 200


@pytest.mark.parametrize("method,path", RUNS_CODE_REQUESTS)
def test_code_execution_takes_the_key_from_a_lan_device_while_network_access_is_on(app, monkeypatch, network_access_on, method, path):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    client = app.test_client()

    def send(**headers):
        return client.open(path, method=method, json={}, headers=headers, environ_base={"REMOTE_ADDR": LAN_DEVICE})

    without_key = send()
    wrong_key = send(**{"X-API-Key": "not-it"})

    assert without_key.status_code == 401
    assert without_key.get_json()["code"] == auth_guard.API_KEY_CODE
    assert wrong_key.status_code == 401
    assert wrong_key.get_json()["credential_rejected"] is True
    assert send(**{"X-API-Key": "secret"}).status_code == 200
    # The other protected routes stay open to the device, as before.
    for other_method, other_path in OPENED_BY_NETWORK_ACCESS:
        assert client.open(other_path, method=other_method, json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE}).status_code == 200


def test_code_execution_takes_a_signed_in_browser_on_the_lan(app, monkeypatch, network_access_on):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    monkeypatch.setattr(auth_guard, "request_has_valid_session", lambda: True)
    client = app.test_client()

    response = client.post("/api/code-execution/python", json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})

    assert response.status_code == 200


def test_a_tool_schema_read_is_not_a_tool_call(app, monkeypatch, network_access_on):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.get("/api/tools/execute_python", environ_base={"REMOTE_ADDR": LAN_DEVICE})

    assert response.status_code == 200


def test_code_execution_outside_the_local_network_is_refused_the_same_way(app, monkeypatch, network_access_on):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.post("/api/code-execution/python", json={}, environ_base={"REMOTE_ADDR": "8.8.8.8"})

    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.RUNS_CODE_CODE


def test_caller_is_authorized_agrees_with_the_hook_on_code_routes(app, monkeypatch, network_access_on):
    """automation_api passes caller_is_authorized() as the approval for
    policy-gated MCP tools; it must say what the hook says."""
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    local = client.post("/api/automation/mcp/execute", json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert local.status_code == 200
    assert local.get_json()["approved"] is True

    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    with_key = client.post(
        "/api/automation/mcp/execute", json={},
        headers={"X-API-Key": "secret"},
        environ_base={"REMOTE_ADDR": LAN_DEVICE},
    )
    assert with_key.status_code == 200
    assert with_key.get_json()["approved"] is True


def test_network_access_off_keeps_the_local_only_refusal_for_code_routes(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.post("/api/code-execution/python", json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})

    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


# ---- Interconnector operator routes ---------------------------------------

# Every path below is an Interconnector operator route (auth_guard FLEET_*),
# with the method the route answers.
INTERCONNECTOR_OPERATOR_ROUTES = (
    ("GET", "/api/interconnector/config"),
    ("POST", "/api/interconnector/config"),
    ("POST", "/api/interconnector/config/generate-key"),
    ("GET", "/api/interconnector/approvals/pending"),
    ("POST", "/api/interconnector/approvals/7/decide"),
    ("POST", "/api/interconnector/broadcast/push"),
    ("GET", "/api/interconnector/broadcast/status/b-1"),
    ("POST", "/api/interconnector/sync/manual"),
    ("POST", "/api/interconnector/ask-family"),
    ("POST", "/api/interconnector/route-inference"),
    ("POST", "/api/interconnector/nodes/test-all"),
    ("POST", "/api/cluster/routing-table/recompute"),
    ("POST", "/api/interconnector/enrol"),
    ("GET", "/api/interconnector/enrolments"),
    ("GET", "/api/interconnector/nodes"),
    ("DELETE", "/api/interconnector/nodes/n-1"),
    ("POST", "/api/interconnector/nodes/n-1/test"),
    ("GET", "/api/interconnector/debug/nodes"),
    ("POST", "/api/interconnector/test-connection"),
    ("POST", "/api/interconnector/updates/apply"),
    ("POST", "/api/interconnector/sync/preferences"),
    ("GET", "/api/interconnector/sync/files/test"),
    ("POST", "/api/interconnector/sync/files/verify"),
    ("GET", "/api/interconnector/backups"),
    ("POST", "/api/interconnector/transition"),
)
INTERCONNECTOR_OPERATOR_PREFIXES = (
    "/api/interconnector/config",
    "/api/interconnector/approvals/",
    "/api/interconnector/broadcast/",
    "/api/interconnector/sync/manual",
    "/api/interconnector/ask-family",
    "/api/interconnector/route-inference",
    "/api/interconnector/nodes/test-all",
    "/api/cluster/routing-table/recompute",
    "/api/interconnector/enrol",
    "/api/interconnector/debug/",
    "/api/interconnector/test-connection",
    "/api/interconnector/updates/apply",
    "/api/interconnector/sync/files/test",
    "/api/interconnector/sync/files/verify",
    "/api/interconnector/backups",
    "/api/interconnector/transition",
)
# Routes other machines call. The guard leaves them to the route, which checks
# the calling machine itself; and reads that stay open.
INTERCONNECTOR_NODE_ROUTES = (
    ("GET", "/api/interconnector/status"),
    ("POST", "/api/interconnector/nodes/register"),
    ("POST", "/api/interconnector/nodes/n-1/heartbeat"),
    ("GET", "/api/interconnector/nodes/local-identity"),
    ("GET", "/api/interconnector/sync/pull"),
    ("GET", "/api/interconnector/sync/files/pull"),
    ("GET", "/api/interconnector/updates/manifest"),
    ("GET", "/api/interconnector/sync/history"),
    ("GET", "/api/interconnector/sync/preferences"),
)
OTHER_MACHINE = {"REMOTE_ADDR": "192.0.2.44"}
THIS_MACHINE = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def interconnector_app(monkeypatch):
    # This machine is loopback only, whatever the test box's own address is.
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    test_app = Flask(__name__)
    test_app.before_request(auth_guard.check_endpoint_auth)
    routes = INTERCONNECTOR_OPERATOR_ROUTES + INTERCONNECTOR_NODE_ROUTES
    for index, (method, path) in enumerate(routes):
        test_app.add_url_rule(path, f"route_{index}", lambda **_kw: {"ok": True}, methods=[method])
    return test_app


def _call(client, method, path, environ, headers=None):
    return client.open(path, method=method, environ_base=environ, headers=headers or {})


def test_every_interconnector_operator_prefix_is_a_fleet_route():
    for prefix in INTERCONNECTOR_OPERATOR_PREFIXES:
        assert prefix in auth_guard.FLEET_PREFIXES, prefix


@pytest.mark.parametrize("method,path,fleet", [
    ("GET", "/api/interconnector/nodes", True),
    ("GET", "/api/interconnector/nodes/", True),
    ("DELETE", "/api/interconnector/nodes/n-1", True),
    ("POST", "/api/interconnector/nodes/n-1/test", True),
    ("POST", "/api/interconnector/sync/preferences", True),
    ("GET", "/api/interconnector/sync/preferences", False),
    ("POST", "/api/interconnector/nodes/register", False),
    ("POST", "/api/interconnector/nodes/n-1/heartbeat", False),
    ("GET", "/api/interconnector/nodes/local-identity", False),
    ("GET", "/api/interconnector/status", False),
    ("GET", "/api/interconnector/updates/manifest", False),
    ("POST", "/api/interconnector/receive-directive", False),
])
def test_which_requests_control_the_fleet(method, path, fleet):
    with Flask(__name__).test_request_context(path, method=method):
        assert auth_guard.request_controls_fleet() is fleet


@pytest.mark.parametrize("method,path", INTERCONNECTOR_OPERATOR_ROUTES)
def test_interconnector_operator_route_refuses_another_machine_without_a_key(interconnector_app, method, path):
    response = _call(interconnector_app.test_client(), method, path, OTHER_MACHINE)
    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


@pytest.mark.parametrize("method,path", INTERCONNECTOR_OPERATOR_ROUTES)
def test_interconnector_operator_route_refuses_a_lan_device_behind_the_local_proxy(interconnector_app, method, path):
    response = _call(interconnector_app.test_client(), method, path, THIS_MACHINE,
                     headers={"X-Forwarded-For": "192.0.2.44"})
    assert response.status_code == 403


@pytest.mark.parametrize("method,path", INTERCONNECTOR_OPERATOR_ROUTES)
def test_interconnector_operator_route_answers_this_machine(interconnector_app, method, path):
    assert _call(interconnector_app.test_client(), method, path, THIS_MACHINE).status_code == 200


@pytest.mark.parametrize("method,path", INTERCONNECTOR_OPERATOR_ROUTES)
def test_interconnector_operator_route_takes_the_install_key_from_any_host(interconnector_app, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "install-key")
    client = interconnector_app.test_client()
    assert _call(client, method, path, OTHER_MACHINE).status_code == 401
    assert _call(client, method, path, OTHER_MACHINE, headers={"X-API-Key": "wrong"}).status_code == 401
    assert _call(client, method, path, OTHER_MACHINE, headers={"X-API-Key": "install-key"}).status_code == 200
    # Once a key is set it is asked of this machine too.
    assert _call(client, method, path, THIS_MACHINE).status_code == 401


@pytest.mark.parametrize("method,path", INTERCONNECTOR_NODE_ROUTES)
def test_interconnector_node_routes_are_left_to_the_route(interconnector_app, method, path):
    assert _call(interconnector_app.test_client(), method, path, OTHER_MACHINE).status_code == 200


# Network access opens protected routes to the local network, but the
# Interconnector's operator routes stay with this machine and the key.

@pytest.mark.parametrize("method,path", INTERCONNECTOR_OPERATOR_ROUTES)
def test_network_access_does_not_open_a_fleet_route_to_a_lan_device(interconnector_app, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")
    client = interconnector_app.test_client()

    direct = _call(client, method, path, {"REMOTE_ADDR": LAN_DEVICE})
    proxied = _call(client, method, path, THIS_MACHINE, headers={"X-Forwarded-For": LAN_DEVICE})

    for response in (direct, proxied):
        assert response.status_code == 403
        assert response.get_json()["code"] == auth_guard.FLEET_CODE
        assert response.get_json()["error"] == auth_guard.FLEET_MESSAGE.format(machine=auth_guard.machine_name())


@pytest.mark.parametrize("method,path", INTERCONNECTOR_OPERATOR_ROUTES)
def test_a_fleet_route_answers_this_machine_while_network_access_is_on(interconnector_app, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")
    assert _call(interconnector_app.test_client(), method, path, THIS_MACHINE).status_code == 200


@pytest.mark.parametrize("method,path", INTERCONNECTOR_OPERATOR_ROUTES)
def test_a_fleet_route_takes_the_key_from_a_lan_device_while_network_access_is_on(interconnector_app, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")
    monkeypatch.setenv("GUAARDVARK_API_KEY", "install-key")
    client = interconnector_app.test_client()
    lan = {"REMOTE_ADDR": LAN_DEVICE}

    without_key = _call(client, method, path, lan)
    assert without_key.status_code == 401
    assert without_key.get_json()["code"] == auth_guard.API_KEY_CODE
    assert _call(client, method, path, lan, headers={"X-API-Key": "install-key"}).status_code == 200


def test_a_fleet_route_takes_a_signed_in_browser_on_the_lan(interconnector_app, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")
    monkeypatch.setenv("GUAARDVARK_API_KEY", "install-key")
    monkeypatch.setattr(auth_guard, "request_has_valid_session", lambda: True)
    response = _call(interconnector_app.test_client(), "GET", "/api/interconnector/config", {"REMOTE_ADDR": LAN_DEVICE})
    assert response.status_code == 200


@pytest.mark.parametrize("method,path", INTERCONNECTOR_NODE_ROUTES)
def test_network_access_leaves_the_node_routes_to_the_route(interconnector_app, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")
    assert _call(interconnector_app.test_client(), method, path, {"REMOTE_ADDR": LAN_DEVICE}).status_code == 200


def test_network_access_still_opens_other_protected_routes_beside_the_fleet(app, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    response = app.test_client().post("/api/files/write", json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})
    assert response.status_code == 200


BACKUP_ROUTES = [
    ("GET", "/api/backups"),
    ("GET", "/api/backups/a.zip/download"),
    ("POST", "/api/backups/create"),
    ("POST", "/api/backups/restore"),
    ("DELETE", "/api/backups/a.zip"),
    ("POST", "/api/meta/backup/export"),
]
LAN_ADDR = {"REMOTE_ADDR": "192.168.1.20"}
LOCAL_ADDR = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def backup_app():
    test_app = Flask(__name__)
    test_app.before_request(auth_guard.check_endpoint_auth)
    for i, (method, rule) in enumerate(BACKUP_ROUTES):
        test_app.add_url_rule(rule, f"backup_{i}", lambda **_: {"ok": True}, methods=[method])
    return test_app


@pytest.mark.parametrize("method,path", BACKUP_ROUTES)
def test_backups_stay_closed_to_a_lan_device_under_network_access(backup_app, monkeypatch, method, path):
    monkeypatch.setenv(auth_guard.NETWORK_ACCESS_ENV, "1")
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    response = backup_app.test_client().open(path, method=method, json={}, environ_base=LAN_ADDR)
    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.BACKUPS_CODE


@pytest.mark.parametrize("method,path", BACKUP_ROUTES)
def test_backups_stay_closed_to_a_lan_device_through_the_local_proxy(backup_app, monkeypatch, method, path):
    monkeypatch.setenv(auth_guard.NETWORK_ACCESS_ENV, "1")
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    response = backup_app.test_client().open(
        path, method=method, json={}, headers={"X-Forwarded-For": "192.168.1.20"}, environ_base=LOCAL_ADDR,
    )
    assert response.status_code == 403


@pytest.mark.parametrize("method,path", BACKUP_ROUTES)
def test_backups_refuse_another_host_with_network_access_off(backup_app, monkeypatch, method, path):
    monkeypatch.delenv(auth_guard.NETWORK_ACCESS_ENV, raising=False)
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    response = backup_app.test_client().open(path, method=method, json={}, environ_base=LAN_ADDR)
    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


@pytest.mark.parametrize("network_access", [None, "1"])
@pytest.mark.parametrize("method,path", BACKUP_ROUTES)
def test_backups_answer_this_machine(backup_app, monkeypatch, network_access, method, path):
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    if network_access:
        monkeypatch.setenv(auth_guard.NETWORK_ACCESS_ENV, network_access)
    else:
        monkeypatch.delenv(auth_guard.NETWORK_ACCESS_ENV, raising=False)
    response = backup_app.test_client().open(path, method=method, json={}, environ_base=LOCAL_ADDR)
    assert response.status_code == 200


@pytest.mark.parametrize("method,path", BACKUP_ROUTES)
def test_backups_take_the_key_from_a_lan_device(backup_app, monkeypatch, method, path):
    monkeypatch.setenv(auth_guard.NETWORK_ACCESS_ENV, "1")
    monkeypatch.setenv(auth_guard.API_KEY_ENV, "install-key")
    client = backup_app.test_client()
    without_key = client.open(path, method=method, json={}, environ_base=LAN_ADDR)
    assert without_key.status_code == 401
    assert without_key.get_json()["code"] == auth_guard.API_KEY_CODE
    with_key = client.open(path, method=method, json={}, headers={"X-API-Key": "install-key"}, environ_base=LAN_ADDR)
    assert with_key.status_code == 200


def test_settings_access_lists_downloading_backups():
    assert any("downloading" in item and "backups" in item for item in auth_guard.protected_summary())
