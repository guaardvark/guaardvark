"""Filenames produced by the render dispatcher must be UUID-suffixed.

After Commit 3, the endpoint dispatches to Celery and the *worker* writes
the file. The endpoint pre-allocates the output_path though, so we can
assert UUID-ness by capturing what gets passed into celery.send_task.
"""
import pytest
import re
from pathlib import Path

try:
    from flask import Flask
    from backend.models import db, Document
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


def test_render_timeline_filename_uuid(client, monkeypatch):
    """Two dispatches of the same source should pre-allocate distinct UUID-suffixed paths."""
    captured_paths = []

    # Capture what gets sent to Celery instead of actually running it.
    def fake_send_task(name, args=None, queue=None, **kwargs):
        captured_paths.append(args[1])  # args = (payload, output_path_str, job_id)

    from backend.api import video_overlay_api as mod
    monkeypatch.setattr(mod.celery, "send_task", fake_send_task)
    monkeypatch.setattr(mod, "_resolve_video_path", lambda d: Path("dummy.mp4"))

    doc = Document(filename="source_video.mp4", path="source_video.mp4")
    db.session.add(doc)
    db.session.commit()
    doc_id = doc.id

    res1 = client.post("/api/video-overlay/render-timeline", json={"video_document_id": doc_id})
    assert res1.status_code == 202, res1.get_data(as_text=True)
    assert "job_id" in res1.json["data"]

    res2 = client.post("/api/video-overlay/render-timeline", json={"video_document_id": doc_id})
    assert res2.status_code == 202

    assert len(captured_paths) == 2
    name1 = Path(captured_paths[0]).name
    name2 = Path(captured_paths[1]).name

    assert name1 != name2, "Concurrent dispatches must not collide on the same filename"

    pattern = re.compile(r"^source_video_[0-9a-f]{8}\.mp4$")
    assert pattern.match(name1), f"{name1!r} doesn't match UUID pattern"
    assert pattern.match(name2), f"{name2!r} doesn't match UUID pattern"
