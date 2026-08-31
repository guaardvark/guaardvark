"""Delete History removes batch-image, batch-video and audio history — files
and their database mirrors — and nothing else in the same folders.

Scope is structural: a batch is a directory carrying batch_metadata.json, an
audio generation is a uuid-hex-named file. Hand-named audio, editor renders,
root folders, indexing job history and running batches must all survive.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

import requests
from flask import Flask

from backend.models import Document, EvalPair, Folder, JobHistory, RetentionAudit, db
from backend.services import generation_history_service as svc

HEX_A = "0" * 31 + "a"
HEX_B = "0" * 31 + "b"


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def upload(tmp_path):
    """An uploads tree mixing history with things that must survive."""
    img = tmp_path / "Images" / "ImageBatch_x"
    (img / "images").mkdir(parents=True)
    (img / "batch_metadata.json").write_text(json.dumps({"batch_id": "ImageBatch_x", "status": "completed"}))
    (img / "images" / "a.png").write_bytes(b"x" * 100)

    vid = tmp_path / "Videos" / "VideoBatch_y"
    vid.mkdir(parents=True)
    (vid / "batch_metadata.json").write_text(json.dumps({"batch_id": "VideoBatch_y", "status": "completed"}))
    (vid / "clip.mp4").write_bytes(b"y" * 200)
    renders = tmp_path / "Videos" / "Editor Renders"
    renders.mkdir()
    (renders / "r.mp4").write_bytes(b"keep")

    audio = tmp_path / "Audio"
    (audio / ".jobs").mkdir(parents=True)
    (audio / f"{HEX_A}.wav").write_bytes(b"a" * 50)
    (audio / f"{HEX_A}_input_params.json").write_text("{}")
    (audio / "NARRATOR.wav").write_bytes(b"keep")
    (audio / ".jobs" / f"{HEX_A}.json").write_text(json.dumps({"id": HEX_A, "status": "done"}))
    (audio / ".jobs" / f"{HEX_B}.json").write_text(json.dumps({"id": HEX_B, "status": "running"}))
    return tmp_path


@pytest.fixture
def seeded(app, upload):
    images_root = Folder(name="Images", path="Images")
    videos_root = Folder(name="Videos", path="Videos")
    audio_root = Folder(name="Audio", path="Audio")
    db.session.add_all([images_root, videos_root, audio_root])
    db.session.flush()
    img_folder = Folder(name="ImageBatch_x", path="Images/ImageBatch_x", parent_id=images_root.id)
    vid_folder = Folder(name="VideoBatch_y", path="Videos/VideoBatch_y", parent_id=videos_root.id)
    renders = Folder(name="Editor Renders", path="Videos/Editor Renders", parent_id=videos_root.id)
    db.session.add_all([img_folder, vid_folder, renders])
    db.session.flush()
    docs = [
        Document(filename="a.png", path="Images/ImageBatch_x/images/a.png", folder_id=img_folder.id),
        Document(filename="clip.mp4", path="Videos/VideoBatch_y/clip.mp4", folder_id=vid_folder.id),
        Document(filename="r.mp4", path="Videos/Editor Renders/r.mp4", folder_id=renders.id),
        Document(filename=f"{HEX_A}.wav", path=f"Audio/{HEX_A}.wav", folder_id=audio_root.id),
        Document(filename="NARRATOR.wav", path="Audio/NARRATOR.wav", folder_id=audio_root.id),
    ]
    db.session.add_all(docs)
    db.session.flush()
    db.session.add(EvalPair(question="q", expected_answer="a", source_doc_id=docs[0].id))
    now = datetime.now()
    db.session.add_all([
        JobHistory(id="unified:ImageBatch_x", kind="unified", native_id="ImageBatch_x", label="img",
                   status="completed", finished_at=now, job_metadata={"process_type": "image_generation"}),
        JobHistory(id="unified:idx1", kind="unified", native_id="idx1", label="index",
                   status="completed", finished_at=now, job_metadata={"process_type": "indexing"}),
        JobHistory(id="video_gen:VideoBatch_y", kind="video_gen", native_id="VideoBatch_y", label="vid",
                   status="completed", finished_at=now),
    ])
    db.session.commit()
    return upload


@pytest.fixture
def no_generators(monkeypatch):
    monkeypatch.setattr(svc, "_generator_instance", lambda kind: None)


@pytest.fixture
def sidecar_down(monkeypatch):
    def _down(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "delete", _down)
    monkeypatch.setattr(requests, "get", _down)


def _tree(path):
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def test_counts_only_see_history(seeded, no_generators, sidecar_down):
    img_files, img_bytes = _tree(seeded / "Images" / "ImageBatch_x")
    vid_files, vid_bytes = _tree(seeded / "Videos" / "VideoBatch_y")
    counts = svc.count_generation_history(upload_dir=seeded)
    # batch_metadata.json is deleted with the batch, so it is counted with it.
    assert counts["images"] == {"batches": 1, "files": img_files, "bytes": img_bytes, "running": []}
    assert img_files == 2 and img_bytes > 100
    assert counts["videos"] == {"batches": 1, "files": vid_files, "bytes": vid_bytes, "running": []}
    assert counts["audio"] == {"jobs": 2, "files": 2, "bytes": 52, "running": []}
    # Editor render and NARRATOR docs are not counted; indexing history is not counted.
    assert counts["db"] == {"documents": 3, "folders": 2, "job_history": 2}
    assert counts["total_bytes"] == img_bytes + vid_bytes + 52


def test_delete_removes_history_and_keeps_everything_else(seeded, no_generators, sidecar_down):
    img_files, img_bytes = _tree(seeded / "Images" / "ImageBatch_x")
    _, vid_bytes = _tree(seeded / "Videos" / "VideoBatch_y")
    result = svc.delete_generation_history(upload_dir=seeded, triggered_by="test")

    assert result["success"], result["errors"]
    assert result["sidecar_available"] is False
    assert result["deleted"]["images"] == {"batches": 1, "files": img_files, "bytes": img_bytes}
    assert result["deleted"]["videos"]["batches"] == 1
    # Sidecar down: both job files are stale, both go.
    assert result["deleted"]["audio"] == {"jobs": 2, "files": 2, "bytes": 52}
    assert result["deleted"]["documents"] == 3
    assert result["deleted"]["folders"] == 2
    assert result["deleted"]["job_history"] == 2
    assert result["bytes_freed"] == img_bytes + vid_bytes + 52

    assert not (seeded / "Images" / "ImageBatch_x").exists()
    assert not (seeded / "Videos" / "VideoBatch_y").exists()
    assert (seeded / "Videos" / "Editor Renders" / "r.mp4").exists()
    assert (seeded / "Audio" / "NARRATOR.wav").exists()
    assert not (seeded / "Audio" / f"{HEX_A}.wav").exists()
    assert not list((seeded / "Audio" / ".jobs").glob("*.json"))

    remaining_folders = {f.path for f in Folder.query.all()}
    assert remaining_folders == {"Images", "Videos", "Audio", "Videos/Editor Renders"}
    remaining_docs = {d.path for d in Document.query.all()}
    assert remaining_docs == {"Videos/Editor Renders/r.mp4", "Audio/NARRATOR.wav"}
    assert [j.id for j in JobHistory.query.all()] == ["unified:idx1"]
    assert EvalPair.query.one().source_doc_id is None

    audits = {r.kind: r for r in RetentionAudit.query.all()}
    assert set(audits) == {
        "generation_history_images", "generation_history_videos",
        "generation_history_audio", "generation_history_db",
    }
    assert audits["generation_history_images"].bytes_freed == img_bytes
    assert audits["generation_history_db"].item_count == 7
    assert audits["generation_history_images"].triggered_by == "test"


def test_running_batch_is_skipped_and_reported(seeded, sidecar_down, monkeypatch):
    live = SimpleNamespace(
        active_batches={"ImageBatch_x": SimpleNamespace(status="running")},
        batch_lock=threading.Lock(),
        cancel_events={},
        queue_order=["ImageBatch_x"],
    )
    monkeypatch.setattr(svc, "_generator_instance", lambda kind: live if kind == "images" else None)

    counts = svc.count_generation_history(upload_dir=seeded)
    assert counts["images"]["running"] == ["ImageBatch_x"]

    result = svc.delete_generation_history(upload_dir=seeded)
    assert result["skipped"]["images"] == ["ImageBatch_x"]
    assert result["deleted"]["images"]["batches"] == 0
    assert (seeded / "Images" / "ImageBatch_x" / "batch_metadata.json").exists()
    # Its DB mirror stays with it.
    assert Folder.query.filter_by(path="Images/ImageBatch_x").one()
    assert db.session.get(JobHistory, "unified:ImageBatch_x") is not None
    assert "ImageBatch_x" in live.active_batches
    # The video batch was not running and went normally.
    assert result["deleted"]["videos"]["batches"] == 1


def test_stale_running_metadata_without_a_live_generator_is_deletable(seeded, no_generators, sidecar_down):
    meta = seeded / "Images" / "ImageBatch_x" / "batch_metadata.json"
    meta.write_text(json.dumps({"batch_id": "ImageBatch_x", "status": "running"}))
    result = svc.delete_generation_history(upload_dir=seeded)
    assert result["deleted"]["images"]["batches"] == 1
    assert not meta.exists()


def test_finished_batch_is_forgotten_by_the_live_generator(seeded, sidecar_down, monkeypatch):
    live = SimpleNamespace(
        active_batches={"VideoBatch_y": SimpleNamespace(status="completed")},
        batch_lock=threading.Lock(),
        cancel_events={"VideoBatch_y": object()},
        queue_order=["VideoBatch_y"],
    )
    monkeypatch.setattr(svc, "_generator_instance", lambda kind: live if kind == "videos" else None)
    svc.delete_generation_history(upload_dir=seeded)
    assert live.active_batches == {} and live.cancel_events == {} and live.queue_order == []


def test_sidecar_up_keeps_its_active_job_record(seeded, no_generators, monkeypatch):
    calls = []

    class _Resp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"removed": 1, "removed_ids": [HEX_A], "active_ids": [HEX_B]}

    def _delete(url, timeout):
        calls.append(url)
        # The sidecar removed its own finished record before answering.
        (seeded / "Audio" / ".jobs" / f"{HEX_A}.json").unlink()
        return _Resp()

    monkeypatch.setattr(requests, "delete", _delete)
    result = svc.delete_generation_history(upload_dir=seeded)

    assert calls == [f"{svc._sidecar_url()}/jobs"]
    assert result["sidecar_available"] is True
    assert result["skipped"]["audio"] == [HEX_B]
    assert result["deleted"]["audio"]["jobs"] == 1
    assert (seeded / "Audio" / ".jobs" / f"{HEX_B}.json").exists()
    assert not (seeded / "Audio" / f"{HEX_A}.wav").exists()


def test_sidecar_error_leaves_job_records_alone_and_is_reported(seeded, no_generators, monkeypatch):
    class _Resp:
        status_code = 500
        def raise_for_status(self):
            raise requests.HTTPError("500 boom")
        def json(self):
            return {}

    monkeypatch.setattr(requests, "delete", lambda url, timeout: _Resp())
    result = svc.delete_generation_history(upload_dir=seeded)
    assert result["success"] is False
    assert any(e.startswith("audio sidecar") for e in result["errors"])
    assert len(list((seeded / "Audio" / ".jobs").glob("*.json"))) == 2
    # Everything else still ran.
    assert result["deleted"]["images"]["batches"] == 1
    assert result["deleted"]["documents"] == 3


def test_empty_tree_is_a_no_op(app, tmp_path, no_generators, sidecar_down):
    result = svc.delete_generation_history(upload_dir=tmp_path)
    assert result["success"] and result["bytes_freed"] == 0
    assert RetentionAudit.query.count() == 0
