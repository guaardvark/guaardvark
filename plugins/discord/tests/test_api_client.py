"""Tests for the Guaardvark API client."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.api_client import GuaardvarkClient, APIError


class TestGuaardvarkClient:
    """Tests for GuaardvarkClient."""

    @pytest.mark.asyncio
    async def test_setup_creates_session(self):
        client = GuaardvarkClient()
        assert client.session is None
        await client.setup()
        assert client.session is not None
        assert not client.session.closed
        await client.close()

    def test_unwrap_envelope(self):
        client = GuaardvarkClient()
        envelope = {"success": True, "data": {"result": "hello"}}
        assert client._unwrap(envelope) == {"result": "hello"}

    def test_unwrap_raw(self):
        client = GuaardvarkClient()
        raw = {"result": "hello"}
        assert client._unwrap(raw) == {"result": "hello"}

    @pytest.mark.asyncio
    async def test_generate_image_wraps_prompt_in_list(self):
        client = GuaardvarkClient()
        captured_kwargs = {}

        async def mock_post(path, **kwargs):
            captured_kwargs.update(kwargs)
            return {"batch_id": "test-123"}

        client._post = mock_post
        await client.generate_image("a cute cat", steps=20, width=512, height=512)
        payload = captured_kwargs["json"]
        assert isinstance(payload["prompts"], list)
        assert payload["prompts"] == ["a cute cat"]

    @pytest.mark.asyncio
    async def test_chat_sends_correct_endpoint(self):
        client = GuaardvarkClient()
        captured_path = None

        async def mock_post(path, **kwargs):
            nonlocal captured_path
            captured_path = path
            return {"response": "hi"}

        client._post = mock_post
        await client.chat("hello", session_id="sess_1")
        assert captured_path == "/enhanced-chat"

    @pytest.mark.asyncio
    async def test_chat_payload_structure(self):
        client = GuaardvarkClient()
        captured_kwargs = {}

        async def mock_post(path, **kwargs):
            captured_kwargs.update(kwargs)
            return {"response": "hi"}

        client._post = mock_post
        await client.chat("hello", session_id="sess_1", project_id=42)
        payload = captured_kwargs["json"]
        assert payload["message"] == "hello"
        assert payload["session_id"] == "sess_1"
        assert payload["project_id"] == 42
        assert payload["use_rag"] is True
        assert payload["voice_mode"] is False

    def test_api_error_attributes(self):
        err = APIError("not found", 404)
        assert str(err) == "not found"
        assert err.status_code == 404
