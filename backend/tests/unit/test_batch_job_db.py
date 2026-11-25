import csv
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.utils import batch_job_db, progress_manager

CSV_HEADERS = ["A", "B"]


@pytest.fixture(autouse=True)
def batch_store(tmp_path, monkeypatch):
    """Point the module's engine at a SQLite file of its own.

    The module's CREATE TABLE statements are PostgreSQL's (SERIAL, JSONB), so
    the two tables are made here in SQLite's spelling; its IF NOT EXISTS then
    leaves them alone.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'batch_jobs.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE batch_job_rows (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_id TEXT NOT NULL, row_data TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "CREATE TABLE batch_job_columns (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_id TEXT UNIQUE NOT NULL, columns TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    monkeypatch.setattr(batch_job_db, "_engine", engine)
    monkeypatch.setattr(batch_job_db, "_SessionFactory", sessionmaker(bind=engine))
    yield engine
    engine.dispose()


def _count(engine, table, job_id):
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE job_id = :j"), {"j": job_id}
        ).scalar()


def test_insert_and_export(tmp_path, batch_store):
    output_dir = str(tmp_path)
    job_id = progress_manager.start_job(output_dir, "out.csv")
    batch_job_db.init_db(output_dir, job_id, CSV_HEADERS)
    batch_job_db.insert_row(output_dir, job_id, CSV_HEADERS, ["1", "2"])
    batch_job_db.insert_row(output_dir, job_id, CSV_HEADERS, ["3", "4"])

    export_path = os.path.join(output_dir, "out.csv")
    files = batch_job_db.export_to_csv(output_dir, job_id, export_path, CSV_HEADERS)
    assert len(files) == 1
    with open(files[0], newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows == [CSV_HEADERS, ["1", "2"], ["3", "4"]]
    assert _count(batch_store, "batch_job_rows", job_id) == 2
    batch_job_db.cleanup_db(output_dir, job_id)
    assert _count(batch_store, "batch_job_rows", job_id) == 0
    assert _count(batch_store, "batch_job_columns", job_id) == 0


def test_export_chunked(tmp_path):
    output_dir = str(tmp_path)
    job_id = progress_manager.start_job(output_dir, "out.csv")
    batch_job_db.init_db(output_dir, job_id, CSV_HEADERS)
    for i in range(3):
        batch_job_db.insert_row(output_dir, job_id, CSV_HEADERS, [str(i), str(i)])

    export_path = os.path.join(output_dir, "out.csv")
    files = batch_job_db.export_to_csv(
        output_dir, job_id, export_path, CSV_HEADERS, row_limit=2
    )
    assert len(files) == 2
    sizes = [os.path.exists(p) for p in files]
    assert all(sizes)
    batch_job_db.cleanup_db(output_dir, job_id)
