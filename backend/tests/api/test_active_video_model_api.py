"""GET/POST /api/settings/active_video_model."""
import pytest

try:
    from flask import Flask
    from backend.api.settings_api import settings_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(settings_bp)
    stored = {}

    def _save(key, value):
        stored[key] = value

    def _get(key, default="", **kw):
        return stored.get(key, default)

    monkeypatch.setattr("backend.utils.settings_utils.save_setting", _save)
    monkeypatch.setattr("backend.utils.settings_utils.get_setting", _get)
    monkeypatch.setattr(
        "backend.services.video_model_registry.preflight_video_model",
        lambda m: (True, "") if m == "wan22-5b" else (False, f"not installed: {m}"),
    )
    monkeypatch.setattr(
        "backend.services.video_model_registry.resolve_active_video_model",
        lambda role, explicit=None, surface=None: (stored.get("active_video_model") or "wan22-5b", None),
    )
    return app.test_client()


def test_get_returns_resolved(client):
    resp = client.get("/api/settings/active_video_model")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "resolved" in data
    assert data["resolved"]["t2v"]["model"] == "wan22-5b"


def test_post_unknown_is_400(client):
    resp = client.post("/api/settings/active_video_model", json={"model": "nope"})
    assert resp.status_code == 400


def test_post_uninstalled_is_400(client):
    resp = client.post("/api/settings/active_video_model", json={"model": "wan22-14b"})
    assert resp.status_code == 400


def test_post_installed_saves(client):
    resp = client.post("/api/settings/active_video_model", json={"model": "wan22-5b"})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["model"] == "wan22-5b"
