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
    # Mock the render_timeline service to just touch the file
    def mock_render_timeline(output_path, **kwargs):
        Path(output_path).touch()
    
    monkeypatch.setattr("backend.services.video_timeline_render.render_timeline", mock_render_timeline)
    
    # Create a dummy video doc
    from backend.models import db, Document
    doc = Document(filename="source_video.mp4", path="source_video.mp4")
    db.session.add(doc)
    db.session.commit()
    doc_id = doc.id
    
    # Mock _resolve_video_path to return a dummy path
    monkeypatch.setattr("backend.api.video_overlay_api._resolve_video_path", lambda d: Path("dummy.mp4"))
    
    # Fire two requests in sequence
    res1 = client.post("/api/video-overlay/render-timeline", json={"video_document_id": doc_id})
    assert res1.status_code == 201
    
    res2 = client.post("/api/video-overlay/render-timeline", json={"video_document_id": doc_id})
    assert res2.status_code == 201
    
    # Assert filenames
    doc1 = db.session.get(Document, res1.json["data"]["id"])
    doc2 = db.session.get(Document, res2.json["data"]["id"])
    
    assert doc1.filename != doc2.filename
    
    pattern = re.compile(r"^source_video_[0-9a-f]{8}\.mp4$")
    assert pattern.match(doc1.filename), f"Filename {doc1.filename} does not match pattern"
    assert pattern.match(doc2.filename), f"Filename {doc2.filename} does not match pattern"
