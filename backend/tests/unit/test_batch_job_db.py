import csv
import os

import pytest

from backend.utils import batch_job_db, progress_manager

CSV_HEADERS = ["A", "B"]


def test_insert_and_export(tmp_path):
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
    batch_job_db.cleanup_db(output_dir, job_id)


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
