"""Tests for the ChatCog (/ask command)."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from commands.chat import ChatCog, split_message
from core.api_client import APIError


@pytest.fixture
def chat_cog(mock_api_client, sample_config):
    bot = MagicMock()
    return ChatCog(bot=bot, api_client=mock_api_client, config=sample_config)


class TestSplitMessage:
    def test_short_message_unchanged(self):
        assert split_message("hello") == ["hello"]

    def test_splits_long_message(self):
        text = "word " * 500  # 2500 chars
        chunks = split_message(text, max_length=2000)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 2000

    def test_empty_string(self):
        assert split_message("") == [""]

    def test_exact_limit(self):
        text = "a" * 2000
        assert split_message(text) == [text]


class TestAskCommand:
    @pytest.mark.asyncio
    async def test_ask_returns_response(self, chat_cog, mock_interaction, mock_api_client):
        """Basic ask should defer then follow up with the response."""
        await chat_cog._handle_ask(mock_interaction, "Hello AI")

        mock_interaction.response.defer.assert_awaited_once()
        mock_interaction.followup.send.assert_awaited()
        call_kwargs = mock_interaction.followup.send.call_args
        assert "Hello! I'm Guaardvark." in call_kwargs.kwargs.get("content", "") or \
               "Hello! I'm Guaardvark." in (call_kwargs.args[0] if call_kwargs.args else "")

    @pytest.mark.asyncio
    async def test_ask_uses_user_session_id(self, chat_cog, mock_interaction, mock_api_client):
        """Session ID should be discord_{user_id}."""
        await chat_cog._handle_ask(mock_interaction, "test prompt")

        mock_api_client.chat.assert_awaited_once()
        call_args = mock_api_client.chat.call_args
        assert call_args.args[1] == "discord_123456789"

    @pytest.mark.asyncio
    async def test_ask_handles_api_error(self, chat_cog, mock_interaction, mock_api_client):
        """API errors should be caught and reported to the user."""
        mock_api_client.chat.side_effect = APIError("Backend offline", 503)

        await chat_cog._handle_ask(mock_interaction, "Hello")

        mock_interaction.response.defer.assert_awaited_once()
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "backend may be offline" in call_kwargs.get("content", "").lower() or \
               "failed" in call_kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_ask_sanitizes_input(self, chat_cog, mock_interaction, mock_api_client):
        """Mentions and code blocks should be stripped before sending to API."""
        await chat_cog._handle_ask(mock_interaction, "<@!12345> tell me about ```code```")

        call_args = mock_api_client.chat.call_args.args[0]
        assert "<@!12345>" not in call_args
        assert "```" not in call_args

    @pytest.mark.asyncio
    async def test_ask_splits_long_response(self, chat_cog, mock_interaction, mock_api_client):
        """Responses over 2000 chars should be split into multiple messages."""
        long_response = "word " * 600  # ~3000 chars, under 4000 so no file
        mock_api_client.chat.return_value = {"response": long_response}

        await chat_cog._handle_ask(mock_interaction, "Give me a long answer")

        assert mock_interaction.followup.send.await_count >= 2

    @pytest.mark.asyncio
    async def test_ask_very_long_response_as_file(self, chat_cog, mock_interaction, mock_api_client):
        """Responses over 4000 chars should be sent as a file attachment."""
        huge_response = "x" * 5000
        mock_api_client.chat.return_value = {"response": huge_response}

        await chat_cog._handle_ask(mock_interaction, "Give me a huge answer")

        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("file") is not None
        assert "too long" in call_kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_ask_empty_after_sanitization(self, chat_cog, mock_interaction, mock_api_client):
        """If the prompt is empty after sanitization, respond ephemeral."""
        await chat_cog._handle_ask(mock_interaction, "<@!12345>")

        mock_interaction.response.send_message.assert_awaited_once()
        call_kwargs = mock_interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_ask_channel_not_allowed(self, chat_cog, mock_interaction, sample_config):
        """If channel is not in allowlist, deny the request."""
        chat_cog.config = {
            **sample_config,
            "security": {**sample_config["security"], "allowed_channels": [999999]},
        }

        await chat_cog._handle_ask(mock_interaction, "Hello")

        mock_interaction.response.send_message.assert_awaited_once()
        call_args = mock_interaction.response.send_message.call_args
        content = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "not allowed" in content.lower()
