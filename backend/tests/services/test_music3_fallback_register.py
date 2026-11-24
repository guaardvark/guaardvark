"""A Music 3 song leaves the video cache for a folder it can be registered
from, also when uploads refuses it. In-memory SQLite, scratch install root."""

from __future__ import annotations

import shutil

import pytest
from flask import Flask

from backend import config
from backend.models import Document, db
from backend.services import comfyui_music_generator as m3
from backend.services import output_registration as reg
from backend.services.document_path_resolver import resolve_document_path


@pytest.fixture
def app(monkeypatch, tmp_path):
    uploads, outputs = tmp_path / "data" / "uploads", tmp_path / "data" / "outputs"
    uploads.mkdir(parents=True)
    outputs.mkdir(parents=True)
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(reg, "UPLOAD_DIR", str(uploads))
    flask_app = Flask(__name__)
    flask_app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                            UPLOAD_FOLDER=str(uploads))
    db.init_app(flask_app)
    with flask_app.app_context():
        db.create_all()
        reg.ensure_default_folders()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def song(tmp_path):
    cache = tmp_path / "data" / "cache" / "generated_videos" / "music"
    cache.mkdir(parents=True)
    path = cache / "minimax_music3_ab12cd34_00001_.flac"
    path.write_bytes(b"fLaC song")
    return path


def _result(path):
    return {"success": True, "path": str(path), "model": "minimax-music3-int8", "seconds": 30,
            "seed": 7, "steps": 30, "attribution": "MiniMax-Music3"}


def test_a_song_moves_into_uploads_audio_and_is_registered(app, tmp_path, song):
    moved = m3._move_to_audio_folder(str(song))
    doc_id = m3._register(app, _result(moved), {"caption": "folk"})

    doc = db.session.get(Document, doc_id)
    assert doc.path == f"Audio/{song.name}"
    assert resolve_document_path(doc).read_bytes() == b"fLaC song"
    assert not song.exists()


def test_a_song_uploads_refuses_lands_in_the_audio_output_folder_and_is_registered(
        app, tmp_path, song, monkeypatch):
    real_move = shutil.move
    uploads = str(tmp_path / "data" / "uploads")

    def no_uploads(src, dst):
        if str(dst).startswith(uploads):
            raise OSError(28, "No space left on device")
        return real_move(src, dst)

    monkeypatch.setattr(shutil, "move", no_uploads)
    moved = m3._move_to_audio_folder(str(song))
    doc_id = m3._register(app, _result(moved), {"caption": "folk"})

    assert moved == str(tmp_path / "data" / "outputs" / "audio" / song.name)
    doc = db.session.get(Document, doc_id)
    assert doc.path == f"data/outputs/audio/{song.name}"
    assert resolve_document_path(doc).read_bytes() == b"fLaC song"
    from backend.services.media_recipe import list_audio
    assert [row["document_id"] for row in list_audio()] == [doc_id]


def test_a_song_left_in_the_cache_is_not_registered(app, song, monkeypatch):
    def nowhere(src, dst):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(shutil, "move", nowhere)
    moved = m3._move_to_audio_folder(str(song))

    assert moved == str(song)
    assert m3._register(app, _result(moved), {"caption": "folk"}) is None
    assert Document.query.count() == 0
