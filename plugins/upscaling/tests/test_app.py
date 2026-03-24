"""Integration tests for the FastAPI app.

Uses httpx TestClient. Model-dependent tests are skipped if no GPU.
"""
import pytest
from fastapi.testclient import TestClient

# Mock torch.cuda before importing app to allow CPU-only test runs
import torch
if not torch.cuda.is_available():
    torch.cuda.is_available = lambda: False

# Fix 11: Import AUTH_TOKEN from service.auth, not _auth_token from app
from service.app import app
from service.auth import AUTH_TOKEN

AUTH_HEADER = {"Authorization": f"Bearer {AUTH_TOKEN}"}


@pytest.fixture(scope="module")
def client():
    """TestClient as context manager triggers lifespan events."""
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "gpu" in data
    assert "auth_token" in data  # Fix 2 verification


def test_models_endpoint(client):
    resp = client.get("/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "downloaded" in data
    assert "available" in data


def test_config_endpoint(client):
    resp = client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "default_model" in data


def test_jobs_endpoint_empty(client):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_upscale_image_requires_auth(client):
    """POST endpoints require bearer token."""
    resp = client.post("/upscale/image", json={"input_path": "/fake", "output_path": "/fake_out"})
    assert resp.status_code == 401


def test_upscale_video_requires_auth(client):
    resp = client.post("/upscale/video", json={"input_path": "/fake"})
    assert resp.status_code == 401


def test_upscale_video_validates_input(client):
    resp = client.post(
        "/upscale/video",
        json={"input_path": "/nonexistent/video.mp4"},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 400


def test_job_not_found(client):
    resp = client.get("/jobs/nonexistent")
    assert resp.status_code == 404


def test_cancel_job_requires_auth(client):
    resp = client.delete("/jobs/someid")
    assert resp.status_code == 401
