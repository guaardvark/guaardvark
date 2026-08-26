"""Preview frames must not touch unified progress / .progress_jobs."""
from __future__ import annotations

import base64

from backend.utils.preview_emitter import MAX_PREVIEW_BYTES, emit_preview_event


def test_emit_preview_skips_empty():
    assert emit_preview_event("", "image/jpeg", b"\xff\xd8") is False
    assert emit_preview_event("job", "image/jpeg", b"") is False


def test_emit_preview_drops_oversize():
    assert emit_preview_event("job", "image/jpeg", b"x" * (MAX_PREVIEW_BYTES + 1)) is False


def test_emit_preview_does_not_touch_ups(monkeypatch):
    published = []
    emitted = []

    monkeypatch.setattr(
        "backend.utils.preview_emitter._publish_redis",
        lambda payload: published.append(payload) or True,
    )
    monkeypatch.setattr(
        "backend.utils.preview_emitter._emit_socketio",
        lambda process_id, payload: emitted.append((process_id, payload)) or True,
    )

    ups_called = []

    def boom(*_a, **_k):
        ups_called.append(True)
        raise AssertionError("UPS must not see preview bytes")

    monkeypatch.setattr("backend.utils.unified_progress_system.get_unified_progress", boom)
    monkeypatch.setattr("backend.utils.progress_emitter.emit_progress_event", boom)

    jpeg = b"\xff\xd8\xff\xd9"
    assert emit_preview_event("item-9", "image/jpeg", jpeg) is True
    assert ups_called == []
    assert published and published[0]["job_id"] == "item-9"
    assert published[0]["mime"] == "image/jpeg"
    assert base64.b64decode(published[0]["b64"]) == jpeg
    assert emitted[0][0] == "item-9"
    assert "b64" in emitted[0][1]


def test_router_direct_launch_calls_preview_cli_args():
    import inspect
    from backend.services.video_generation_router import VideoGenerationRouter

    src = inspect.getsource(VideoGenerationRouter._start_comfyui_direct)
    assert "preview_cli_args" in src
    assert "args.extend" in src
