"""resume_pending_tick on a file from which no text can be read.

The tick indexes each row through index_one_document, the routine every
indexing path shares. A falsy add_file_to_index means the environment failed
(embedding service, index not loaded) or the file holds nothing to index; only
the second records no_content_reason. An environment failure leaves the row
PENDING for a later tick; a file with nothing to read ends ERROR with the
reason, as it does from the upload path.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import celery_tasks_isolated as iso
from backend import config
from backend.models import Document as DBDocument, db
from backend.services import indexing_service as isvc
from backend.tasks import index_resume_tasks as mod


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_INDEX_RESUME_EXTENSIONS", ".txt")
    monkeypatch.setenv("GUAARDVARK_INDEX_AUTO_RESUME", "true")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setenv("GUAARDVARK_UPLOAD_DIR", str(uploads))
    # A file database: index_one_document writes through its own engine, which
    # has to see the rows the Flask session wrote.
    url = f"sqlite:///{tmp_path / 'resume.db'}"
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": url})
    db.init_app(app)
    engine = create_engine(url)
    monkeypatch.setattr(iso, "_engine", engine)
    monkeypatch.setattr(iso, "_SessionFactory", sessionmaker(bind=engine))
    # The tick and add_file_to_index_by_id build their app context from backend.app.
    monkeypatch.setitem(sys.modules, "backend.app",
                        types.SimpleNamespace(get_or_create_app=lambda: app))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(isvc, "is_indexing_paused", lambda: False)
    monkeypatch.setattr(isvc, "_under_resource_pressure", lambda: False)
    monkeypatch.setattr(isvc, "vector_store_fallback_reason", lambda: None)
    monkeypatch.setattr(mod, "_embedding_service_reachable", lambda: True)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
    engine.dispose()


def _pending(tmp_path, name, data=b"some text"):
    (tmp_path / "uploads" / name).write_bytes(data)
    doc = DBDocument(filename=name, path=name, type=".txt", index_status="PENDING")
    db.session.add(doc)
    db.session.commit()
    return doc.id


def _row(doc_id):
    db.session.expire_all()
    return db.session.get(DBDocument, doc_id)


def test_a_file_with_no_text_ends_error_with_its_reason(app, tmp_path, monkeypatch):
    doc_id = _pending(tmp_path, "blob.txt")

    def no_text(path, doc):
        isvc._NO_CONTENT_REASONS[str(doc.id)] = "nothing to index: no text could be read from blob.txt"
        return False
    monkeypatch.setattr(isvc, "add_file_to_index", no_text)
    monkeypatch.setattr(isvc, "_NO_CONTENT_REASONS", {})

    result = mod.resume_pending_tick()

    row = _row(doc_id)
    assert row.index_status == "ERROR"
    assert row.error_message == "nothing to index: no text could be read from blob.txt"
    assert result["no_text"] == 1 and result["failed"] == 0


def test_a_binary_file_ends_error_without_reaching_the_indexer(app, tmp_path, monkeypatch):
    doc_id = _pending(tmp_path, "blob.txt", b"\x00\x01binary")
    monkeypatch.setattr(isvc, "add_file_to_index",
                        lambda path, doc: pytest.fail("a binary file was sent to the indexer"))

    result = mod.resume_pending_tick()

    row = _row(doc_id)
    assert row.index_status == "ERROR"
    assert row.error_message == "Compiled or binary file: there is no text to read"
    assert result["no_text"] == 1 and result["failed"] == 0


def test_an_environment_failure_still_leaves_the_row_pending(app, tmp_path, monkeypatch):
    doc_id = _pending(tmp_path, "notes.txt", b"hello")
    monkeypatch.setattr(isvc, "_NO_CONTENT_REASONS", {})
    monkeypatch.setattr(isvc, "add_file_to_index", lambda path, doc: False)

    result = mod.resume_pending_tick()

    row = _row(doc_id)
    assert row.index_status == "PENDING"
    assert row.error_message == f"Indexing failed for document {doc_id}"
    assert result["failed"] == 1 and result["no_text"] == 0


def test_a_store_not_in_use_leaves_the_row_pending_and_stops_the_batch(app, tmp_path, monkeypatch):
    doc_id = _pending(tmp_path, "notes.txt", b"hello")
    monkeypatch.setattr(isvc, "_NO_CONTENT_REASONS", {})
    monkeypatch.setattr(isvc, "add_file_to_index", lambda path, doc: False)
    monkeypatch.setattr(isvc, "vector_store_fallback_reason", lambda: "it is unavailable")

    result = mod.resume_pending_tick()

    row = _row(doc_id)
    assert row.index_status == "PENDING"
    assert row.error_message == "Waiting for the vector store: it is unavailable"
    assert result["indexed"] == 0 and result["failed"] == 0
    assert "stopped" in result
