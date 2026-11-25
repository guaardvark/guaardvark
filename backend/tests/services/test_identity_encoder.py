"""The identity check's image encoder arrives only by its Install, and only whole.

Pins: reading the status never downloads; Install fetches exactly the declared
files at the pinned commit with no token, into the install folder; weights that
do not match the pinned sha256 are deleted and the install fails; Remove deletes
the folder; a busy or pointless Install or Remove is refused with a reason; and
the routes behind the Settings control say the same.

snapshot_download is replaced by a stand-in that writes the files into the
folder it is given. Nothing here reaches the network or loads weights.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from flask import Flask

from backend.services import identity_encoder as ie
from backend.services.identity_method_data import IMAGE_ENCODERS
from backend.tests.fixtures.model_files import safetensors_bytes

REPO = "facebook/dinov2-base"
# A whole safetensors file: an install counts only when its weights pass the
# format check as well as the pinned sha256.
WEIGHTS = safetensors_bytes(16)


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = tmp_path / "identity_encoder"
    monkeypatch.setattr(ie, "install_root", lambda: path)
    with ie._lock:
        ie._job.update(ie._idle_job())
    return path


@pytest.fixture
def pinned_to_stand_in(monkeypatch):
    """Pin the entry to the stand-in weights, so a stand-in download verifies."""
    entry = IMAGE_ENCODERS[REPO]
    monkeypatch.setitem(entry, "weights_sha256", hashlib.sha256(WEIGHTS).hexdigest())
    return entry


class _Hub:
    """snapshot_download stand-in: records its call and writes the files."""

    def __init__(self, weights=WEIGHTS, error=None):
        self.calls = []
        self.weights = weights
        self.error = error

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        folder = Path(kwargs["local_dir"])
        for name in kwargs["allow_patterns"]:
            data = self.weights if name.endswith(".safetensors") else b"{}"
            (folder / name).write_bytes(data)
        return str(folder)


def _wait(timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = ie.status()
        if status["install"]["state"] != "running":
            return status
        time.sleep(0.02)
    raise AssertionError("the install did not finish")


def test_reading_the_status_downloads_nothing(root, monkeypatch):
    hub = _Hub()
    monkeypatch.setattr("huggingface_hub.snapshot_download", hub)
    status = ie.status()

    assert hub.calls == []
    assert status["installed"] is False
    assert status["smoke_check"] is None
    assert status["clip_check"] == "colour"
    assert status["repo"] == REPO and status["license"] == "Apache-2.0"
    assert status["revision"] == IMAGE_ENCODERS[REPO]["revision"]
    assert status["size_mb"] == round(IMAGE_ENCODERS[REPO]["size_bytes"] / 1_000_000)
    assert not root.exists(), "nothing is created before Install"


def test_install_fetches_the_declared_files_at_the_pinned_commit(root, pinned_to_stand_in, monkeypatch):
    hub = _Hub()
    monkeypatch.setattr("huggingface_hub.snapshot_download", hub)
    ie.start_install()
    status = _wait()

    assert status["install"]["state"] == "done", status["install"]
    assert status["installed"] is True and status["clip_check"] == "embed"
    (call,) = hub.calls
    assert call["repo_id"] == REPO
    assert call["revision"] == pinned_to_stand_in["revision"]
    assert call["allow_patterns"] == list(pinned_to_stand_in["files"])
    assert call["local_dir"] == str(root / pinned_to_stand_in["folder"])
    assert call["token"] is False, "a token in .env is never sent"
    assert ie.installed_path() == root / pinned_to_stand_in["folder"]


def test_weights_that_do_not_match_the_pin_are_deleted(root, monkeypatch):
    monkeypatch.setattr("huggingface_hub.snapshot_download", _Hub(weights=safetensors_bytes(8)))
    ie.start_install()
    status = _wait()

    assert status["install"]["state"] == "failed"
    assert "sha256" in status["install"]["error"]
    assert status["installed"] is False
    folder = root / IMAGE_ENCODERS[REPO]["folder"]
    assert not (folder / IMAGE_ENCODERS[REPO]["weights_file"]).exists()
    assert not (folder / ie.RECORD_NAME).exists()


def test_an_offline_backend_says_so(root, monkeypatch):
    monkeypatch.setattr("huggingface_hub.snapshot_download",
                        _Hub(error=OSError("Cannot reach the Hub: offline mode is enabled")))
    ie.start_install()
    status = _wait()
    assert status["install"]["state"] == "failed"
    assert "HF_HUB_OFFLINE" in status["install"]["error"]


def test_install_and_remove_refuse_what_they_cannot_do(root, pinned_to_stand_in, monkeypatch):
    with pytest.raises(ie.Refused, match="nothing to remove"):
        ie.remove()

    monkeypatch.setattr("huggingface_hub.snapshot_download", _Hub())
    ie.start_install()
    _wait()
    with pytest.raises(ie.Refused, match="already installed"):
        ie.start_install()

    status = ie.remove()
    assert status["installed"] is False
    assert not (root / pinned_to_stand_in["folder"]).exists()


def test_remove_waits_for_a_running_install(root):
    with ie._lock:
        ie._job.update({"state": "running"})
    with pytest.raises(ie.Refused, match="installing"):
        ie.remove()
    with pytest.raises(ie.Refused, match="already installing"):
        ie.start_install()


def test_the_routes_behind_the_settings_control(root, pinned_to_stand_in, monkeypatch):
    from backend.api.cast_library_api import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    client = app.test_client()

    got = client.get("/api/cast-library/identity-check")
    assert got.status_code == 200 and got.get_json()["installed"] is False

    refused = client.post("/api/cast-library/identity-check/remove")
    assert refused.status_code == 409
    assert "nothing to remove" in refused.get_json()["error"]
    assert refused.get_json()["installed"] is False

    monkeypatch.setattr("huggingface_hub.snapshot_download", _Hub())
    started = client.post("/api/cast-library/identity-check/install")
    assert started.status_code == 202
    _wait()
    again = client.post("/api/cast-library/identity-check/install")
    assert again.status_code == 409 and again.get_json()["installed"] is True

    removed = client.post("/api/cast-library/identity-check/remove")
    assert removed.status_code == 200 and removed.get_json()["installed"] is False
