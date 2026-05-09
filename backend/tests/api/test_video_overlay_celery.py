import pytest
from pathlib import Path
try:
    from flask import Flask
    from backend.models import db, Document
    from backend.api.video_overlay_api import video_overlay_bp
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType
    from backend.tasks.video_render_tasks import create_video_render_tasks
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

@pytest.fixture(autouse=True)
def mock_emit_event(monkeypatch):
    monkeypatch.setattr("backend.utils.unified_progress_system.UnifiedProgressSystem._emit_event", lambda *args, **kwargs: None)

def test_render_timeline_returns_202_and_job_id(client, monkeypatch):
    monkeypatch.setattr("backend.api.video_overlay_api.celery.send_task", lambda *args, **kwargs: None)
    
    from backend.models import db, Document
    doc = Document(filename="source_video.mp4", path="source_video.mp4")
    db.session.add(doc)
    db.session.commit()
    doc_id = doc.id
    
    monkeypatch.setattr("backend.api.video_overlay_api._resolve_video_path", lambda d: Path("dummy.mp4"))
    
    res = client.post("/api/video-overlay/render-timeline", json={"video_document_id": doc_id})
    assert res.status_code == 202
    assert "job_id" in res.json["data"]
    assert res.json["data"]["status"] == "pending"

def test_render_status_returns_404_for_unknown_job(client):
    res = client.get("/api/video-overlay/render-status/nope")
    assert res.status_code == 404

def test_render_status_returns_progress_for_known_job(client, app, monkeypatch):
    with app.app_context():
        progress_system = get_unified_progress()
        job_id = progress_system.create_process(ProcessType.VIDEO_RENDER, "Test Render")
        progress_system.update_process(job_id, 50, "Halfway there")
        
        res = client.get(f"/api/video-overlay/render-status/{job_id}")
        assert res.status_code == 200
        assert res.json["data"]["job_id"] == job_id
        assert res.json["data"]["status"] == "processing"
        assert res.json["data"]["progress"] == 50
        assert res.json["data"]["message"] == "Halfway there"

def test_render_timeline_task_invokes_render_timeline_service(monkeypatch, app):
    import sys
    from types import ModuleType
    mock_app_module = ModuleType("backend.app")
    mock_app_module.create_app = lambda: app
    sys.modules["backend.app"] = mock_app_module
    
    with app.app_context():
        # Mock the celery app and create the task
        class MockCeleryApp:
            def task(self, bind, name):
                def decorator(func):
                    self.task_func = func
                    return func
                return decorator
        
        mock_celery = MockCeleryApp()
        tasks = create_video_render_tasks(mock_celery)
        render_task = tasks["render_timeline_task"]
        
            # Mock backend.app.create_app to return the test app
            # Already mocked via sys.modules
        
        # Mock dependencies
        called_render = False
        def mock_render_timeline(*args, **kwargs):
            nonlocal called_render
            called_render = True
            
        monkeypatch.setattr("backend.tasks.video_render_tasks.render_timeline", mock_render_timeline)
        
        called_register = False
        def mock_register_file(*args, **kwargs):
            nonlocal called_register
            called_register = True
            doc = Document(filename="out.mp4", path="out.mp4")
            db.session.add(doc)
            db.session.commit()
            return doc
            
        monkeypatch.setattr("backend.tasks.video_render_tasks.register_file", mock_register_file)
        
        # Mock gate
        gate_registered = False
        gate_unregistered = False
        class MockGate:
            def register_running(self, kind, id):
                nonlocal gate_registered
                gate_registered = True
            def unregister_running(self, kind, id):
                nonlocal gate_unregistered
                gate_unregistered = True
                
        monkeypatch.setattr("backend.tasks.video_render_tasks.get_gate", lambda: MockGate())
        
        # Create dummy doc
        doc = Document(filename="source_video.mp4", path="source_video.mp4")
        db.session.add(doc)
        db.session.commit()
        doc_id = doc.id
        
        monkeypatch.setattr("backend.tasks.video_render_tasks._resolve_video_path", lambda d: Path("dummy.mp4"))
        
        # Create job
        progress_system = get_unified_progress()
        job_id = progress_system.create_process(ProcessType.VIDEO_RENDER, "Test Render")
        
        # Call task directly
        # The task is bound, so first arg is `self`
        render_task(None, {"video_document_id": doc_id}, "dummy_out.mp4", job_id)
        
        assert called_render
        assert called_register
        assert gate_registered
        assert gate_unregistered
        
        proc = progress_system.get_process(job_id)
        assert proc.status.value == "complete"
        assert proc.progress == 100
