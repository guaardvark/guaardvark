"""Unit tests for service.orchestrator_client.

Mocks httpx.post so we can exercise success / network-error / non-2xx paths
without a live backend. The contract: every method is non-fatal; HTTP
failures must NOT raise; they return False.

Most importantly, with enabled=False every method must short-circuit to True
without touching the network — that's how tests keep their hands clean.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Important: clear the env-disable for these specific tests so we can verify
# the enabled=True path actually does call httpx. The conftest sets the env
# var, so we have to override it here per-test.
@pytest.fixture(autouse=True)
def _clear_orch_env():
    prev = os.environ.pop("AUDIO_FOUNDRY_DISABLE_ORCHESTRATOR", None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ["AUDIO_FOUNDRY_DISABLE_ORCHESTRATOR"] = prev


from service.orchestrator_client import OrchestratorClient  # noqa: E402


def _ok_response():
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json.return_value = {"success": True}
    return r


def test_disabled_client_short_circuits_without_http():
    """enabled=False must skip the HTTP layer entirely and return True."""
    client = OrchestratorClient(enabled=False)
    with patch("service.orchestrator_client.httpx.post") as mock_post:
        assert client.request_vram("audio_foundry:fx", 6000) is True
        assert client.mark_loaded("audio_foundry:fx") is True
        assert client.release("audio_foundry:fx") is True
        assert client.evict("audio_foundry:fx") is True
        mock_post.assert_not_called()


def test_env_var_overrides_constructor_arg():
    os.environ["AUDIO_FOUNDRY_DISABLE_ORCHESTRATOR"] = "1"
    try:
        client = OrchestratorClient(enabled=True)  # ignored
        assert client.enabled is False
    finally:
        os.environ.pop("AUDIO_FOUNDRY_DISABLE_ORCHESTRATOR", None)


def test_request_vram_posts_correct_payload():
    with patch("service.orchestrator_client.httpx.post", return_value=_ok_response()) as mock_post:
        client = OrchestratorClient()
        ok = client.request_vram("audio_foundry:fx", 6000, priority=72)
    assert ok is True
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0].endswith("/api/gpu/memory/preload")
    assert kwargs["json"] == {"slot_id": "audio_foundry:fx", "vram_mb": 6000, "priority": 72}


def test_mark_loaded_posts_correct_payload():
    with patch("service.orchestrator_client.httpx.post", return_value=_ok_response()) as mock_post:
        client = OrchestratorClient()
        ok = client.mark_loaded("audio_foundry:voice")
    assert ok is True
    args, kwargs = mock_post.call_args
    assert args[0].endswith("/api/gpu/memory/mark-loaded")
    assert kwargs["json"] == {"slot_id": "audio_foundry:voice"}


def test_release_posts_correct_payload():
    with patch("service.orchestrator_client.httpx.post", return_value=_ok_response()) as mock_post:
        client = OrchestratorClient()
        client.release("audio_foundry:music")
    args, _ = mock_post.call_args
    assert args[0].endswith("/api/gpu/memory/release")


def test_evict_posts_correct_payload():
    with patch("service.orchestrator_client.httpx.post", return_value=_ok_response()) as mock_post:
        client = OrchestratorClient()
        client.evict("audio_foundry:fx")
    args, _ = mock_post.call_args
    assert args[0].endswith("/api/gpu/memory/evict")


def test_connect_error_returns_false_does_not_raise():
    with patch("service.orchestrator_client.httpx.post", side_effect=httpx.ConnectError("refused")):
        client = OrchestratorClient()
        assert client.request_vram("audio_foundry:fx", 6000) is False


def test_5xx_returns_false_does_not_raise():
    response = MagicMock()
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    )
    with patch("service.orchestrator_client.httpx.post", return_value=response):
        client = OrchestratorClient()
        assert client.mark_loaded("audio_foundry:fx") is False


def test_backend_url_trailing_slash_normalized():
    with patch("service.orchestrator_client.httpx.post", return_value=_ok_response()) as mock_post:
        client = OrchestratorClient(backend_url="http://localhost:5002/")
        client.evict("audio_foundry:fx")
    args, _ = mock_post.call_args
    # No double-slash before /api/...
    assert args[0] == "http://localhost:5002/api/gpu/memory/evict"
