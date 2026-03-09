import pytest

try:
    from flask import Flask

    from backend.api.jobs_api import jobs_bp
    from backend.models import db
    from backend.utils import progress_manager
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "OUTPUT_DIR": str(tmp_path),
        }
    )
    db.init_app(app)
    if jobs_bp.name not in app.blueprints:
        app.register_blueprint(jobs_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_job_status_includes_percent(client, tmp_path):
    output_dir = str(tmp_path)
    with client.application.app_context():
        job_id = progress_manager.start_job(output_dir, "test.csv")
    meta = progress_manager.get_job_metadata(output_dir, job_id)
    assert meta["job_id"] == job_id
    processed = meta.get("processed_item_count", 0)
    total = meta.get("total_items_expected") or 0
    percent = int((processed / total) * 100) if total else 0
    assert percent == 0


def test_update_job_status_sets_total_and_percent(client, tmp_path):
    output_dir = str(tmp_path)
    with client.application.app_context():
        job_id = progress_manager.start_job(output_dir, "test2.csv")
        progress_manager.update_job_status(
            output_dir, job_id, "RUN", total_items_expected=5
        )
        progress_manager.log_item_processed(output_dir, job_id, "i1", {"v": 1}, "m")
        progress_manager.log_item_processed(output_dir, job_id, "i2", {"v": 2}, "m")

    meta = progress_manager.get_job_metadata(output_dir, job_id)
    assert meta["total_items_expected"] == 5
    assert meta["processed_item_count"] == 2
    percent = int((meta["processed_item_count"] / meta["total_items_expected"]) * 100)
    assert percent == 40


def test_update_job_status_marks_complete(client, tmp_path):
    output_dir = str(tmp_path)
    with client.application.app_context():
        job_id = progress_manager.start_job(output_dir, "done.csv")
        progress_manager.update_job_status(
            output_dir, job_id, "FINISHED", is_complete=True, total_items_expected=1
        )

    meta = progress_manager.get_job_metadata(output_dir, job_id)
    assert meta["is_complete"] is True
    assert meta["job_status"] == "FINISHED"
