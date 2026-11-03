import pytest
import time
import json
from unittest.mock import patch

from conftest import fake_ollama_get, fake_ollama_post
from fastapi.testclient import TestClient
from PIL import Image
import io
import base64


def _make_frame(color="red"):
    img = Image.new("RGB", (64, 64), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def client():
    """Create test client with mocked Ollama and pre-authenticated."""
    with patch("service.model_tier.requests.get", side_effect=fake_ollama_get), \
         patch("service.frame_analyzer.requests.post", side_effect=fake_ollama_post):
        from service.app import app, _auth_token
        # An address the service's Host check answers.
        with TestClient(app, base_url="http://127.0.0.1:8201") as c:
            c.headers["Authorization"] = f"Bearer {_auth_token()}"
            yield c


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] in ("healthy", "degraded", "error")

    def test_health_never_carries_the_token(self, client):
        from service.app import _auth_token
        resp = client.get("/health", headers={"Authorization": ""})
        assert "token" not in resp.json()
        assert _auth_token() not in resp.text

    def test_a_rebound_name_is_refused(self, client):
        resp = client.post("/camera/start", json={}, headers={"Host": "evil.example:8201"})
        assert resp.status_code == 421

    @pytest.mark.parametrize("method, path", [
        ("GET", "/status"), ("GET", "/context"), ("GET", "/frame/latest"), ("GET", "/camera/status"),
        ("POST", "/camera/start"), ("POST", "/camera/stop"), ("POST", "/stream/start"),
        ("POST", "/gpu/contention"), ("GET", "/benchmark/results"), ("GET", "/config"),
    ])
    def test_every_route_but_health_needs_the_token(self, client, method, path):
        for token in ("", "Bearer wrong"):
            resp = client.request(method, path, json={}, headers={"Authorization": token})
            assert resp.status_code == 401

class TestStreamLifecycle:
    def test_start_stop_stream(self, client):
        resp = client.post("/stream/start", json={"source_type": "camera"})
        assert resp.status_code == 200
        stream_id = resp.json()["stream_id"]

        resp = client.post("/stream/stop", json={"stream_id": stream_id})
        assert resp.status_code == 200

    def test_submit_frame(self, client):
        resp = client.post("/stream/start", json={"source_type": "camera"})
        stream_id = resp.json()["stream_id"]

        resp = client.post("/frame", json={
            "stream_id": stream_id,
            "frame": _make_frame(),
            "timestamp": time.time()
        })
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True

        client.post("/stream/stop", json={"stream_id": stream_id})

class TestContextEndpoint:
    def test_context_no_stream(self, client):
        resp = client.get("/context")
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

class TestGPUContention:
    def test_contention_start_stop(self, client):
        resp = client.post("/gpu/contention", json={"source": "image_gen", "action": "start"})
        assert resp.status_code == 200
        assert "throttle_state" in resp.json()

        resp = client.post("/gpu/contention", json={"source": "image_gen", "action": "stop"})
        assert resp.status_code == 200

class TestConfigEndpoint:
    def test_get_config(self, client):
        resp = client.get("/config")
        assert resp.status_code == 200
        assert "max_fps" in resp.json()
        assert resp.json()["phash_threshold"] == 8

    def test_fps_edit_reaches_the_throttle(self, client):
        from service import app as app_module
        resp = client.put("/config", json={"max_fps": 1.0, "phash_threshold": 12})
        assert resp.status_code == 200
        assert app_module._adaptive_throttle.max_fps == 1.0
        assert app_module._change_detector.phash_threshold == 12
        client.put("/config", json={"max_fps": 2.0, "phash_threshold": 8})

    def test_frame_keeps_the_callers_timestamp(self, client):
        from service import app as app_module
        resp = client.post("/stream/start", json={"source_type": "camera"})
        stream_id = resp.json()["stream_id"]
        client.post("/frame", json={"stream_id": stream_id, "frame": _make_frame(),
                                    "timestamp": 1700000000.5})
        latest = app_module._stream_manager.get_latest_frame(stream_id)
        assert latest["timestamp"] == 1700000000.5
        client.post("/stream/stop", json={"stream_id": stream_id})

class TestStatusEndpoint:
    def test_status(self, client):
        resp = client.get("/status")
        assert resp.status_code == 200
        assert "streams" in resp.json()
