"""Tests for the SystemCog (/status, /models, /switch-model commands)."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from discord_bot.commands.system import SystemCog
from discord_bot.core.api_client import APIError


@pytest.fixture
def system_cog(mock_api_client, sample_config):
    bot = MagicMock()
    return SystemCog(bot=bot, api_client=mock_api_client, config=sample_config)


def _make_admin_user():
    """Create a mock user with the Admin role."""
    user = MagicMock()
    user.id = 123456789
    role = MagicMock()
    role.name = "Admin"
    user.roles = [role]
    return user


def _make_regular_user():
    """Create a mock user without admin roles."""
    user = MagicMock()
    user.id = 123456789
    role = MagicMock()
    role.name = "Member"
    user.roles = [role]
    return user


class TestStatusCommand:
    @pytest.mark.asyncio
    async def test_status_returns_embed(
        self, system_cog, mock_interaction, mock_api_client
    ):
        """Status should return an embed with system info."""
        await system_cog._handle_status(mock_interaction)

        mock_interaction.response.defer.assert_awaited_once()
        mock_interaction.followup.send.assert_awaited_once()
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        field_names = [f.name for f in embed.fields]
        assert "Model" in field_names
        assert "Ollama" in field_names
        assert "Version" in field_names

    @pytest.mark.asyncio
    async def test_status_handles_api_error(
        self, system_cog, mock_interaction, mock_api_client
    ):
        """API errors should be reported."""
        mock_api_client.get_diagnostics.side_effect = APIError("Backend down", 503)

        await system_cog._handle_status(mock_interaction)

        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "failed" in call_kwargs.get("content", "").lower()


class TestModelsCommand:
    @pytest.mark.asyncio
    async def test_models_returns_list(
        self, system_cog, mock_interaction, mock_api_client
    ):
        """Models should return an embed listing available models."""
        await system_cog._handle_models(mock_interaction)

        mock_interaction.response.defer.assert_awaited_once()
        mock_interaction.followup.send.assert_awaited_once()
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        field_names = [f.name for f in embed.fields]
        assert "llama3" in field_names
        assert "mistral" in field_names

    @pytest.mark.asyncio
    async def test_models_empty_list(
        self, system_cog, mock_interaction, mock_api_client
    ):
        """When no models available, show a text message."""
        mock_api_client.get_models.return_value = {"models": []}

        await system_cog._handle_models(mock_interaction)

        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "no models" in call_kwargs.get("content", "").lower()


class TestSwitchModelCommand:
    @pytest.mark.asyncio
    async def test_switch_model_requires_admin(self, system_cog, mock_interaction):
        """Non-admin users should be denied."""
        mock_interaction.user = _make_regular_user()

        await system_cog._handle_switch_model(mock_interaction, "llama3")

        mock_interaction.response.send_message.assert_awaited_once()
        call_args = mock_interaction.response.send_message.call_args
        assert call_args.kwargs.get("ephemeral") is True
        content = (
            call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        )
        assert "admin" in content.lower()

    @pytest.mark.asyncio
    async def test_switch_model_admin_succeeds(
        self, system_cog, mock_interaction, mock_api_client
    ):
        """Admin users should be able to switch models."""
        mock_interaction.user = _make_admin_user()

        await system_cog._handle_switch_model(mock_interaction, "llama3")

        mock_interaction.response.defer.assert_awaited_once()
        mock_api_client.switch_model.assert_awaited_once_with("llama3")
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "switch" in call_kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_switch_model_api_error(
        self, system_cog, mock_interaction, mock_api_client
    ):
        """API errors during model switch should be reported."""
        mock_interaction.user = _make_admin_user()
        mock_api_client.switch_model.side_effect = APIError("Model not found", 404)

        await system_cog._handle_switch_model(mock_interaction, "nonexistent")

        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "failed" in call_kwargs.get("content", "").lower()
