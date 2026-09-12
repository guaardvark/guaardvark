"""backend_http against a real local HTTP server: no backend, database or GPU."""

import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.utils.backend_http import BackendError, request_json, run_tool_in_backend


class _Server(ThreadingHTTPServer):
    daemon_threads = True


@pytest.fixture
def backend(monkeypatch):
    """A local server. Tests set ``routes[path] = (status, body, delay_s)``;
    every request lands in ``requests`` as (method, path, headers, body)."""
    routes: dict = {}
    requests: list = []

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            requests.append((self.command, self.path, dict(self.headers), body))
            status, payload, delay = routes.get(self.path.split("?")[0], (404, {"error": "no route"}, 0))
            if delay:
                time.sleep(delay)
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = _serve
        do_POST = _serve

        def log_message(self, *_args):
            pass

    server = _Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("GUAARDVARK_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    yield routes, requests
    server.shutdown()
    server.server_close()


def test_envelope_data_is_unwrapped_and_the_body_kept(backend):
    routes, _ = backend
    routes["/api/x"] = (200, {"success": True, "data": {"id": 7}, "message": "ok"}, 0)
    resp = request_json("GET", "/api/x")
    assert resp.status == 200
    assert resp.data == {"id": 7}
    assert resp.body["message"] == "ok"


def test_a_raw_list_body_is_the_data(backend):
    routes, _ = backend
    routes["/api/list"] = (200, [{"id": 1}], 0)
    assert request_json("GET", "/api/list").data == [{"id": 1}]


@pytest.mark.parametrize("status,kind", [
    (401, "auth"), (403, "auth"), (503, "plugin_offline"), (404, "http"), (500, "http"),
])
def test_error_statuses_are_typed_and_keep_the_server_message(backend, status, kind):
    routes, _ = backend
    routes["/api/err"] = (status, {"error": "nope"}, 0)
    with pytest.raises(BackendError) as info:
        request_json("GET", "/api/err")
    assert info.value.kind == kind
    assert info.value.status == status
    assert "nope" in str(info.value)


def test_a_success_status_with_a_non_json_body_is_malformed(backend):
    routes, _ = backend
    routes["/api/html"] = (200, b"<html>not json</html>", 0)
    with pytest.raises(BackendError) as info:
        request_json("GET", "/api/html")
    assert info.value.kind == "malformed"


def test_an_unreachable_backend_says_so(monkeypatch):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("GUAARDVARK_URL", f"http://127.0.0.1:{port}")
    with pytest.raises(BackendError) as info:
        request_json("GET", "/api/x", connect_timeout=1)
    assert info.value.kind == "unreachable"
    assert "not answering" in str(info.value)


def test_a_read_timeout_is_its_own_kind(backend):
    routes, _ = backend
    routes["/api/slow"] = (200, {"data": 1}, 1.0)
    with pytest.raises(BackendError) as info:
        request_json("GET", "/api/slow", read_timeout=0.2)
    assert info.value.kind == "timeout"


def test_the_api_key_is_sent_and_never_logged(backend, monkeypatch, caplog):
    routes, requests = backend
    routes["/api/x"] = (200, {"data": 1}, 0)
    monkeypatch.setenv("GUAARDVARK_API_KEY", "sekrit-123")
    caplog.set_level(logging.DEBUG)
    request_json("GET", "/api/x")
    assert requests[-1][2].get("X-API-Key") == "sekrit-123"
    assert "sekrit-123" not in caplog.text


def test_a_timed_out_tool_run_is_not_sent_again(backend):
    routes, requests = backend
    routes["/api/tools/execute"] = (200, {"success": True, "result": {"success": True}}, 1.0)
    result = run_tool_in_backend("generate_video", {"prompt": "x"}, read_timeout=0.2)
    assert not result.success
    assert result.metadata["backend_error"] == "timeout"
    time.sleep(1.2)
    assert sum(1 for r in requests if r[1] == "/api/tools/execute") == 1


def test_run_tool_in_backend_returns_the_tool_result_without_internal_arguments(backend):
    routes, requests = backend
    routes["/api/tools/execute"] = (200, {
        "success": True,
        "result": {"success": False, "output": None, "error": "GPU busy", "metadata": {"a": 1}},
    }, 0)
    result = run_tool_in_backend("generate_image", {"prompt": "x", "_agent_context": {"session": 1}})
    assert (result.success, result.error, result.metadata) == (False, "GPU busy", {"a": 1})
    assert json.loads(requests[-1][3]) == {"tool_name": "generate_image", "parameters": {"prompt": "x"}}
