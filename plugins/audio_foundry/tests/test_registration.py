"""Unit tests for service.registration.register_output.

Mocks httpx.post so we can exercise the success and failure paths without a
live backend. A failed registration must NOT raise — the generate response
must still succeed because the file is already on disk.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends.base import GenerationResult  # noqa: E402
from service.registration import register_output  # noqa: E402


def _make_result(tmp_path: Path) -> GenerationResult:
    f = tmp_path / "foo.wav"
    f.write_bytes(b"RIFFtest")
    return GenerationResult(
        path=f,
        duration_s=1.0,
        sample_rate=44100,
        meta={"backend": "mock", "seed": 7},
    )


def test_register_output_success_returns_document_dict(tmp_path):
    result = _make_result(tmp_path)

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "success": True,
        "message": "Output registered",
        "data": {"id": 42, "filename": "foo.wav", "path": "Audio/foo.wav"},
    }

    with patch("service.registration.httpx.post", return_value=fake_response) as mock_post:
        doc = register_output(result)

    assert doc is not None
    assert doc["id"] == 42
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["folder_name"] == "Audio"
    assert call_kwargs["json"]["file_metadata"]["seed"] == 7
    assert call_kwargs["json"]["physical_path"].endswith("foo.wav")


def test_register_output_http_error_returns_none(tmp_path):
    result = _make_result(tmp_path)

    with patch("service.registration.httpx.post", side_effect=httpx.ConnectError("refused")):
        doc = register_output(result)

    assert doc is None  # non-fatal, file still on disk


def test_register_output_non_2xx_returns_none(tmp_path):
    result = _make_result(tmp_path)

    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    )

    with patch("service.registration.httpx.post", return_value=fake_response):
        doc = register_output(result)

    assert doc is None


def test_register_output_sends_absolute_path(tmp_path):
    """Backend needs the absolute path — we resolve relative paths before sending."""
    rel_file = tmp_path / "foo.wav"
    rel_file.write_bytes(b"RIFFtest")
    result = GenerationResult(
        path=Path("foo.wav"),  # intentionally relative
        duration_s=1.0,
        sample_rate=44100,
        meta={},
    )

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"data": {"id": 1}}

    with patch("service.registration.httpx.post", return_value=fake_response) as mock_post:
        register_output(result)

    sent_path = mock_post.call_args.kwargs["json"]["physical_path"]
    assert Path(sent_path).is_absolute()
