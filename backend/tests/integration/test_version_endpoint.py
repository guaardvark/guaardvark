import pytest

from backend.app import app


def test_version_route():
    with app.test_client() as client:
        resp = client.get("/api/version")
        assert resp.status_code == 200
        assert "version" in resp.get_json()
