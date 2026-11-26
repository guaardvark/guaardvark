"""The Upscaling page's target resolution reaches the plugin on every route.

The plugin resizes to ``target_width`` (plugins/upscaling/tests/test_app.py
proves the sizes); these check that each proxy route forwards it, refuses a
malformed one before anything is staged or queued, and that a scheduled
upscale replays it.
"""
from __future__ import annotations

import contextlib
import io

import pytest
from flask import Flask

import backend.api.upscaling_api as m
from backend.services.job_handoff import HANDOFFS


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_get_upload_dir", lambda: tmp_path)
    # Results are written only under the output folders; here that is tmp_path.
    monkeypatch.setattr("backend.config.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_start_upscaling_for_job", lambda: None)
    monkeypatch.setattr(m, "_gpu_turn", lambda _model: contextlib.nullcontext())
    monkeypatch.setenv(m.upscale_supervisor.ENV, "0")
    app = Flask(__name__)
    app.register_blueprint(m.upscaling_bp)
    return app.test_client(), tmp_path


@pytest.fixture
def posted(monkeypatch):
    calls = []

    def fake_post(path, payload, timeout=None):
        calls.append((path, payload))
        return {"job_id": "j1", "status": "pending"}, 202

    monkeypatch.setattr(m, "_proxy_post", fake_post)
    return calls


def _png(name="shot.png"):
    return (io.BytesIO(b"\x89PNG\r\n\x1a\n"), name)


def _mp4(name="clip.mp4"):
    return (io.BytesIO(b"\x00\x00\x00\x18ftypmp42"), name)


@pytest.mark.parametrize("value, expected", [
    (None, None), ("", None), ("3840", 3840), (7680, 7680), (3840.0, 3840), (" 1080 ", 1080),
])
def test_parse_target_width(value, expected):
    assert m._parse_target_width(value) == expected


@pytest.mark.parametrize("value", ["4k", "12.5", "0", "15", "16385", "nan", "inf", True])
def test_parse_target_width_refuses(value):
    with pytest.raises(ValueError):
        m._parse_target_width(value)


@pytest.mark.parametrize("width, suffix", [("3840", "4k"), ("7680", "8k"), ("1920", "upscaled")])
def test_video_upload_sends_the_target(client, posted, width, suffix):
    c, root = client
    resp = c.post("/api/upscaling/upload", data={"file": _mp4(), "target_width": width},
                  content_type="multipart/form-data")

    assert resp.status_code == 200
    path, payload = posted[0]
    assert path == "/upscale/video"
    assert payload["target_width"] == int(width)
    assert payload["output_path"] == str(root / "output" / f"clip_{suffix}.mp4")


def test_a_second_upload_of_a_name_keeps_the_first(client, posted):
    c, root = client
    for _ in range(2):
        resp = c.post("/api/upscaling/upload", data={"file": _mp4(), "target_width": "3840"},
                      content_type="multipart/form-data")
        assert resp.status_code == 200

    first, second = posted[0][1], posted[1][1]
    assert first["output_path"] == str(root / "output" / "clip_4k.mp4")
    assert second["input_path"] != first["input_path"]
    assert second["output_path"] != first["output_path"]
    assert second["output_path"].endswith("_4k.mp4")


def test_video_upload_without_a_target_sends_none(client, posted):
    c, _ = client
    resp = c.post("/api/upscaling/upload", data={"file": _mp4()}, content_type="multipart/form-data")

    assert resp.status_code == 200
    assert "target_width" not in posted[0][1]


def test_video_upload_refuses_a_bad_target_before_saving(client, posted):
    c, root = client
    resp = c.post("/api/upscaling/upload", data={"file": _mp4(), "target_width": "4k"},
                  content_type="multipart/form-data")

    assert resp.status_code == 400
    assert posted == []
    assert not (root / "input" / "clip.mp4").exists()


@pytest.mark.parametrize("width, suffix", [(7680, "8k"), (3840, "4k"), (None, "upscaled")])
def test_video_json_names_the_output_by_its_target(client, posted, width, suffix):
    c, _ = client
    body = {"input_path": "/v/clip.mp4", **({"target_width": width} if width else {})}
    assert c.post("/api/upscaling/upscale/video", json=body).status_code == 200
    assert posted[0][1]["suffix"] == suffix


def test_video_json_keeps_a_suffix_the_caller_gave(client, posted):
    c, _ = client
    c.post("/api/upscaling/upscale/video", json={"input_path": "/v/clip.mp4", "target_width": 7680, "suffix": "final"})
    assert posted[0][1]["suffix"] == "final"


def test_a_scheduled_upscale_replays_the_target(client, posted):
    """save_for_later returns the request the scheduler later posts to the
    handoff's start route; the target survives the round trip."""
    c, _ = client
    saved = c.post("/api/upscaling/upload",
                   data={"file": _mp4(), "target_width": "7680", "save_for_later": "true"},
                   content_type="multipart/form-data").get_json()["data"]["request"]
    assert saved["target_width"] == 7680
    assert posted == []

    start = HANDOFFS["upscale"].starts["video"]
    assert start == "/api/upscaling/upscale/video"
    resp = c.post(start, json=saved)

    assert resp.status_code == 200
    path, payload = posted[0]
    assert path == "/upscale/video"
    assert payload["target_width"] == 7680
    assert payload["output_path"] == saved["output_path"]


def test_video_json_refuses_a_bad_target(client, posted):
    c, _ = client
    resp = c.post("/api/upscaling/upscale/video", json={"input_path": "/v/clip.mp4", "target_width": 0})

    assert resp.status_code == 400
    assert posted == []


def test_the_upscale_queue_gets_the_target(client, monkeypatch, posted):
    c, _ = client
    monkeypatch.setenv(m.upscale_supervisor.ENV, "1")
    submitted = []

    class FakeSupervisor:
        def submit(self, kind, path, payload, label, on_complete=None):
            submitted.append((kind, path, payload))
            return {"job_id": "q1"}, None, 200

    monkeypatch.setattr(m.upscale_supervisor, "get_supervisor", lambda: FakeSupervisor())
    resp = c.post("/api/upscaling/upload", data={"file": _mp4(), "target_width": "3840"},
                  content_type="multipart/form-data")

    assert resp.status_code == 200
    assert submitted[0][:2] == ("video", "/upscale/video")
    assert submitted[0][2]["target_width"] == 3840


def test_single_image_sends_the_target(client, monkeypatch):
    c, _ = client
    captured = []

    def fake_post(path, payload, timeout=None):
        captured.append(payload)
        return {"status": "completed"}, 200

    monkeypatch.setattr(m, "_proxy_post", fake_post)
    resp = c.post("/api/upscaling/upscale/image", data={"file": _png(), "target_width": "3840"},
                  content_type="multipart/form-data")

    assert resp.status_code == 200
    assert captured[0]["target_width"] == 3840


def test_single_image_refuses_a_bad_target_before_staging(client, posted):
    c, root = client
    resp = c.post("/api/upscaling/upscale/image", data={"file": _png(), "target_width": "huge"},
                  content_type="multipart/form-data")

    assert resp.status_code == 400
    assert posted == []
    assert not (root / "input" / "images").exists()


def test_image_batch_sends_the_target(client, posted):
    c, _ = client
    resp = c.post("/api/upscaling/upscale/images",
                  data={"files": [_png("a.png"), _png("b.png")], "target_width": "7680"},
                  content_type="multipart/form-data")

    assert resp.status_code == 200
    path, payload = posted[0]
    assert path == "/upscale/images"
    assert payload["target_width"] == 7680


def test_document_options_carry_the_target():
    """/upscale/document stringifies its JSON body into this same reader."""
    body = {"model": "4x-UltraSharp", "target_width": 3840}
    options = m._image_options_from_form({k: str(v) for k, v in body.items()})
    assert options == {"model": "4x-UltraSharp", "target_width": 3840}


def test_video_json_refuses_an_output_outside_the_output_folders(client, posted, tmp_path_factory):
    c, root = client
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    (root / "link").symlink_to(elsewhere)

    for target in (elsewhere / "o.mp4", root / ".." / elsewhere.name / "o.mp4", root / "link" / "o.mp4"):
        resp = c.post("/api/upscaling/upscale/video", json={"input_path": "/v/clip.mp4", "output_path": str(target)})
        assert resp.status_code == 400, target
    resp = c.post("/api/upscaling/upscale/video", json={"input_path": "/v/clip.mp4", "suffix": "../../x"})
    assert resp.status_code == 400
    assert posted == []


def test_video_json_never_writes_beside_an_input_outside_the_output_folders(client, posted, tmp_path_factory):
    """The default is beside the input only when the input is in a folder
    results are written to; otherwise it is the upscaling output folder."""
    c, root = client
    elsewhere = tmp_path_factory.mktemp("elsewhere")

    c.post("/api/upscaling/upscale/video", json={"input_path": str(elsewhere / "clip.mp4")})
    c.post("/api/upscaling/upscale/video", json={"input_path": str(root / "videos" / "clip.mp4")})

    assert posted[0][1]["output_path"] == str(root / "output" / "clip_upscaled.mp4")
    assert posted[1][1]["output_path"] == str(root / "videos" / "clip_upscaled.mp4")
