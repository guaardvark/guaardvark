"""Tests for the GenerationCog (/generate-csv command)."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from commands.generation import GenerationCog
from core.api_client import APIError


@pytest.fixture
def generation_cog(mock_api_client, sample_config):
    bot = MagicMock()
    return GenerationCog(bot=bot, api_client=mock_api_client, config=sample_config)


class TestGenerateCsvCommand:
    @pytest.mark.asyncio
    async def test_generates_and_sends_result(self, generation_cog, mock_interaction, mock_api_client):
        """Successful generation should return an embed with stats."""
        await generation_cog._handle_generate(mock_interaction, "10 rows of user data")

        mock_interaction.response.defer.assert_awaited_once()
        mock_api_client.generate_csv.assert_awaited_once()
        mock_interaction.followup.send.assert_awaited_once()
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "10" in embed.description  # generated_items count
        assert "5.0s" in embed.description  # processing_time

    @pytest.mark.asyncio
    async def test_passes_output_filename(self, generation_cog, mock_interaction, mock_api_client):
        """Output filename should include user ID and timestamp."""
        with patch("commands.generation.time") as mock_time:
            mock_time.time.return_value = 1700000000
            await generation_cog._handle_generate(mock_interaction, "test data")

        call_args = mock_api_client.generate_csv.call_args
        filename = call_args.args[1]
        assert filename == "discord_123456789_1700000000.csv"

    @pytest.mark.asyncio
    async def test_handles_rate_limit(self, generation_cog, mock_interaction, sample_config):
        """When rate limited, should reject with ephemeral message."""
        # Exhaust the rate limit (max 2 per minute)
        for _ in range(sample_config["rate_limits"]["generate_csv"]):
            generation_cog.rate_limiter.check(123456789, "generate_csv")

        await generation_cog._handle_generate(mock_interaction, "more data please")

        mock_interaction.response.send_message.assert_awaited_once()
        call_args = mock_interaction.response.send_message.call_args
        assert call_args.kwargs.get("ephemeral") is True
        content = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "rate limited" in content.lower()

    @pytest.mark.asyncio
    async def test_handles_api_error(self, generation_cog, mock_interaction, mock_api_client):
        """API errors should be reported to the user."""
        mock_api_client.generate_csv.side_effect = APIError("Generation failed", 500)

        await generation_cog._handle_generate(mock_interaction, "test data")

        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "failed" in call_kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_empty_after_sanitization(self, generation_cog, mock_interaction):
        """Empty description after sanitization should be rejected."""
        await generation_cog._handle_generate(mock_interaction, "<@!12345>")

        mock_interaction.response.send_message.assert_awaited_once()
        call_kwargs = mock_interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
