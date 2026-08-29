import pytest
from pathlib import Path
try:
    from flask import Flask
    from backend.models import db, Document
    from backend.api.video_overlay_api import video_overlay_bp
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)

# Deliberately outside the guard: if the render tasks cannot import, Celery
# drops them and every timeline render silently never runs. That must fail
# this file, not skip it (2026-08-28: a renamed font constant did exactly
# this and the skip hid it for a day).
from backend.tasks.video_render_tasks import create_video_render_tasks  # noqa: E402

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
    # setitem, not assignment: the stub has no `app` attribute, so leaking it past
    # this test breaks every later `from backend.app import app`.
    monkeypatch.setitem(sys.modules, "backend.app", mock_app_module)
    
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
        
        # The task claims the GPU through gpu_session (not the old get_gate
        # register/unregister pair). Stand in a context manager that records use.
        from contextlib import contextmanager
        session_entered = False
        session_exited = False

        @contextmanager
        def mock_gpu_session(kind, ident, **kwargs):
            nonlocal session_entered, session_exited
            session_entered = True
            try:
                yield
            finally:
                session_exited = True

        monkeypatch.setattr("backend.tasks.video_render_tasks.gpu_session", mock_gpu_session)
        
        # Create dummy doc
        doc = Document(filename="source_video.mp4", path="source_video.mp4")
        db.session.add(doc)
        db.session.commit()
        doc_id = doc.id
        
        # The task imports _resolve_video_path from the API module at call time; patch it there.
        monkeypatch.setattr("backend.api.video_overlay_api._resolve_video_path", lambda d: Path("dummy.mp4"))
        
        # Create job
        progress_system = get_unified_progress()
        job_id = progress_system.create_process(ProcessType.VIDEO_RENDER, "Test Render")
        
        # Call task directly
        # The task is bound, so first arg is `self`
        render_task(None, {"video_document_id": doc_id}, "dummy_out.mp4", job_id)
        
        assert called_render
        assert called_register
        assert session_entered and session_exited
        
        proc = progress_system.get_process(job_id)
        assert proc.status.value == "complete"
        assert proc.progress == 100
