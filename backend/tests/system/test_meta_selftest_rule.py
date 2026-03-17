import os

import pytest

try:
    from flask import Flask
    from tests.helpers import make_mock_llm

    from backend.api import diagnostics_api
    from backend.api.diagnostics_api import diagnostics_bp
    from backend.models import Rule, db
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)
else:
    if getattr(diagnostics_api, "prompt_utils", None) is None:
        pytest.skip("prompt_utils missing", allow_module_level=True)

# Skip if LlamaIndex is unavailable, since diagnostics_api relies on it
if getattr(diagnostics_api, "Settings", None) is None:
    pytest.skip("llama_index package missing", allow_module_level=True)


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = Flask(__name__)
    storage_dir = tmp_path / "storage"
    upload_dir = tmp_path / "upload"
    output_dir = tmp_path / "output"
    storage_dir.mkdir()
    upload_dir.mkdir()
    output_dir.mkdir()
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "STORAGE_DIR": str(storage_dir),
            "UPLOAD_FOLDER": str(upload_dir),
            "OUTPUT_DIR": str(output_dir),
        }
    )
    db.init_app(app)
    if diagnostics_bp.name not in app.blueprints:
        app.register_blueprint(diagnostics_bp)
    with app.app_context():
        db.create_all()
        monkeypatch.setattr(
            "backend.api.diagnostics_api.get_available_ollama_models", lambda: []
        )
        monkeypatch.setattr(
            "backend.api.diagnostics_api.prompt_utils.get_prompt_text_by_name",
            lambda *a, **k: "Answer {context_str} {query_str}",
        )

        os.environ["IS_TESTING"] = "1"
        diagnostics_api.Settings.llm = "default"
        monkeypatch.setattr(
            "backend.utils.llm_service.run_llm_chat_prompt",
            lambda *a, **k: "hi",
        )
        yield app.test_client()
        db.session.remove()
        db.drop_all()


@pytest.mark.rules
@pytest.mark.db
@pytest.mark.xfail(
    reason="Backend no longer self-heals duplicate rules at runtime, only warns. This matches new critical policy."
)
def test_selftest_heals_duplicate_rule(client):
    with client.application.app_context():
        r1 = Rule(
            name="qa_default",
            level="SYSTEM",
            type="QA_TEMPLATE",
            rule_text="A",
            is_active=True,
        )
        r2 = Rule(
            name="qa_default",
            level="SYSTEM",
            type="QA_TEMPLATE",
            rule_text="B",
            is_active=True,
        )
        db.session.add_all([r1, r2])
        db.session.commit()

    resp = client.post("/api/meta/selftest")
    assert resp.status_code == 200

    with client.application.app_context():
        active_rules = (
            db.session.query(Rule)
            .filter_by(
                name="qa_default",
                level="SYSTEM",
                type="QA_TEMPLATE",
                is_active=True,
            )
            .all()
        )
        assert len(active_rules) == 1
