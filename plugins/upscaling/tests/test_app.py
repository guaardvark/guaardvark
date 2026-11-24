"""Integration tests for the FastAPI app.

Uses httpx TestClient. Model-dependent tests are skipped if no GPU.
"""
import pytest
from fastapi.testclient import TestClient

# Mock torch.cuda before importing app to allow CPU-only test runs
import torch
if not torch.cuda.is_available():
    torch.cuda.is_available = lambda: False

from service.app import app
from service.auth import auth_token

AUTH_HEADER = {"Authorization": f"Bearer {auth_token()}"}


@pytest.fixture(scope="module")
def client():
    """TestClient as context manager triggers lifespan events. The base URL
    is an address the service's Host check answers."""
    with TestClient(app, base_url="http://127.0.0.1:8202") as c:
        yield c


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "gpu" in data
    # The backend relays /health to browsers; the token stays in its file.
    assert "auth_token" not in data
    assert auth_token() not in resp.text


def test_a_rebound_name_is_refused(client):
    resp = client.get("/health", headers={"Host": "evil.example:8202"})
    assert resp.status_code == 421


def test_models_endpoint(client):
    resp = client.get("/models", headers=AUTH_HEADER)
    assert resp.status_code == 200
    data = resp.json()
    assert "downloaded" in data
    assert "available" in data


def test_config_endpoint(client):
    resp = client.get("/config", headers=AUTH_HEADER)
    assert resp.status_code == 200
    data = resp.json()
    assert "default_model" in data


def test_jobs_endpoint_empty(client):
    resp = client.get("/jobs", headers=AUTH_HEADER)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.parametrize("method, path", [
    ("GET", "/models"), ("GET", "/config"), ("GET", "/jobs"), ("GET", "/jobs/x"),
    ("POST", "/models/download"), ("PUT", "/config"), ("DELETE", "/jobs"),
])
def test_every_route_but_health_needs_the_token(client, method, path):
    resp = client.request(method, path)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    wrong = client.request(method, path, headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 401


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
    resp = client.get("/jobs/nonexistent", headers=AUTH_HEADER)
    assert resp.status_code == 404


def test_cancel_job_requires_auth(client):
    resp = client.delete("/jobs/someid")
    assert resp.status_code == 401


def test_upscale_images_requires_auth(client):
    resp = client.post(
        "/upscale/images",
        json={"inputs": ["/fake.png"], "output_dir": "/tmp/out"},
    )
    assert resp.status_code == 401


def test_upscale_images_rejects_empty_input_list(client):
    resp = client.post(
        "/upscale/images",
        json={"inputs": [], "output_dir": "/tmp/out"},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 400


def test_upscale_images_validates_inputs_exist(client):
    resp = client.post(
        "/upscale/images",
        json={"inputs": ["/nonexistent/frame.png"], "output_dir": "/tmp/out"},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 400


def _empty_models_dir(monkeypatch, tmp_path):
    """Point the service at an empty models dir; any download fails the test."""
    import service.app as app_module
    import service.model_manager as model_manager_module
    from service.model_manager import ModelManager

    def _no_download(*_args, **_kwargs):
        raise AssertionError("an upscale request must not download a model")

    monkeypatch.setattr(model_manager_module, "urlretrieve", _no_download)
    monkeypatch.setattr(
        app_module,
        "_model_manager",
        ModelManager(models_dir=str(tmp_path / "models"), precision="fp32", compile_enabled=False),
    )
    return app_module


def test_upscale_image_refuses_a_model_that_is_not_installed(client, monkeypatch, tmp_path):
    import cv2
    import numpy as np

    _empty_models_dir(monkeypatch, tmp_path)
    source = tmp_path / "in.png"
    cv2.imwrite(str(source), np.zeros((8, 8, 3), np.uint8))

    resp = client.post(
        "/upscale/image",
        json={
            "input_path": str(source),
            "output_path": str(tmp_path / "out.png"),
            "model": "RealESRGAN_x2plus",
        },
        headers=AUTH_HEADER,
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "'RealESRGAN_x2plus' is not installed" in detail
    assert "Manage Upscaling Models" in detail
    assert not (tmp_path / "out.png").exists()


def test_upscale_video_refuses_the_default_model_before_queueing(client, monkeypatch, tmp_path):
    app_module = _empty_models_dir(monkeypatch, tmp_path)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"not decoded: the refusal comes first")

    resp = client.post("/upscale/video", json={"input_path": str(source)}, headers=AUTH_HEADER)

    assert resp.status_code == 409
    assert f"'{app_module._config.default_model}' is not installed" in resp.json()["detail"]
    jobs = client.get("/jobs", headers=AUTH_HEADER).json()
    assert all(job["input_path"] != str(source) for job in jobs)


def test_a_request_without_a_model_uses_an_installed_one_when_the_default_is_missing(client, monkeypatch, tmp_path):
    app_module = _empty_models_dir(monkeypatch, tmp_path)
    assert app_module._default_model() == app_module._config.default_model
    # A stand-in of the published size: a shorter legacy .pth reads as cut short.
    with open(tmp_path / "models" / "4x-UltraSharp.pth", "wb") as f:
        f.write(b"weights")
        f.truncate(66961958)
    assert app_module._default_model() == "4x-UltraSharp"
    assert app_module._require_installed(None) == "4x-UltraSharp"


def test_a_queued_job_records_the_refusal_as_its_error(client, monkeypatch, tmp_path):
    """Watch-folder jobs reach the worker without the request check."""
    import functools
    import threading

    app_module = _empty_models_dir(monkeypatch, tmp_path)
    # The worker's free-VRAM check would fail first on a busy card.
    monkeypatch.setattr(
        torch.cuda, "is_available", functools.wraps(torch.cuda.is_available)(lambda: False)
    )
    job = app_module._job_manager.create_job(
        input_path=str(tmp_path / "a.png"),
        output_path=str(tmp_path / "out"),
        model="RealESRGAN_x2plus",
        scale=0,
        kind="image_batch",
    )

    app_module._run_image_batch_job(
        job["job_id"], [str(tmp_path / "a.png")], str(tmp_path / "out"), "RealESRGAN_x2plus",
        None, 0.0, 0.3, False, False, "upscaled", threading.Event(),
    )

    stored = app_module._job_manager.get_job(job["job_id"])
    assert stored["status"] == "failed"
    assert "'RealESRGAN_x2plus' is not installed" in stored["error"]


def test_atomic_imwrite_keeps_the_target_extension(tmp_path):
    """OpenCV picks its encoder off the final extension, so the temp file keeps it."""
    import numpy as np
    from service.app import _atomic_imwrite

    target = tmp_path / "still_upscaled.png"
    assert _atomic_imwrite(str(target), np.zeros((4, 4, 3), np.uint8)) is True
    assert target.read_bytes().startswith(b"\x89PNG")
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_imwrite_creates_missing_directories(tmp_path):
    import numpy as np
    from service.app import _atomic_imwrite

    target = tmp_path / "nested" / "out.png"
    assert _atomic_imwrite(str(target), np.zeros((4, 4, 3), np.uint8)) is True
    assert target.exists()

