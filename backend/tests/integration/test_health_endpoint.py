import pytest

try:
    from backend.app import app
except Exception:
    pytest.skip("Flask not available", allow_module_level=True)


def test_health_route():
    with app.test_client() as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("status") == "ok"
