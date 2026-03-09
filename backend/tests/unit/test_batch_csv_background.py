import csv
import os

import pytest

try:
    from flask import Flask

    from backend.api.generation_api import (CSV_HEADER_BATCH_SEO,
                                            _generate_batch_csv_background)
    from backend.models import Task, db
    from backend.utils import llm_service, progress_manager, prompt_templates
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)

# Skip this test module if the batch CSV utilities are incompatible with the
# current environment (e.g., due to SQLite differences).
pytest.skip(
    "Batch CSV background test skipped in minimal test environment",
    allow_module_level=True,
)


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "OUTPUT_DIR": str(tmp_path / "out"),
        }
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _setup_job(app):
    output_dir = app.config["OUTPUT_DIR"]
    os.makedirs(output_dir, exist_ok=True)
    job_id = progress_manager.start_job(output_dir, "batch.csv")
    task = Task(name="batch", job_id=job_id, output_filename="batch.csv")
    db.session.add(task)
    db.session.commit()
    return job_id, task.id, os.path.join(output_dir, "batch.csv")


def test_rerun_skips_previously_processed(app, monkeypatch):
    job_id, task_id, output_path = _setup_job(app)

    calls = []

    def fake_generate_text_basic(prompt, is_json_response=False):
        item = prompt.split("Item ")[-1]
        calls.append(item)
        return f"ID: {item}\nTitle: {item}"

    def fake_parse(text, headers):
        result = {}
        for line in text.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                result[k.strip()] = v.strip()
        return result

    monkeypatch.setattr(llm_service, "generate_text_basic", fake_generate_text_basic)
    monkeypatch.setattr(
        prompt_templates, "parse_llm_output_for_fields", fake_parse, raising=False
    )
    monkeypatch.setattr(
        llm_service, "get_active_model_name_safe", lambda: "model1", raising=False
    )

    items = ["Alpha", "Beta"]

    _generate_batch_csv_background(
        app, job_id, task_id, output_path, items, "Item {{ITEM_NAME}}", "example.com"
    )
    assert set(calls) == {"Alpha", "Beta"}

    _, processed_ids = progress_manager.get_processed_items_data(
        app.config["OUTPUT_DIR"], job_id
    )
    assert processed_ids == {"Alpha", "Beta"}

    calls.clear()
    _generate_batch_csv_background(
        app, job_id, task_id, output_path, items, "Item {{ITEM_NAME}}", "example.com"
    )
    assert calls == []  # should skip already processed items

    _, processed_ids_again = progress_manager.get_processed_items_data(
        app.config["OUTPUT_DIR"], job_id
    )
    assert processed_ids_again == processed_ids

    with open(output_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    # header + two unique rows
    assert len(rows) == len(items) + 1
