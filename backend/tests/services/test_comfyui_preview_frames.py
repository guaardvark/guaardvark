"""ComfyUI latent-preview binary frames: parse, throttle, flags."""
from __future__ import annotations

import json
import struct
from pathlib import Path

from backend.services.comfyui_launch_flags import (
    PREVIEW_METHOD_ENV,
    PREVIEW_SIZE_ENV,
    preview_cli_args,
)
from backend.services.comfyui_progress_bridge import (
    ComfyUIProgressBridge,
    parse_comfy_preview_frame,
    ws_preview_enabled,
    ws_progress_enabled,
)

JPEG = b"\xff\xd8\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n"


def _pack_image(payload: bytes, type_num: int = 1) -> bytes:
    return struct.pack(">II", 1, type_num) + payload


def _pack_meta(payload: bytes, meta: dict | None = None) -> bytes:
    blob = json.dumps(meta or {"image_type": "image/jpeg"}).encode("utf-8")
    return struct.pack(">II", 4, len(blob)) + blob + payload


def test_parse_preview_image_jpeg():
    mime, payload = parse_comfy_preview_frame(_pack_image(JPEG, 1))
    assert mime == "image/jpeg"
    assert payload == JPEG


def test_parse_preview_image_png():
    mime, payload = parse_comfy_preview_frame(_pack_image(PNG, 2))
    assert mime == "image/png"
    assert payload == PNG


def test_parse_preview_with_metadata():
    raw = _pack_meta(JPEG, {"image_type": "image/jpeg", "node_id": "10"})
    mime, payload = parse_comfy_preview_frame(raw)
    assert mime == "image/jpeg"
    assert payload == JPEG


def test_parse_rejects_truncated_and_empty():
    assert parse_comfy_preview_frame(b"") is None
    assert parse_comfy_preview_frame(b"\x00\x00\x00\x01") is None
    assert parse_comfy_preview_frame(_pack_image(b"", 1)) is None
    # TEXT event type 3
    assert parse_comfy_preview_frame(struct.pack(">II", 3, 0) + b"hi") is None


def test_parse_rejects_overlong_metadata_header():
    raw = struct.pack(">II", 4, 9999) + b"nope"
    assert parse_comfy_preview_frame(raw) is None


def test_ws_preview_follows_progress_flag(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_WS_PROGRESS", "1")
    monkeypatch.setenv("GUAARDVARK_COMFYUI_WS_PREVIEW", "1")
    assert ws_preview_enabled() is True
    monkeypatch.setenv("GUAARDVARK_COMFYUI_WS_PREVIEW", "0")
    assert ws_preview_enabled() is False
    monkeypatch.setenv("GUAARDVARK_COMFYUI_WS_PREVIEW", "1")
    monkeypatch.setenv("GUAARDVARK_COMFYUI_WS_PROGRESS", "0")
    assert ws_progress_enabled() is False
    assert ws_preview_enabled() is False


def test_throttle_emits_first_then_drops(monkeypatch):
    emitted = []

    def fake_emit(process_id, mime, image_bytes):
        emitted.append((process_id, mime, image_bytes))
        return True

    monkeypatch.setattr(
        "backend.services.comfyui_progress_bridge.emit_preview_event", fake_emit
    )
    bridge = ComfyUIProgressBridge()
    frame = _pack_image(JPEG)
    bridge._maybe_emit_preview("item-1", frame)
    bridge._maybe_emit_preview("item-1", _pack_image(b"\xff\xd8" + b"x" * 8))
    bridge._maybe_emit_preview("item-1", _pack_image(b"\xff\xd8" + b"y" * 8))
    assert len(emitted) == 1
    assert emitted[0] == ("item-1", "image/jpeg", JPEG)


def test_preview_cli_args_defaults():
    assert list(preview_cli_args({})) == [
        "--preview-method", "auto", "--preview-size", "256",
    ]


def test_preview_cli_args_none_omits_size():
    assert list(preview_cli_args({PREVIEW_METHOD_ENV: "none"})) == [
        "--preview-method", "none",
    ]


def test_preview_cli_args_clamps_size_and_method():
    assert list(preview_cli_args({PREVIEW_METHOD_ENV: "nope", PREVIEW_SIZE_ENV: "12"})) == [
        "--preview-method", "auto", "--preview-size", "64",
    ]
    args = list(preview_cli_args({PREVIEW_METHOD_ENV: "taesd", PREVIEW_SIZE_ENV: "9999"}))
    assert args == ["--preview-method", "taesd", "--preview-size", "1024"]


def test_start_sh_matches_python_helper():
    root = Path(__file__).resolve().parents[3]
    text = (root / "plugins/comfyui/scripts/start.sh").read_text()
    assert "GUAARDVARK_COMFYUI_PREVIEW_METHOD" in text
    assert "GUAARDVARK_COMFYUI_PREVIEW_SIZE" in text
    assert "--preview-method" in text
    assert "--preview-size" in text
