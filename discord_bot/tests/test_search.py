"""Tests for the SearchCog (/search command)."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from discord_bot.commands.search import SearchCog
from discord_bot.core.api_client import APIError


@pytest.fixture
def search_cog(mock_api_client, sample_config):
    bot = MagicMock()
    return SearchCog(bot=bot, api_client=mock_api_client, config=sample_config)


class TestSearchCommand:
    @pytest.mark.asyncio
    async def test_search_returns_embed(
        self, search_cog, mock_interaction, mock_api_client
    ):
        """Search should return an embed with the answer and sources."""
        await search_cog._handle_search(mock_interaction, "what is the answer?")

        mock_interaction.response.defer.assert_awaited_once()
        mock_interaction.followup.send.assert_awaited_once()
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "42" in embed.description

    @pytest.mark.asyncio
    async def test_search_handles_no_results(
        self, search_cog, mock_interaction, mock_api_client
    ):
        """When there are no results, show a 'no results' message."""
        mock_api_client.semantic_search.return_value = {
            "answer": None,
            "sources": [],
        }

        await search_cog._handle_search(mock_interaction, "something obscure")

        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert "no matching results" in embed.description.lower()

    @pytest.mark.asyncio
    async def test_search_sanitizes_input(
        self, search_cog, mock_interaction, mock_api_client
    ):
        """Mentions and code blocks should be stripped from search query."""
        await search_cog._handle_search(mock_interaction, "<@!99999> find ```stuff```")

        call_args = mock_api_client.semantic_search.call_args.args[0]
        assert "<@!99999>" not in call_args
        assert "```" not in call_args

    @pytest.mark.asyncio
    async def test_search_handles_api_error(
        self, search_cog, mock_interaction, mock_api_client
    ):
        """API errors should be reported to the user."""
        mock_api_client.semantic_search.side_effect = APIError(
            "Search service down", 503
        )

        await search_cog._handle_search(mock_interaction, "test query")

        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "failed" in call_kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_search_empty_after_sanitization(self, search_cog, mock_interaction):
        """Empty query after sanitization should be rejected ephemeral."""
        await search_cog._handle_search(mock_interaction, "<@!12345>")

        mock_interaction.response.send_message.assert_awaited_once()
        call_kwargs = mock_interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
