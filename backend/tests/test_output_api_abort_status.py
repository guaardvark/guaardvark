"""The tracking-file routes answer with the status they abort with.

abort() raises an HTTPException inside a try whose handler catches
Exception; without a re-raise a missing file or a refused name became 500.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.api import output_api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(output_api, "TRACKING_DIR", str(tmp_path))
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(output_api.output_bp)
    return app.test_client()


@pytest.mark.parametrize("method,url", [
    ("GET", "/api/outputs/missing.json"),
    ("GET", "/api/outputs/missing.json/csv"),
    ("GET", "/api/outputs/missing.json/xml"),
    ("DELETE", "/api/outputs/missing.json"),
    ("GET", "/api/outputs/missing.json/merged-csv"),
])
def test_a_missing_tracking_file_is_404(client, method, url):
    assert client.open(url, method=method).status_code == 404


@pytest.mark.parametrize("method,url", [
    ("GET", "/api/outputs/notes.txt"),
    ("GET", "/api/outputs/notes.txt/csv"),
    ("DELETE", "/api/outputs/notes.txt"),
])
def test_a_refused_name_is_400(client, method, url):
    assert client.open(url, method=method).status_code == 400
