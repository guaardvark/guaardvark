"""Generated audio and imported files never take each other's place in Files.

Audio Foundry writes a clip under its own id in uploads/Audio (its config.yaml
``output.dir``) and registers it with a display name; a spoken clip is named
after its first words. An import into the same folder must not hide it, and it
must not hide an import.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

import backend.config as config
import backend.services.output_registration as oreg
from backend.models import Document, Folder, db
from backend.utils.filename_resolver import resolve_storage_filename


@pytest.fixture
def lib(tmp_path, monkeypatch):
    uploads, outputs = tmp_path / "uploads", tmp_path / "outputs"
    (uploads / "Audio" / "Voice").mkdir(parents=True)
    outputs.mkdir()
    monkeypatch.setattr(oreg, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", tmp_path)

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield uploads
        db.session.remove()
        db.drop_all()


def _voice_folder():
    audio = Folder.query.filter_by(path="Audio").first() or Folder(name="Audio", path="Audio")
    db.session.add(audio)
    db.session.flush()
    voice = Folder.query.filter_by(path="Audio/Voice").first() or Folder(
        name="Voice", path="Audio/Voice", parent_id=audio.id)
    db.session.add(voice)
    db.session.commit()
    return voice


def _import(uploads: Path, name: str) -> Document:
    """What the upload service does: a free name, the file stored under it, a row."""
    folder = _voice_folder()
    chosen = resolve_storage_filename(folder.id, uploads / "Audio" / "Voice", "Audio/Voice", name,
                                      db.session, Document)
    (uploads / "Audio" / "Voice" / chosen).write_bytes(b"imported audio")
    row = Document(filename=chosen, path=f"Audio/Voice/{chosen}", folder_id=folder.id, type=".wav")
    db.session.add(row)
    db.session.commit()
    return row


def _generate(uploads: Path, asset_id: str, display: str) -> Document:
    clip = uploads / "Audio" / "Voice" / f"{asset_id}.wav"
    clip.write_bytes(b"generated " + asset_id.encode())
    return oreg.register_file(str(clip), folder_name="Audio", subfolder_name="Voice", filename=display)


def test_a_generated_clip_beside_an_import_of_the_same_name_gets_its_own_row(lib):
    uploads = lib
    imported = _import(uploads, "Hello there.wav")

    generated = _generate(uploads, "a1b2", "Hello there.wav")

    assert generated is not None
    assert generated.id != imported.id
    assert generated.filename == "Hello there (2).wav"
    assert (uploads / "Audio" / "Voice" / "Hello there.wav").read_bytes() == b"imported audio"


def test_two_generated_clips_with_the_same_words_are_both_filed(lib):
    uploads = lib
    first = _generate(uploads, "a1b2", "Hello there.wav")
    second = _generate(uploads, "c3d4", "Hello there.wav")

    assert first is not None and second is not None
    assert first.id != second.id
    assert {first.filename, second.filename} == {"Hello there.wav", "Hello there (2).wav"}


def test_registering_the_same_clip_again_returns_its_row(lib):
    uploads = lib
    first = _generate(uploads, "a1b2", "Hello there.wav")
    again = oreg.register_file(str(uploads / "Audio" / "Voice" / "a1b2.wav"), folder_name="Audio",
                               subfolder_name="Voice", filename="Hello there.wav")

    assert again is not None and again.id == first.id
    assert Document.query.count() == 1


def test_an_import_after_a_generated_clip_takes_the_next_name(lib):
    uploads = lib
    generated = _generate(uploads, "a1b2", "Hello there.wav")

    imported = _import(uploads, "Hello there.wav")

    assert generated.filename == "Hello there.wav"
    assert imported.filename == "Hello there (2).wav"
