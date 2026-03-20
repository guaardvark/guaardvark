import pytest
import time
import json
from unittest.mock import patch, MagicMock
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
    with patch("service.model_tier.requests.get") as mock_get, \
         patch("service.frame_analyzer.requests.post") as mock_post:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"models": [{"name": "moondream"}]})
        )
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={
                "message": {"content": "A test scene"},
                "done": True
            })
        )
        from service.app import app, _auth_token
        with TestClient(app) as c:
            c.headers["Authorization"] = f"Bearer {_auth_token}"
            yield c


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] in ("healthy", "degraded", "error")

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

class TestStatusEndpoint:
    def test_status(self, client):
        resp = client.get("/status")
        assert resp.status_code == 200
        assert "streams" in resp.json()
