import pytest

try:
    from flask import Flask
    from backend.models import db, MusicVideo, Document
    from backend.api.music_video_api import bp as music_video_bp
    from backend.services.music_video_service import MusicVideoService
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(music_video_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _no_dispatch(monkeypatch):
    # Never actually enqueue Celery work in API tests.
    monkeypatch.setattr(MusicVideoService, "dispatch_agent", lambda self, mv_id, agent: None)
    monkeypatch.setattr(
        "backend.services.video_model_registry.resolve_active_video_model",
        lambda role, explicit=None, surface=None: (explicit or "wan22-5b", None),
    )
    monkeypatch.setattr(
        "backend.api.music_video_api.get_video_generator",
        lambda: type("VG", (), {"service_available": True})(),
    )


def _song_doc(tmp_path):
    f = tmp_path / "song.wav"
    f.write_bytes(b"RIFF")
    doc = Document(filename="song.wav", path=str(f), type="wav", size=4)
    db.session.add(doc)
    db.session.commit()
    return doc


def test_create_requires_fields(client):
    resp = client.post("/api/music-video", json={"name": "x"})
    assert resp.status_code == 400


def test_create_rejects_missing_song_document(client):
    resp = client.post("/api/music-video", json={
        "name": "x", "style_prompt": "deep blue", "song_document_id": 9999,
    })
    assert resp.status_code == 400


def test_create_rejects_unknown_i2v_model(client, app, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.services.video_model_registry.resolve_active_video_model",
        lambda *a, **k: (None, "Unknown video model 'nope'"),
    )
    with app.app_context():
        doc = _song_doc(tmp_path)
        resp = client.post("/api/music-video", json={
            "name": "x",
            "style_prompt": "deep blue",
            "song_document_id": doc.id,
            "settings": {"i2v_model": "nope"},
        })
    assert resp.status_code == 400
    assert "Unknown" in (resp.get_json() or {}).get("error", "")


def test_create_advances_to_analyzing(client, app, tmp_path):
    with app.app_context():
        doc = _song_doc(tmp_path)
        resp = client.post("/api/music-video", json={
            "name": "Loss & Heartache",
            "style_prompt": "animation style, deep blue, slow movement",
            "song_document_id": doc.id,
        })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["current_stage"] == "analyzing"
    assert data["status"] == "analyzing"
    assert data["id"] > 0


def test_approve_rejects_when_not_awaiting(client, app, tmp_path):
    with app.app_context():
        doc = _song_doc(tmp_path)
        svc = MusicVideoService(db.session)
        mv = svc.create(name="x", song_document_id=doc.id, song_path=str(tmp_path / "song.wav"),
                        style_prompt="x", project_id=None)
        mv_id = mv.id
    resp = client.post(f"/api/music-video/{mv_id}/approve")
    assert resp.status_code == 409  # still in draft, not awaiting_approval


def test_approve_advances_to_generating(client, app, tmp_path):
    with app.app_context():
        doc = _song_doc(tmp_path)
        svc = MusicVideoService(db.session)
        mv = svc.create(name="x", song_document_id=doc.id, song_path=str(tmp_path / "song.wav"),
                        style_prompt="x", project_id=None)
        # Walk to the gate and give it a cut plan so the estimate surfaces.
        mv.current_stage = "awaiting_approval"
        mv.status = "awaiting_approval"
        mv.cut_plan = [{"index": 0, "start_s": 0.0, "end_s": 2.0, "energy": 1.0, "section_label": "a"}]
        db.session.commit()
        mv_id = mv.id
    resp = client.post(f"/api/music-video/{mv_id}/approve")
    assert resp.status_code == 200
    assert resp.get_json()["current_stage"] == "generating"


def test_get_surfaces_estimate_at_gate(client, app, tmp_path):
    with app.app_context():
        doc = _song_doc(tmp_path)
        svc = MusicVideoService(db.session)
        mv = svc.create(name="x", song_document_id=doc.id, song_path=str(tmp_path / "song.wav"),
                        style_prompt="x", project_id=None)
        mv.cut_plan = [{"index": i, "start_s": i, "end_s": i + 1, "energy": 1.0, "section_label": "a"}
                       for i in range(40)]
        mv.clips = [{"index": i, "start": i, "end": i + 1, "clip_path": None, "status": "pending"}
                    for i in range(40)]
        db.session.commit()
        mv_id = mv.id
    data = client.get(f"/api/music-video/{mv_id}").get_json()
    assert data["cut_count"] == 40
    assert data["clip_count"] == 40
    assert data["clips_done"] == 0
    assert data["estimate"]["clips_to_generate"] == 40
    assert data["estimate"]["estimated_seconds"] == 40 * 75


def test_cancel_generating_music_video(client, app, tmp_path):
    with app.app_context():
        doc = _song_doc(tmp_path)
        svc = MusicVideoService(db.session)
        mv = svc.create(
            name="x",
            song_document_id=doc.id,
            song_path=str(tmp_path / "song.wav"),
            style_prompt="x",
            project_id=None,
        )
        mv.current_stage = "generating"
        mv.status = "generating"
        mv.clips = [
            {"index": 0, "start": 0, "end": 1, "clip_path": None, "status": "pending"},
            {"index": 1, "start": 1, "end": 2, "clip_path": "/tmp/done.mp4", "status": "done"},
        ]
        db.session.commit()
        mv_id = mv.id

    resp = client.post(f"/api/music-video/{mv_id}/cancel")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "cancelled"
    assert data["clips"][0]["status"] == "cancelled"
    assert data["clips"][1]["status"] == "done"


def test_approve_rejects_without_cut_plan(client, app, tmp_path):
    with app.app_context():
        doc = _song_doc(tmp_path)
        svc = MusicVideoService(db.session)
        mv = svc.create(
            name="x",
            song_document_id=doc.id,
            song_path=str(tmp_path / "song.wav"),
            style_prompt="x",
            project_id=None,
        )
        mv.current_stage = "awaiting_approval"
        mv.status = "awaiting_approval"
        db.session.commit()
        mv_id = mv.id

    resp = client.post(f"/api/music-video/{mv_id}/approve")
    assert resp.status_code == 409


def test_analyze_restarts_orphaned_awaiting_approval(client, app, tmp_path):
    with app.app_context():
        doc = _song_doc(tmp_path)
        svc = MusicVideoService(db.session)
        mv = svc.create(
            name="x",
            song_document_id=doc.id,
            song_path=str(tmp_path / "song.wav"),
            style_prompt="x",
            project_id=None,
        )
        mv.current_stage = "awaiting_approval"
        mv.status = "awaiting_approval"
        db.session.commit()
        mv_id = mv.id

    resp = client.post(f"/api/music-video/{mv_id}/analyze")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["current_stage"] == "analyzing"
    assert data["status"] == "analyzing"


def test_cancel_rejects_non_cancellable_stage(client, app, tmp_path):
    with app.app_context():
        doc = _song_doc(tmp_path)
        svc = MusicVideoService(db.session)
        mv = svc.create(
            name="x",
            song_document_id=doc.id,
            song_path=str(tmp_path / "song.wav"),
            style_prompt="x",
            project_id=None,
        )
        mv.current_stage = "complete"
        mv.status = "complete"
        db.session.commit()
        mv_id = mv.id

    resp = client.post(f"/api/music-video/{mv_id}/cancel")
    assert resp.status_code == 409
