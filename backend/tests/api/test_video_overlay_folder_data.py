import pytest
from sqlalchemy import event

try:
    from flask import Flask
    from backend.models import db, Folder, Document as DBDocument
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

def test_video_overlay_folder_data_videos(client, app):
    with app.app_context():
        folder = Folder(name="DoomBatch", path="Videos/DoomBatch")
        db.session.add(folder)
        db.session.commit()
        
        doc_video = DBDocument(filename="doom_001.mp4", path="Videos/DoomBatch/doom_001.mp4", folder_id=folder.id, type="video")
        db.session.add(doc_video)
        db.session.commit()

    res = client.get("/api/video-overlay/videos")
    assert res.status_code == 200
    data = res.json["data"]
    assert len(data["videos"]) == 1
    assert data["videos"][0]["folder"] == {"id": 1, "name": "DoomBatch", "path": "Videos/DoomBatch"}

def test_video_overlay_folder_data_audio(client, app):
    with app.app_context():
        folder = Folder(name="DoomBatch", path="Videos/DoomBatch")
        db.session.add(folder)
        db.session.commit()
        
        doc_audio = DBDocument(filename="doom_001.wav", path="Videos/DoomBatch/doom_001.wav", folder_id=folder.id, type="audio")
        db.session.add(doc_audio)
        db.session.commit()

    res = client.get("/api/video-overlay/audio-library")
    assert res.status_code == 200
    data = res.json["data"]
    assert len(data["audio"]) == 1
    assert data["audio"][0]["folder"] == {"id": 1, "name": "DoomBatch", "path": "Videos/DoomBatch"}

def test_video_overlay_folder_data_image(client, app):
    with app.app_context():
        folder = Folder(name="DoomBatch", path="Videos/DoomBatch")
        db.session.add(folder)
        db.session.commit()
        
        doc_image = DBDocument(filename="doom_001.png", path="Videos/DoomBatch/doom_001.png", folder_id=folder.id, type="image")
        db.session.add(doc_image)
        db.session.commit()

    res = client.get("/api/video-overlay/image-library")
    assert res.status_code == 200
    data = res.json["data"]
    assert len(data["images"]) == 1
    assert data["images"][0]["folder"] == {"id": 1, "name": "DoomBatch", "path": "Videos/DoomBatch"}

def test_video_overlay_folder_data_n_plus_1(client, app):
    with app.app_context():
        folder = Folder(name="DoomBatch", path="Videos/DoomBatch")
        db.session.add(folder)
        db.session.commit()
        
        docs = [
            DBDocument(filename=f"doom_{i:03d}.mp4", path=f"Videos/DoomBatch/doom_{i:03d}.mp4", folder_id=folder.id, type="video")
            for i in range(5)
        ]
        db.session.add_all(docs)
        db.session.commit()

    query_count = 0
    def count_queries(conn, cursor, statement, parameters, context, executemany):
        nonlocal query_count
        query_count += 1

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", count_queries)
        try:
            res = client.get("/api/video-overlay/videos")
            assert res.status_code == 200
            data = res.json["data"]
            assert len(data["videos"]) == 5
        finally:
            event.remove(db.engine, "before_cursor_execute", count_queries)

    # 1 query for the list of videos (with joinedload).
    # If N+1, it would be 1 query for videos + 5 queries for folders = 6 queries.
    assert query_count == 1, f"Expected 1 query, got {query_count}"
