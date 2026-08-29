"""Tests for ChannelChatCog."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from commands.channel_chat import ChannelChatCog
from core.api_client import APIError


def _message(*, content, user_id=123456789, channel_id=111222333, attachments=None, mention=False, reply_to_bot=False, bot_user=None):
    message = AsyncMock()
    message.author = MagicMock()
    message.author.bot = False
    message.author.id = user_id
    message.author.__str__ = lambda self: "testuser"
    message.channel = MagicMock()
    message.channel.id = channel_id
    message.channel.typing = MagicMock()
    message.channel.typing.return_value.__aenter__ = AsyncMock()
    message.channel.typing.return_value.__aexit__ = AsyncMock()
    message.channel.send = AsyncMock()
    message.content = content
    message.attachments = attachments or []
    message.mentions = [bot_user] if mention and bot_user else []
    message.reply = AsyncMock()
    if reply_to_bot and bot_user:
        resolved = MagicMock()
        resolved.author = bot_user
        resolved.attachments = []
        message.reference = MagicMock()
        message.reference.resolved = resolved
    else:
        message.reference = None
    return message


@pytest.fixture
def channel_cog(mock_api_client, sample_config):
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 999
    bot.user.__eq__ = lambda self, other: other is bot.user
    return ChannelChatCog(bot=bot, api_client=mock_api_client, config=sample_config)


@pytest.mark.asyncio
async def test_mention_uses_unified_chat(channel_cog, mock_api_client):
    msg = _message(
        content="<@999> hello there",
        mention=True,
        bot_user=channel_cog.bot.user,
    )
    await channel_cog.on_message(msg)
    mock_api_client.unified_chat.assert_awaited_once()
    mock_api_client.chat_claude.assert_not_awaited()
    kwargs = mock_api_client.unified_chat.call_args.kwargs
    assert kwargs["session_id"] == "discord_user_123456789"


@pytest.mark.asyncio
async def test_listen_channel_uses_channel_session(mock_api_client, sample_config):
    config = {
        **sample_config,
        "channel_chat": {"channel_ids": [111222333], "respond_to_mentions": True},
    }
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 999
    cog = ChannelChatCog(bot=bot, api_client=mock_api_client, config=config)
    msg = _message(content="what can you do")
    await cog.on_message(msg)
    kwargs = mock_api_client.unified_chat.call_args.kwargs
    assert kwargs["session_id"] == "discord_channel_111222333"


@pytest.mark.asyncio
async def test_forwards_image_attachment(channel_cog, mock_api_client):
    att = AsyncMock()
    att.read = AsyncMock(return_value=b"\x89PNG" + b"\x00" * 8)
    att.size = 12
    att.filename = "shot.png"
    att.content_type = "image/png"
    msg = _message(
        content="<@999> edit this",
        mention=True,
        bot_user=channel_cog.bot.user,
        attachments=[att],
    )
    await channel_cog.on_message(msg)
    kwargs = mock_api_client.unified_chat.call_args.kwargs
    assert kwargs["image"]


@pytest.mark.asyncio
async def test_ignores_unrelated_channel(channel_cog, mock_api_client):
    msg = _message(content="hello")
    await channel_cog.on_message(msg)
    mock_api_client.unified_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_reports_offline_on_api_error(channel_cog, mock_api_client):
    mock_api_client.unified_chat.side_effect = APIError("backend down", 503)
    msg = _message(
        content="<@999> hello",
        mention=True,
        bot_user=channel_cog.bot.user,
    )
    await channel_cog.on_message(msg)
    content = msg.reply.call_args.kwargs.get("content") or (
        msg.reply.call_args.args[0] if msg.reply.call_args.args else ""
    )
    assert "offline" in content.lower() or "failed" in content.lower()
    assert "claude" not in content.lower()
