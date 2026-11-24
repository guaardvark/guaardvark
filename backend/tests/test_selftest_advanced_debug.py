"""POST /api/meta/selftest with advanced debug on and web access off.

The outdated-packages check is skipped when web access is off, and its
subprocess result was then read anyway by the debug lines, ending the
self-test in a 500 after every check had run.
"""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.api import diagnostics_api
from backend.models import Setting, db
from backend.utils import settings_utils


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(diagnostics_api, "local_imports_ok", True)
    monkeypatch.setattr(diagnostics_api, "llama_index_available", True)
    monkeypatch.setattr(diagnostics_api, "_gather_diagnostics", lambda: {})
    monkeypatch.setattr(settings_utils, "web_access_block_reason", lambda _why: "web access is off")
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(diagnostics_api.diagnostics_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Setting(key="advanced_debug", value="true"))
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_web_access_off_still_completes(client, monkeypatch):
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="No broken requirements found.", stderr="")

    monkeypatch.setattr(diagnostics_api.subprocess, "run", fake_run)

    resp = client.post("/api/meta/selftest", json={"mode": "basic"})

    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    legacy = resp.get_json()["results"]["legacy_diagnostics"]
    assert legacy["pip_outdated"].startswith("Skipped")
    assert not any("--outdated" in c for c in calls)


def test_a_timed_out_pip_check_still_completes(client, monkeypatch):
    def timeout(cmd, **_kw):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(diagnostics_api.subprocess, "run", timeout)

    resp = client.post("/api/meta/selftest", json={"mode": "basic"})

    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    assert resp.get_json()["results"]["legacy_diagnostics"]["pip_check"].startswith("Timeout")
