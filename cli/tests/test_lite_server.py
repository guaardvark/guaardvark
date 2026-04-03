"""Tests for the embedded lite server."""
import json
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def config_dir(tmp_path):
    config_path = tmp_path / ".guaardvark"
    config_path.mkdir()
    return config_path


@pytest.fixture
def patch_config(monkeypatch, config_dir):
    monkeypatch.setattr("llx.launch_config._config_dir", lambda: config_dir)
    return config_dir


@pytest.fixture
def lite_app(patch_config, tmp_path):
    from llx.lite_server import create_lite_app
    app = create_lite_app(db_path=str(tmp_path / "test.db"))
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(lite_app):
    return lite_app.test_client()


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["mode"] == "lite"
        assert "version" in data

    def test_health_includes_model(self, client, patch_config):
        from llx.launch_config import save_launch_config
        save_launch_config({"model": "llama3.3"})
        resp = client.get("/api/health")
        data = resp.get_json()
        assert data.get("model") == "llama3.3"


class TestModelEndpoints:
    def test_model_list_returns_models(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "llama3.3", "size": 4_000_000_000},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("llx.lite_server.httpx.get", return_value=mock_resp):
            resp = client.get("/api/model/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        models = data["data"]["models"]
        assert len(models) >= 1

    def test_model_status_returns_active_model(self, client, patch_config):
        from llx.launch_config import save_launch_config
        save_launch_config({"model": "llama3.3"})
        resp = client.get("/api/model/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["data"]["text_model"] == "llama3.3"

    def test_model_set_updates_config(self, client, patch_config):
        resp = client.post("/api/model/set",
            data=json.dumps({"model": "glm-4.7-flash"}),
            content_type="application/json")
        assert resp.status_code == 200
        from llx.launch_config import load_launch_config
        cfg = load_launch_config()
        assert cfg["model"] == "glm-4.7-flash"


class TestChatEndpoint:
    def test_chat_returns_response(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"content": "Hello!"},
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("llx.lite_server.httpx.post", return_value=mock_resp):
            resp = client.post("/api/chat/unified",
                data=json.dumps({
                    "session_id": "test-session",
                    "message": "Hi there",
                }),
                content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["response"] == "Hello!"
