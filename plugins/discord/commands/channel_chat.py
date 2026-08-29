"""Channel chat cog — respond to regular messages in designated channels or when mentioned."""
import logging

import discord
from discord.ext import commands

from core.api_client import GuaardvarkClient, APIError
from core.approvals import make_approval_handler
from core.chat_reply import (
    attachment_to_b64,
    channel_session_id,
    files_from_generated,
    first_image_attachment,
    send_chunks,
    user_session_id,
)
from core.rate_limiter import RateLimiter
from core.security import sanitize_input

logger = logging.getLogger("discord_bot")


class ChannelChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"].get("ask", 10), window_seconds=60
        )
        self._chat_channels: set[int] = set(
            config.get("channel_chat", {}).get("channel_ids", [])
        )
        self._respond_to_mentions = config.get("channel_chat", {}).get(
            "respond_to_mentions", True
        )

    def _auto_approve(self) -> bool:
        return bool(self.config.get("tools", {}).get("auto_approve", False))

    def _session_id(self, message: discord.Message, is_chat_channel: bool) -> str:
        if is_chat_channel:
            return channel_session_id(message.channel.id)
        return user_session_id(message.author.id)

    async def _image_from_message(self, message: discord.Message) -> str | None:
        att = first_image_attachment(message.attachments)
        if att is None and message.reference:
            resolved = getattr(message.reference, "resolved", None)
            att = first_image_attachment(getattr(resolved, "attachments", None))
        return await attachment_to_b64(att) if att is not None else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        is_chat_channel = message.channel.id in self._chat_channels
        bot_id = self.bot.user.id if self.bot.user else None
        is_mention = bool(bot_id) and any(
            getattr(m, "id", None) == bot_id for m in (message.mentions or [])
        )
        resolved = getattr(message.reference, "resolved", None) if message.reference else None
        is_reply_to_bot = bool(bot_id) and getattr(
            getattr(resolved, "author", None), "id", None
        ) == bot_id

        if not (
            is_chat_channel
            or (self._respond_to_mentions and is_mention)
            or is_reply_to_bot
        ):
            return

        content = message.content
        if self.bot.user:
            content = (
                content.replace(f"<@{self.bot.user.id}>", "")
                .replace(f"<@!{self.bot.user.id}>", "")
                .strip()
            )

        has_image = first_image_attachment(message.attachments) is not None
        if not has_image and message.reference:
            resolved = getattr(message.reference, "resolved", None)
            has_image = first_image_attachment(
                getattr(resolved, "attachments", None)
            ) is not None

        if not content and not has_image:
            return
        # Image-only posts in a listen-all channel without a mention/reply are noise.
        if not content and has_image and is_chat_channel and not is_mention and not is_reply_to_bot:
            return

        allowed, _, retry_after = self.rate_limiter.check(message.author.id, "ask")
        if not allowed:
            await message.reply(
                f"Rate limited. Try again in {retry_after:.0f}s.", mention_author=False
            )
            return

        cleaned = sanitize_input(
            content, max_length=self.config["security"]["max_prompt_length"]
        )
        if not cleaned and not has_image:
            return
        if not cleaned:
            cleaned = "Describe this image."

        logger.info("[channel] user=%s msg=%r", message.author, cleaned[:100])

        first = {"sent": False}

        async def send_fn(text, *, files=None, view=None):
            kwargs = {"content": text, "mention_author": False}
            if files:
                kwargs["files"] = files
            if view is not None:
                kwargs["view"] = view
            if not first["sent"]:
                first["sent"] = True
                return await message.reply(**kwargs)
            extra = {"content": kwargs["content"]}
            if kwargs.get("files"):
                extra["files"] = kwargs["files"]
            if kwargs.get("view") is not None:
                extra["view"] = kwargs["view"]
            return await message.channel.send(**extra)

        async with message.channel.typing():
            try:
                image_b64 = await self._image_from_message(message)
                result = await self.api.unified_chat(
                    cleaned,
                    session_id=self._session_id(message, is_chat_channel),
                    image=image_b64,
                    approval_handler=make_approval_handler(
                        send_fn, message.author.id, auto_approve=self._auto_approve()
                    ),
                )
                response_text = result.get("response", "No response received.")
                files = await files_from_generated(
                    self.api, result.get("generated_images") or []
                )
                await send_chunks(send_fn, response_text, files=files)

            except APIError as e:
                logger.error("Channel chat API error: %s", e)
                await message.reply(
                    content=f"Failed to get a response. Guaardvark may be offline. ({e})",
                    mention_author=False,
                )
            except Exception:
                logger.exception("Unexpected error in channel chat")
                await message.reply(
                    content="An unexpected error occurred.",
                    mention_author=False,
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelChatCog(bot, bot.api_client, bot.config))
