import subprocess

import pytest

try:
    from flask import Flask

    from backend.api.codex_analyzer_api import codex_analyzer_api
    from backend.models import db
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "OUTPUT_DIR": str(tmp_path),
        }
    )
    db.init_app(app)
    app.register_blueprint(codex_analyzer_api)
    with app.app_context():
        db.create_all()
        monkeypatch.setattr(
            "backend.api.codex_analyzer_api.run_static_analysis", lambda p: "lint"
        )
        monkeypatch.setattr(
            "backend.api.codex_analyzer_api.run_llamaindex_summary", lambda p: "summary"
        )

        class Result:
            returncode = 0
            stdout = "ok\n/createfile output_file=demo.py code"
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_codex_analyze_returns_content(client):
    resp = client.post("/api/codex/analyze", json={"prompt": "test", "path": str(".")})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result"]
    assert data["suggestions"]
