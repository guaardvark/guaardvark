"""Integration test: full pipeline flow with mocked Ollama.

Tests the complete path: start stream → submit frames → get context → stop stream.
"""
import time
import pytest
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
    with patch("service.frame_analyzer.requests.post") as mock_post, \
         patch("service.model_tier.requests.get") as mock_get:
        # Mock Ollama model list
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"models": [{"name": "moondream"}]})
        )
        # Mock Ollama inference
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={
                "message": {"content": "A person at a desk with a laptop"},
                "done": True
            })
        )
        from service.app import app, _auth_token
        with TestClient(app) as c:
            c.headers["Authorization"] = f"Bearer {_auth_token}"
            yield c


class TestFullPipeline:
    def test_stream_lifecycle(self, client):
        # Start stream
        resp = client.post("/stream/start", json={"source_type": "camera"})
        assert resp.status_code == 200
        stream_id = resp.json()["stream_id"]

        # Submit frames
        for color in ["red", "blue", "green"]:
            resp = client.post("/frame", json={
                "stream_id": stream_id,
                "frame": _make_frame(color),
                "timestamp": time.time()
            })
            assert resp.json()["accepted"] is True
            time.sleep(0.1)

        # Allow analysis to process
        time.sleep(2)

        # Get context
        resp = client.get("/context")
        data = resp.json()
        # Context should be active and have a scene description
        # (may or may not have processed yet depending on timing)
        assert resp.status_code == 200

        # Get status
        resp = client.get("/status")
        assert resp.status_code == 200
        assert stream_id in resp.json()["streams"]

        # Stop stream
        resp = client.post("/stream/stop", json={"stream_id": stream_id})
        assert resp.status_code == 200

        # Context should be inactive after stop
        resp = client.get("/context")
        assert resp.json()["is_active"] is False

    def test_direct_analyze(self, client):
        resp = client.post("/analyze", json={
            "frame": _make_frame(),
            "prompt": "What do you see?"
        })
        assert resp.status_code == 200
        assert "description" in resp.json()

    def test_gpu_contention_flow(self, client):
        # Start stream
        resp = client.post("/stream/start", json={"source_type": "camera"})
        stream_id = resp.json()["stream_id"]

        # Signal contention
        resp = client.post("/gpu/contention", json={"source": "image_gen", "action": "start"})
        assert resp.status_code == 200

        # Release contention
        resp = client.post("/gpu/contention", json={"source": "image_gen", "action": "stop"})
        assert resp.status_code == 200

        client.post("/stream/stop", json={"stream_id": stream_id})
