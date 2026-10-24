"""Changing the embedding model clears the old vectors and indexes again.

A switch to a model of the same width (embeddinggemma to embeddinggemma-2,
both 768) kept the index, so the new model searched the old model's vectors.
Search then switched itself off on the model mismatch and nothing re-indexed.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.api import indexing_api
from backend.models import Document as DBDocument, db
from backend.services import indexing_service


class _Cursor:
    def __init__(self, tables, ran):
        self._tables, self._ran = tables, ran

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._ran.append(sql)

    def fetchall(self):
        return [(t,) for t in self._tables]


class _Conn:
    autocommit = False

    def __init__(self, tables, ran):
        self._tables, self._ran = tables, ran

    def cursor(self):
        return _Cursor(self._tables, self._ran)

    def close(self):
        pass


@pytest.fixture
def pg(monkeypatch):
    tables = ["data_guaardvark_global_768", "data_guaardvark_global_2560",
              "data_guaardvark_research_global_768"]
    ran = []
    monkeypatch.setattr(indexing_service, "_vector_backend", lambda: "pgvector")
    monkeypatch.setattr(indexing_service, "_pg_connect", lambda: _Conn(tables, ran))
    return ran


def test_tables_of_the_width_are_emptied_not_dropped(pg):
    result = indexing_service.empty_vector_tables(768)
    assert result["emptied"] == ["data_guaardvark_global_768", "data_guaardvark_research_global_768"]
    assert [s for s in pg if s.startswith(("TRUNCATE", "DROP"))] == [
        'TRUNCATE TABLE "data_guaardvark_global_768"',
        'TRUNCATE TABLE "data_guaardvark_research_global_768"',
    ]


def test_a_reset_drops_other_widths_and_keeps_the_active_one(pg):
    result = indexing_service.drop_all_vector_stores(keep_width=768)
    assert result["dropped"] == ["data_guaardvark_global_2560"]


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


def _doc(name, status):
    doc = DBDocument(filename=name, path=name, type=os.path.splitext(name)[1], index_status=status)
    db.session.add(doc)
    db.session.commit()
    return doc


@pytest.fixture
def dispatch(monkeypatch):
    """Records what is handed to the one catch-up; nothing reaches Redis."""
    sent = []
    from backend.tasks import index_resume_tasks
    monkeypatch.setattr(indexing_api, "_get_indexing_paused_setting", lambda: False)
    monkeypatch.setattr(index_resume_tasks, "_job_is_live", lambda job_id: False)

    def fake_start(ids):
        sent.extend(ids)
        return "job-1", None

    monkeypatch.setattr(index_resume_tasks, "start_manual_drain", fake_start)
    return sent


def test_indexed_documents_are_queued_again(app, dispatch):
    a = _doc("a.md", "INDEXED")
    b = _doc("b.txt", "INDEXED")
    media = _doc("frame.png", "STORED")
    backlog = _doc("c.py", "PENDING")

    assert indexing_api.requeue_indexed_documents() == {"queued": 2, "pending": 0}
    assert dispatch == [a.id, b.id]
    assert db.session.get(DBDocument, a.id).index_status == "PENDING"
    assert db.session.get(DBDocument, media.id).index_status == "STORED"
    assert db.session.get(DBDocument, backlog.id).index_status == "PENDING"


def test_while_paused_they_are_left_pending(app, dispatch, monkeypatch):
    monkeypatch.setattr(indexing_api, "_get_indexing_paused_setting", lambda: True)
    a = _doc("a.md", "INDEXED")

    assert indexing_api.requeue_indexed_documents() == {"queued": 0, "pending": 1}
    assert dispatch == []
    assert db.session.get(DBDocument, a.id).index_status == "PENDING"


def test_a_catch_up_that_cannot_start_leaves_them_pending(app, dispatch, monkeypatch):
    from backend.tasks import index_resume_tasks
    a = _doc("a.md", "INDEXED")
    b = _doc("b.md", "INDEXED")
    monkeypatch.setattr(index_resume_tasks, "start_manual_drain",
                        lambda ids: ("job-1", RuntimeError("redis down")))

    assert indexing_api.requeue_indexed_documents() == {"queued": 0, "pending": 2}
    assert db.session.get(DBDocument, a.id).index_status == "PENDING"
    assert db.session.get(DBDocument, b.id).index_status == "PENDING"


def test_a_new_index_requeues_every_document_that_belongs_in_it(app, dispatch):
    """After a switch empties the tables, a failed or running document is not
    left out of the new index. A waiting one stays waiting for the resume beat;
    those never meant to be searchable are left alone."""
    keep = [_doc("a.md", "INDEXED"), _doc("b.pdf", "ERROR"), _doc("d.txt", "INDEXING")]
    waiting = _doc("c.py", "PENDING")
    media = _doc("frame.png", "STORED")
    skipped = _doc("e.md", "NOT_INDEXED")

    out = indexing_api.requeue_documents_for_new_index()
    assert out["queued"] == 3 and out["pending"] == 0
    assert out["by_status"] == {"INDEXED": 1, "ERROR": 1, "INDEXING": 1}
    assert dispatch == [d.id for d in keep]
    assert all(db.session.get(DBDocument, d.id).index_status == "PENDING" for d in keep)
    assert db.session.get(DBDocument, waiting.id).index_status == "PENDING"
    assert db.session.get(DBDocument, media.id).index_status == "STORED"
    assert db.session.get(DBDocument, skipped.id).index_status == "NOT_INDEXED"


def test_a_document_a_live_task_is_indexing_is_left_to_it(app, dispatch, monkeypatch):
    from backend.tasks import index_resume_tasks
    running = _doc("live.md", "INDEXING")
    running.indexing_job_id = "live-job"
    db.session.commit()
    monkeypatch.setattr(index_resume_tasks, "_job_is_live", lambda job_id: job_id == "live-job")

    out = indexing_api.requeue_documents_for_new_index()
    assert out["queued"] == 0
    assert db.session.get(DBDocument, running.id).index_status == "INDEXING"


class _CountCursor(_Cursor):
    """Answers the table list, then each table's not-rebuilt rows by kind."""

    def __init__(self, tables, ran, groups):
        super().__init__(tables, ran)
        self._groups = groups

    def fetchall(self):
        last = self._ran[-1]
        if "GROUP BY" in last:
            table = last.split('FROM "')[1].split('"')[0]
            return self._groups.get(table, [])
        return super().fetchall()


def test_what_a_rebuild_will_not_restore_is_counted_with_how(monkeypatch):
    ran = []
    tables = ["data_guaardvark_global_768", "data_guaardvark_7_768", "data_guaardvark_global_2560"]
    groups = {"data_guaardvark_global_768": [("raptor_summary", 12), ("repository_summary", 2),
                                              ("", 5)],
              "data_guaardvark_7_768": [("raptor_summary", 3)]}
    conn = _Conn(tables, ran)
    conn.cursor = lambda: _CountCursor(tables, ran, groups)
    monkeypatch.setattr(indexing_service, "_vector_backend", lambda: "pgvector")
    monkeypatch.setattr(indexing_service, "_pg_connect", lambda: conn)

    out = {row["kind"]: row for row in indexing_service.rows_not_rebuilt(768)}
    assert out["raptor_summary"]["rows"] == 15 and "build-corpus-summaries" in out["raptor_summary"]["how"]
    assert out["repository_summary"]["rows"] == 2 and "/api/index/bulk" in out["repository_summary"]["how"]
    assert out["other"]["rows"] == 5 and "entity-indexing/index-all" in out["other"]["how"]
    assert not any('global_2560"' in sql for sql in ran)  # other widths are not this index
