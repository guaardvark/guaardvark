"""Upscale from the Files page: /api/upscaling/upscale/document.

The upscaler itself is replaced at the proxy seam; what is checked is what the
route sends it and where the result is filed (beside the source, in its folder).
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from flask import Flask

import backend.api.upscaling_api as m
import backend.services.document_path_resolver as resolver
from backend.models import Document, Folder, db


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(m.upscaling_bp)

    images = tmp_path / "Images"
    images.mkdir()
    (images / "harbor.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (images / "harbor.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (images / "notes.txt").write_text("not a picture")

    monkeypatch.setattr(resolver, "resolve_document_path", lambda doc: tmp_path / doc.path)
    monkeypatch.setattr(m, "_start_upscaling_for_job", lambda: None)
    monkeypatch.setattr(m, "_gpu_turn", lambda model: contextlib.nullcontext())

    with app.app_context():
        db.create_all()
        folder = Folder(name="Images", path="Images")
        db.session.add(folder)
        db.session.flush()
        ids = {}
        for name in ("harbor.png", "harbor.mp4", "notes.txt"):
            doc = Document(filename=name, path=f"Images/{name}", folder_id=folder.id, type=Path(name).suffix)
            db.session.add(doc)
            db.session.flush()
            ids[name] = doc.id
        db.session.commit()
        yield app.test_client(), ids, folder.id, images
        db.session.remove()
        db.drop_all()


def _fake_image_upscaler(calls):
    def fake_post(path, payload, timeout=None):
        calls.append((path, payload))
        Path(payload["output_path"]).write_bytes(b"\x89PNG upscaled")
        return {"status": "completed", "output_path": payload["output_path"]}, 200
    return fake_post


def test_image_is_upscaled_and_filed_beside_the_source(env, monkeypatch):
    client, ids, folder_id, images = env
    calls = []
    monkeypatch.setattr(m, "_proxy_post", _fake_image_upscaler(calls))

    resp = client.post("/api/upscaling/upscale/document", json={"document_id": ids["harbor.png"]})

    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert data["filename"] == "harbor_upscaled.png"
    assert data["path"] == "Images/harbor_upscaled.png"
    path, payload = calls[0]
    assert path == "/upscale/image"
    assert payload["input_path"] == str(images / "harbor.png")
    assert payload["output_path"] == str(images / "harbor_upscaled.png")
    row = db.session.get(Document, data["document_id"])
    assert row.folder_id == folder_id


def test_a_second_upscale_takes_the_next_free_name(env, monkeypatch):
    client, ids, _, _ = env
    monkeypatch.setattr(m, "_proxy_post", _fake_image_upscaler([]))

    first = client.post("/api/upscaling/upscale/document", json={"document_id": ids["harbor.png"]})
    second = client.post("/api/upscaling/upscale/document", json={"document_id": ids["harbor.png"]})

    assert first.get_json()["data"]["filename"] == "harbor_upscaled.png"
    assert second.get_json()["data"]["filename"] == "harbor_upscaled (2).png"


def test_video_is_queued_and_filed_when_the_job_completes(env, monkeypatch):
    client, ids, folder_id, images = env
    submitted = {}

    class FakeSupervisor:
        def submit(self, kind, route, payload, label, on_complete=None):
            submitted.update(kind=kind, route=route, payload=payload)
            Path(payload["output_path"]).write_bytes(b"upscaled video")
            job = {"job_id": "job-1", "status": "completed"}
            on_complete(job)
            return job, None, 200

    monkeypatch.setattr(m.upscale_supervisor, "enabled", lambda: True)
    monkeypatch.setattr(m.upscale_supervisor, "get_supervisor", lambda: FakeSupervisor())

    resp = client.post("/api/upscaling/upscale/document", json={"document_id": ids["harbor.mp4"]})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["output_file"] == "harbor_upscaled.mp4"
    assert submitted["kind"] == "video"
    assert submitted["payload"]["input_path"] == str(images / "harbor.mp4")
    filed = Document.query.filter_by(filename="harbor_upscaled.mp4").one()
    assert filed.folder_id == folder_id
    assert filed.path == "Images/harbor_upscaled.mp4"


def test_a_file_that_is_not_a_picture_or_video_is_refused(env):
    client, ids, _, _ = env
    resp = client.post("/api/upscaling/upscale/document", json={"document_id": ids["notes.txt"]})
    assert resp.status_code == 400
