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
        await client.generate_image("a cute cat", steps=9, width=1024, height=1024)
        payload = captured_kwargs["json"]
        assert isinstance(payload["prompts"], list)
        assert payload["prompts"] == ["a cute cat"]
        assert payload["steps"] == 9
        assert payload["width"] == 1024
        assert "subject_ids" not in payload

    @pytest.mark.asyncio
    async def test_generate_image_includes_subject_ids(self):
        client = GuaardvarkClient()
        captured_kwargs = {}

        async def mock_post(path, **kwargs):
            captured_kwargs.update(kwargs)
            return {"batch_id": "test-123"}

        client._post = mock_post
        await client.generate_image(
            "gotham rain", steps=9, width=1024, height=1024, subject_ids=[26]
        )
        payload = captured_kwargs["json"]
        assert payload["subject_ids"] == [26]
        assert captured_kwargs.get("json") is not None

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
        assert payload["use_rag"] is False
        assert payload["voice_mode"] is False

    def test_api_error_attributes(self):
        err = APIError("not found", 404)
        assert str(err) == "not found"
        assert err.status_code == 404

    def test_origin_strips_api_suffix(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        assert client.origin == "http://localhost:5002"

    def test_resolve_fetch_url_allows_relative_and_loopback(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        assert client._resolve_fetch_url("/api/x.png") == "http://localhost:5002/api/x.png"
        assert client._resolve_fetch_url("http://127.0.0.1:5002/api/x.png")
        assert client._resolve_fetch_url("https://evil.example/steal") is None

    @pytest.mark.asyncio
    async def test_unified_chat_posts_unified_endpoint(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        captured = {}

        async def mock_post(path, **kwargs):
            captured["path"] = path
            captured["json"] = kwargs.get("json")
            return {"success": True}

        class FakeStreamer:
            async def run(self, **kwargs):
                await kwargs["post_fn"]({
                    "session_id": kwargs["session_id"],
                    "message": kwargs["message"],
                    "options": kwargs["options"],
                    "image": kwargs.get("image"),
                })
                return {
                    "response": "hi",
                    "session_id": kwargs["session_id"],
                    "generated_images": [],
                }

        client._post = mock_post
        result = await client.unified_chat(
            "hello", session_id="discord_user_1", image="abc", streamer=FakeStreamer()
        )
        assert captured["path"] == "/chat/unified"
        assert captured["json"]["message"] == "hello"
        assert captured["json"]["session_id"] == "discord_user_1"
        assert captured["json"]["image"] == "abc"
        assert result["response"] == "hi"

    @pytest.mark.asyncio
    async def test_chat_claude_posts_escalate(self):
        client = GuaardvarkClient()
        captured_path = None

        async def mock_post(path, **kwargs):
            nonlocal captured_path
            captured_path = path
            return {"response": "hi"}

        client._post = mock_post
        await client.chat_claude("hello")
        assert captured_path == "/claude/escalate"
