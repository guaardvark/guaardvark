"""Every HTTP server Guaardvark starts answers only requests addressed to one
of this machine's names (the backend's rule, backend/utils/host_check.py),
and no plugin reply carries a credential.

A page whose DNS name was re-pointed at 127.0.0.1 (DNS rebinding) is
same-origin with itself, so CORS and Origin checks let it through; its Host
header names its own site. The plugin servers, ComfyUI, the MCP server's HTTP
transport and the reboot log server all apply the rule through the adapters
here. Probed with each framework's own test client and literal Host headers;
no plugin, GPU or network.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from backend.utils import cors_policy, host_check, sidecar_guard
from backend.utils.host_check import HOST_CODE, HostCheckASGIMiddleware, aiohttp_middleware

ROOT = Path(__file__).resolve().parents[2]
MACHINE = frozenset({"localhost", "127.0.0.1", "::1", "192.168.1.20", "workstation", "workstation.local"})
ENV_NAMES = (
    "VITE_PORT", "FLASK_PORT", "PORT", "VITE_FRONTEND_URL", "VITE_ALLOWED_HOSTS",
    "VITE_API_BASE_URL", "VITE_SOCKET_URL", cors_policy.EXTRA_ORIGINS_ENV,
)
ANSWERED = ["127.0.0.1:8206", "localhost:8188", "[::1]:8202", "192.168.1.20:8207", "workstation.local:8210"]
REFUSED = ["evil.example:8206", "localhost.evil.example:8188", "127.0.0.1.nip.io:8202"]


@pytest.fixture(autouse=True)
def machine(monkeypatch):
    monkeypatch.setattr(cors_policy, "_machine_hosts", lambda: MACHINE)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


# ---- the shared rule loads without the backend package ----------------------

def test_plugin_servers_load_the_rule_without_the_backend_package(tmp_path):
    # A plugin server has only its own folder on sys.path and must not start
    # the backend's Socket.IO or LLM stack; sys.modules["backend"] = None
    # makes any import of it fail.
    code = f"""
import importlib.util, sys
sys.modules["backend"] = None
spec = importlib.util.spec_from_file_location("guaardvark_sidecar_guard", {str(ROOT / "backend/utils/sidecar_guard.py")!r})
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
assert guard.host_allowed("localhost:8188") and guard.host_allowed("10.0.0.7:8206")
assert not guard.host_allowed("evil.example:8188")
assert guard.HostCheckASGIMiddleware and guard.aiohttp_middleware and guard.HostCheckMiddleware
print(sorted(m for m in sys.modules if m.startswith("backend")))
"""
    env = {k: v for k, v in os.environ.items() if k not in ENV_NAMES}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['backend']"


def _module_level_imports(tree):
    """Top-level names imported when the module loads (functions' own imports,
    such as aiohttp's or psutil's, run only when called)."""
    names = set()
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add(node.module.split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            pending.extend(node.body + node.orelse + getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                pending.extend(handler.body)
    return names


def test_the_rule_files_import_only_the_standard_library_and_each_other():
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    for name in ("sidecar_guard", "host_check", "cors_policy"):
        tree = ast.parse((ROOT / "backend/utils" / f"{name}.py").read_text())
        imported = _module_level_imports(tree)
        assert imported <= allowed, f"{name}.py imports {sorted(imported - allowed)}"


# ---- ASGI (FastAPI / Starlette / uvicorn) -----------------------------------

def _asgi_client():
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route, WebSocketRoute
    from starlette.testclient import TestClient

    ran = []

    async def thing(request):
        ran.append(request.method)
        return JSONResponse({"ok": True})

    async def socket(websocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    app = Starlette(routes=[Route("/thing", thing, methods=["GET", "POST"]), WebSocketRoute("/ws", socket)])
    app.add_middleware(HostCheckASGIMiddleware)
    return TestClient(app, base_url="http://127.0.0.1:8206"), ran


@pytest.mark.parametrize("host", ANSWERED)
def test_asgi_answers_this_machine(host):
    client, ran = _asgi_client()
    assert client.post("/thing", headers={"Host": host}).status_code == 200
    assert ran == ["POST"]


@pytest.mark.parametrize("host", REFUSED)
def test_asgi_refuses_a_rebound_page_before_any_route(host):
    client, ran = _asgi_client()
    response = client.post("/thing", headers={"Host": host, "Origin": f"http://{host}"})
    assert response.status_code == 421
    assert response.json()["code"] == HOST_CODE
    assert cors_policy.EXTRA_ORIGINS_ENV in response.json()["error"]
    assert ran == []


def test_asgi_closes_a_rebound_websocket_and_opens_this_machines():
    from starlette.websockets import WebSocketDisconnect

    client, _ = _asgi_client()
    with client.websocket_connect("/ws", headers={"Host": "localhost:8188"}) as ws:
        assert ws.receive_text() == "hello"
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws", headers={"Host": "evil.example:8188"}):
            pass
    assert refused.value.code == 1008


def test_asgi_passes_lifespan_through():
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    events = []

    @contextlib.asynccontextmanager
    async def lifespan(app):
        events.append("start")
        yield
        events.append("stop")

    app = Starlette(lifespan=lifespan)
    app.add_middleware(HostCheckASGIMiddleware)
    with TestClient(app, base_url="http://127.0.0.1"):
        pass
    assert events == ["start", "stop"]


# ---- aiohttp (ComfyUI, the Discord bot's health port) -----------------------

def _aiohttp_replies(app, *calls):
    """(status, body) for each (host, path, method), from one test server."""
    from aiohttp.test_utils import TestClient, TestServer

    async def run():
        replies = []
        async with TestClient(TestServer(app)) as client:
            for host, path, method in calls:
                response = await client.request(method, path, headers={"Host": host})
                replies.append((response.status, await response.text()))
        return replies

    return asyncio.run(run())


def _aiohttp_status(app, host, path="/thing", method="GET"):
    return _aiohttp_replies(app, (host, path, method))[0]


def _aiohttp_app(middleware):
    from aiohttp import web

    ran = []

    async def thing(request):
        ran.append(request.method)
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[middleware])
    app.router.add_route("*", "/thing", thing)
    return app, ran


@pytest.mark.parametrize("host", ANSWERED)
def test_aiohttp_answers_this_machine(host):
    app, ran = _aiohttp_app(aiohttp_middleware())
    assert _aiohttp_status(app, host, method="POST")[0] == 200
    assert ran == ["POST"]


@pytest.mark.parametrize("host", REFUSED)
def test_aiohttp_refuses_a_rebound_page_before_any_route(host):
    app, ran = _aiohttp_app(aiohttp_middleware())
    status, body = _aiohttp_status(app, host, method="POST")
    assert status == 421 and json.loads(body)["code"] == HOST_CODE
    assert ran == []


# ---- ComfyUI: the node Guaardvark's launchers load ---------------------------

NODE = ROOT / "plugins/comfyui/guaardvark_nodes/guaardvark_host_check.py"


@pytest.fixture
def comfyui_app(monkeypatch):
    """A stand-in for ComfyUI's PromptServer: an aiohttp app that already
    has middlewares, as ComfyUI's has (cache control, its origin check)."""
    from aiohttp import web

    seen = []

    @web.middleware
    async def comfyui_own(request, handler):
        seen.append(request.path)
        return await handler(request)

    async def view(request):
        return web.Response(text="image bytes")

    app = web.Application(middlewares=[comfyui_own])
    app.router.add_get("/view", view)
    server = SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(app=app)))
    monkeypatch.setitem(sys.modules, "server", server)
    for name in [m for m in sys.modules if m.startswith(("guaardvark_sidecar_guard", "guaardvark_backend_utils"))]:
        monkeypatch.delitem(sys.modules, name)
    yield app, seen
    for name in [m for m in sys.modules if m.startswith(("guaardvark_sidecar_guard", "guaardvark_backend_utils"))]:
        sys.modules.pop(name, None)


def _load_node():
    spec = importlib.util.spec_from_file_location("guaardvark_host_check_node", NODE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_comfyui_loads_the_node_from_guaardvarks_model_paths_file():
    config = (ROOT / "plugins/comfyui/guaardvark_model_paths.yaml").read_text()
    assert re.search(r"^guaardvark_nodes:\n\s+custom_nodes: guaardvark_nodes$", config, re.MULTILINE)
    assert NODE.parent.name == "guaardvark_nodes" and NODE.is_file()
    assert "ComfyUI" not in NODE.relative_to(ROOT / "plugins/comfyui").parts


def test_the_node_puts_the_host_check_first(comfyui_app):
    app, seen = comfyui_app
    module = _load_node()
    assert module.NODE_CLASS_MAPPINGS == {}
    assert len(app.middlewares) == 2 and app.middlewares[0].__name__ == "host_check"
    # The node loads its own copy of the rule, so these hosts do not depend
    # on this machine's names.
    answered, refused = _aiohttp_replies(
        app, ("localhost:8188", "/view", "GET"), ("evil.example:8188", "/view", "GET"),
    )
    assert answered == (200, "image bytes")
    assert refused[0] == 421 and "image bytes" not in refused[1]
    assert seen == ["/view"]


def test_without_the_rule_comfyui_refuses_everything(comfyui_app, monkeypatch):
    app, seen = comfyui_app
    real_spec = importlib.util.spec_from_file_location

    def missing_guard(name, location, *args, **kwargs):
        if name == "guaardvark_sidecar_guard":
            raise FileNotFoundError(location)
        return real_spec(name, location, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "spec_from_file_location", missing_guard)
    _load_node()
    status, body = _aiohttp_status(app, "localhost:8188", "/view")
    assert status == 503 and json.loads(body)["code"] == "host_check_unavailable"
    assert seen == []


# ---- WSGI (the GPU embedding service) -----------------------------------------

def test_wsgi_adapter_is_the_backends():
    assert sidecar_guard.HostCheckMiddleware is host_check.HostCheckMiddleware
    app = Flask(__name__)
    app.add_url_rule("/health", "health", lambda: {"ok": True})
    app.wsgi_app = sidecar_guard.HostCheckMiddleware(app.wsgi_app)
    client = app.test_client()
    assert client.get("/health", headers={"Host": "localhost:8204"}).status_code == 200
    assert client.get("/health", headers={"Host": "evil.example:8204"}).status_code == 421


# ---- the MCP server's HTTP transport -------------------------------------------

def test_mcp_http_transport_is_behind_the_host_check():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from backend.mcp.server import http_app

    inner = Starlette(routes=[Route("/mcp", lambda request: PlainTextResponse("mcp"), methods=["GET", "POST"])])
    server = SimpleNamespace(streamable_http_app=lambda host: inner)
    client = TestClient(http_app(server, "0.0.0.0"), base_url="http://127.0.0.1:8788")
    assert client.post("/mcp").text == "mcp"
    assert client.post("/mcp", headers={"Host": "evil.example:8788"}).status_code == 421


# ---- every Guaardvark server is wired, and on loopback by default ------------

# server file, how it applies the rule, the file that loads sidecar_guard.py
SERVERS = [
    ("plugins/audio_foundry/service/app.py", "app.add_middleware(_load_guard().HostCheckASGIMiddleware)", None),
    ("plugins/upscaling/service/app.py", "app.add_middleware(guard.HostCheckASGIMiddleware)",
     "plugins/upscaling/service/auth.py"),
    ("plugins/swarm/service/app.py", "app.add_middleware(_load_guard().HostCheckASGIMiddleware)", None),
    ("plugins/video_editor/service/app.py", "app.add_middleware(_load_guard().HostCheckASGIMiddleware)", None),
    ("plugins/vision_pipeline/service/app.py", "app.add_middleware(_guard.HostCheckASGIMiddleware)", None),
    ("plugins/gpu_embedding/service/app.py", "app.wsgi_app = _load_guard().HostCheckMiddleware(app.wsgi_app)", None),
    ("plugins/discord/bot.py", "web.Application(middlewares=[_load_guard().aiohttp_middleware()])", None),
]


@pytest.mark.parametrize("path, wiring, loader", SERVERS)
def test_every_plugin_server_applies_the_rule(path, wiring, loader):
    assert wiring in (ROOT / path).read_text()
    loader = loader or path
    depth = len(Path(loader).parts) - 1
    assert f'parents[{depth}] / "backend" / "utils" / "sidecar_guard.py"' in (ROOT / loader).read_text()
    assert ((ROOT / loader).resolve().parents[depth] / "backend/utils/sidecar_guard.py").is_file()


def test_a_fastapi_host_check_is_added_after_cors_so_it_runs_first():
    for path in ("plugins/upscaling/service/app.py", "plugins/vision_pipeline/service/app.py",
                 "plugins/swarm/service/app.py"):
        source = (ROOT / path).read_text()
        assert source.index("HostCheckASGIMiddleware)") > source.index("CORSMiddleware,")


def test_no_plugin_server_listens_on_every_interface_by_default():
    starts = sorted((ROOT / "plugins").glob("*/scripts/start.sh"))
    assert starts
    for start in starts:
        text = start.read_text()
        assert not re.search(r"--host\s+[\"']?0\.0\.0\.0", text), start
        assert not re.search(r"--listen\s+[\"']?0\.0\.0\.0", text), start
    bot = (ROOT / "plugins/discord/bot.py").read_text()
    assert '"0.0.0.0"' not in bot and 'os.environ.get("DISCORD_HEALTH_HOST", "127.0.0.1")' in bot
    for start, variable in (("vision_pipeline", "GUAARDVARK_VISION_PIPELINE_HOST"),
                            ("video_editor", "GUAARDVARK_VIDEO_EDITOR_HOST")):
        text = (ROOT / "plugins" / start / "scripts/start.sh").read_text()
        assert f'BIND_HOST="${{{variable}:-127.0.0.1}}"' in text
        assert '--host "$BIND_HOST"' in text


def test_the_web_terminal_listens_on_loopback_and_checks_origin():
    text = (ROOT / "scripts/terminal_server.sh").read_text()
    assert 'TERMINAL_INTERFACE="${GUAARDVARK_TERMINAL_INTERFACE:-127.0.0.1}"' in text
    assert '--interface "$TERMINAL_INTERFACE"' in text and "--interface 0.0.0.0" not in text
    assert "--check-origin" in text
    assert '(umask 077 && echo "${user}:${pass}" > "$AUTH_FILE")' in text


def test_docker_publishes_every_port_on_loopback_only():
    import yaml

    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    expected = {
        "postgres": ("POSTGRES", 5432),
        "redis": ("REDIS", 6379),
        "ollama": ("OLLAMA", 11434),
        "backend": ("BACKEND", 5000),
        "frontend": ("FRONTEND", 5173),
    }
    # Docker's port rules bypass UFW, so nothing is published past this host
    # unless *_PUBLISH_HOST says so; *_PUBLISH_PORT moves the port on this host.
    for name, (var, port) in expected.items():
        assert services[name]["ports"] == [
            f"${{GUAARDVARK_{var}_PUBLISH_HOST:-127.0.0.1}}:${{GUAARDVARK_{var}_PUBLISH_PORT:-{port}}}:{port}"
        ]
    # Browsers use the Web UI's port on this host as their origin.
    assert services["backend"]["environment"]["VITE_PORT"] == "${GUAARDVARK_FRONTEND_PUBLISH_PORT:-5173}"


def test_docker_database_and_queue_take_the_install_passwords():
    import yaml

    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    pg, redis = "${GUAARDVARK_POSTGRES_PASSWORD:-guaardvark}", "${GUAARDVARK_REDIS_PASSWORD:-guaardvark}"
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == pg
    assert services["redis"]["environment"]["REDIS_PASSWORD"] == redis
    assert "--requirepass" in " ".join(services["redis"]["command"])
    backend = services["backend"]["environment"]
    assert backend["DATABASE_URL"] == f"postgresql://guaardvark:{pg}@postgres:5432/guaardvark"
    for key in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        assert backend[key] == f"redis://:{redis}@redis:6379/0"
    start = (ROOT / "start-docker.sh").read_text()
    # A password is made for PostgreSQL only while no database volume exists.
    assert "com.docker.compose.volume=pgdata" in start
    assert "write_env_value GUAARDVARK_REDIS_PASSWORD" in start
    assert "write_env_value GUAARDVARK_POSTGRES_PASSWORD" in start


def test_no_plugin_lets_any_page_read_its_replies():
    for app in sorted((ROOT / "plugins").glob("*/service/app.py")):
        source = app.read_text()
        assert not re.search(r"allow_origins\s*=\s*\[\s*[\"']\*[\"']", source), app


# ---- credentials stay out of replies ------------------------------------------

def _code_words(path, function):
    """Names, attributes and string constants used by `function`, docstring left out."""
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    words = set()
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name):
                words.add(n.id)
            elif isinstance(n, ast.Attribute):
                words.add(n.attr)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                words.add(n.value)
    return words


def test_health_replies_carry_no_token():
    for path, function in (
        ("plugins/upscaling/service/app.py", "health"),
        ("plugins/upscaling/service/health.py", "get_health_status"),
        ("plugins/vision_pipeline/service/app.py", "health"),
    ):
        words = _code_words(path, function)
        assert words, (path, function)
        assert not [w for w in words if "token" in w.lower()], (path, function)


@pytest.fixture
def token_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sidecar_guard, "DATA_DIR", tmp_path)
    return tmp_path


def test_the_plugin_creates_its_token_readable_by_this_user_only(token_dir):
    assert sidecar_guard.read_internal_token("upscaling") == ""
    token = sidecar_guard.internal_token("upscaling")
    path = token_dir / ".upscaling_internal_secret"
    assert len(token) >= 40 and path.read_text() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sidecar_guard.internal_token("upscaling") == token
    assert sidecar_guard.read_internal_token("upscaling") == token
    assert [p.name for p in token_dir.iterdir()] == [path.name]
    assert sidecar_guard.bearer_matches("upscaling", f"Bearer {token}")
    assert not sidecar_guard.bearer_matches("upscaling", f"Bearer {token}x")
    assert not sidecar_guard.bearer_matches("upscaling", token)
    assert not sidecar_guard.bearer_matches("upscaling", None)
    with pytest.raises(ValueError):
        sidecar_guard.token_path("../escape")


def test_the_token_gate_opens_only_health_without_the_token(token_dir):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def ok(request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route(p, ok, methods=["GET", "POST"]) for p in ("/health", "/jobs", "/camera/start")])
    app.add_middleware(sidecar_guard.BearerTokenASGIMiddleware, name="upscaling")
    client = TestClient(app, base_url="http://127.0.0.1:8202")
    token = sidecar_guard.internal_token("upscaling")
    good = {"Authorization": f"Bearer {token}"}

    assert client.get("/health").status_code == 200
    assert client.post("/health").status_code == 401
    for path in ("/jobs", "/camera/start"):
        for method in ("GET", "POST"):
            refused = client.request(method, path)
            assert refused.status_code == 401 and refused.headers["www-authenticate"] == "Bearer"
            assert client.request(method, path, headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert client.request(method, path, headers=good).status_code == 200


@pytest.mark.parametrize("path, wiring", [
    ("plugins/upscaling/service/app.py", "app.add_middleware(guard.BearerTokenASGIMiddleware, name=TOKEN_NAME)"),
    ("plugins/vision_pipeline/service/app.py", "app.add_middleware(_guard.BearerTokenASGIMiddleware, name=TOKEN_NAME)"),
])
def test_upscaling_and_vision_gate_every_route_behind_the_host_check(path, wiring):
    source = (ROOT / path).read_text()
    assert source.index("CORSMiddleware,") < source.index(wiring) < source.index("HostCheckASGIMiddleware)")


def test_every_backend_call_to_vision_or_upscaling_sends_the_token():
    # /health is the one route that does not need it (liveness probes).
    callers = {
        "backend/utils/vision_context_utils.py": ("{VISION_PIPELINE_URL}/context", "{VISION_PIPELINE_URL}/frame/latest",
                                                  "{VISION_PIPELINE_URL}/analyze"),
        "backend/api/plugins_api.py": ("{VISION_PIPELINE_URL}/camera/start", "{VISION_PIPELINE_URL}/camera/stop",
                                       "{VISION_PIPELINE_URL}/camera/status"),
        "backend/services/gpu_resource_coordinator.py": ("localhost:8201/gpu/contention",),
        "backend/services/offline_image_generator.py": ("localhost:8201/gpu/contention",),
    }
    for path, urls in callers.items():
        source = (ROOT / path).read_text()
        for url in urls:
            assert source.count(f'{url}"') == 1, (path, url)
            call = source[source.index(f'{url}"'):][:200]
            assert "vision_pipeline_headers()" in call or "_vision_headers()" in call, (path, url)
    upscaling = (ROOT / "backend/api/upscaling_api.py").read_text()
    calls = [upscaling[m.start():m.start() + 250] for m in re.finditer(r'f"\{UPSCALING_URL\}', upscaling)]
    assert len(calls) >= 6
    for call in calls:
        assert "headers=_auth_headers()" in call or call.startswith('f"{UPSCALING_URL}/health"'), call


def test_the_backend_reads_tokens_from_their_files_not_from_a_reply(token_dir, monkeypatch):
    from backend.api import upscaling_api
    from backend.utils import vision_context_utils

    def no_handshake(url, *args, **kwargs):
        raise AssertionError(f"asked {url} for a token")

    monkeypatch.setattr(upscaling_api.requests, "get", no_handshake)
    monkeypatch.setattr(vision_context_utils.requests, "get", no_handshake)
    assert upscaling_api._auth_headers() == {} and vision_context_utils.vision_pipeline_headers() == {}
    up = sidecar_guard.internal_token("upscaling")
    vision = sidecar_guard.internal_token("vision_pipeline")
    assert upscaling_api._auth_headers() == {"Authorization": f"Bearer {up}"}
    assert vision_context_utils.vision_pipeline_headers() == {"Authorization": f"Bearer {vision}"}


def test_a_token_in_a_plugins_health_reply_is_not_relayed(monkeypatch):
    from backend.api import upscaling_api
    from backend.plugins.plugin_manager import PluginManager

    leaked = "Zm9vYmFyYmF6cXV4" * 2
    reply = SimpleNamespace(status_code=200, json=lambda: {"status": "healthy", "auth_token": leaked, "token": leaked})

    monkeypatch.setattr(upscaling_api.requests, "get", lambda url, **kw: reply)
    app = Flask(__name__)
    app.register_blueprint(upscaling_api.upscaling_bp)
    response = app.test_client().get("/api/upscaling/health")
    assert response.status_code == 200 and leaked not in response.get_data(as_text=True)
    assert response.get_json()["data"]["status"] == "healthy"

    import backend.plugins.plugin_manager as pm

    monkeypatch.setattr(pm.requests, "get", lambda url, timeout=None: reply)
    manager = SimpleNamespace(
        registry=SimpleNamespace(get_plugin=lambda pid: SimpleNamespace(
            config=SimpleNamespace(enabled=True, service_url="http://localhost:8201"),
            type="service", endpoints={"health": "/health"}, port=8201,
        )),
        _record_health=lambda pid, answering: None,
    )
    health = PluginManager.health_check(manager, "vision_pipeline")
    assert leaked not in repr(health)
    assert health["status"] == "healthy" and health["plugin_id"] == "vision_pipeline"
