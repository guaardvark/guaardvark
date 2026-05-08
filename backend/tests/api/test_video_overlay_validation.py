import pytest
try:
    from flask import Flask
    from backend.models import db
    from backend.api.video_overlay_api import video_overlay_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(video_overlay_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()

def test_videos_limit_validation(client):
    res = client.get("/api/video-overlay/videos?limit=not-a-number")
    assert res.status_code == 400
    assert res.json["error"]["code"] == "INVALID_FIELD"

def test_audio_library_limit_validation(client):
    res = client.get("/api/video-overlay/audio-library?limit=foo")
    assert res.status_code == 400
    assert res.json["error"]["code"] == "INVALID_FIELD"

def test_image_library_limit_validation(client):
    res = client.get("/api/video-overlay/image-library?limit=bar")
    assert res.status_code == 400
    assert res.json["error"]["code"] == "INVALID_FIELD"

def test_render_timeline_audio_volume_validation(client):
    res = client.post("/api/video-overlay/render-timeline", json={
        "video_document_id": 1,
        "audio_volume": "loud"
    })
    assert res.status_code == 400
    assert res.json["error"]["code"] == "INVALID_FIELD"
