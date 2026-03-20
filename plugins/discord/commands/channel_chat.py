"""Channel chat cog — respond to regular messages in designated channels or when mentioned."""
import io
import logging

import discord
from discord.ext import commands

from core.api_client import GuaardvarkClient, APIError
from core.rate_limiter import RateLimiter
from core.security import sanitize_input

logger = logging.getLogger("discord_bot")
DISCORD_MAX_LENGTH = 2000


def split_message(text: str, max_length: int = DISCORD_MAX_LENGTH) -> list[str]:
    if len(text) <= max_length:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks


class ChannelChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"].get("ask", 10), window_seconds=60
        )
        # In-memory conversation history per user
        self._history: dict[int, list[dict]] = {}
        self._max_history = 20
        # Channels where the bot listens to all messages (configured in config.yaml)
        self._chat_channels: set[int] = set(
            config.get("channel_chat", {}).get("channel_ids", [])
        )
        # Also respond when mentioned anywhere
        self._respond_to_mentions = config.get("channel_chat", {}).get("respond_to_mentions", True)

    def _get_history(self, user_id: int) -> list[dict]:
        return self._history.get(user_id, [])

    def _add_to_history(self, user_id: int, role: str, content: str):
        if user_id not in self._history:
            self._history[user_id] = []
        self._history[user_id].append({"role": role, "content": content})
        if len(self._history[user_id]) > self._max_history:
            self._history[user_id] = self._history[user_id][-self._max_history:]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore own messages and other bots
        if message.author.bot:
            return

        # Determine if we should respond
        is_chat_channel = message.channel.id in self._chat_channels
        is_mention = self.bot.user in message.mentions if self.bot.user else False
        is_reply_to_bot = (
            message.reference
            and message.reference.resolved
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author == self.bot.user
        )

        if not (is_chat_channel or (self._respond_to_mentions and is_mention) or is_reply_to_bot):
            return

        # Strip the mention from the message content
        content = message.content
        if self.bot.user:
            content = content.replace(f"<@{self.bot.user.id}>", "").replace(f"<@!{self.bot.user.id}>", "").strip()

        if not content:
            return

        # Rate limit
        allowed, _, retry_after = self.rate_limiter.check(message.author.id, "ask")
        if not allowed:
            await message.reply(f"Rate limited. Try again in {retry_after:.0f}s.", mention_author=False)
            return

        # Sanitize
        cleaned = sanitize_input(content, max_length=self.config["security"]["max_prompt_length"])
        if not cleaned:
            return

        logger.info("[channel] user=%s msg=%r", message.author, cleaned[:100])

        # Show typing indicator while generating
        async with message.channel.typing():
            try:
                history = self._get_history(message.author.id)
                result = await self.api.chat_claude(cleaned, history=history)
                response_text = result.get("response", "No response received.")

                self._add_to_history(message.author.id, "user", cleaned)
                self._add_to_history(message.author.id, "assistant", response_text)

                if len(response_text) > 4000:
                    file = discord.File(
                        io.BytesIO(response_text.encode()), filename="response.md"
                    )
                    await message.reply(
                        content=f"Response too long ({len(response_text)} chars). See attached file.",
                        file=file,
                        mention_author=False,
                    )
                else:
                    for i, chunk in enumerate(split_message(response_text)):
                        if i == 0:
                            await message.reply(content=chunk, mention_author=False)
                        else:
                            await message.channel.send(content=chunk)

            except APIError as e:
                logger.error("Channel chat API error: %s", e)
                await message.reply(
                    content=f"Failed to get a response. ({e})",
                    mention_author=False,
                )
            except Exception as e:
                logger.exception("Unexpected error in channel chat")
                await message.reply(
                    content="An unexpected error occurred.",
                    mention_author=False,
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelChatCog(bot, bot.api_client, bot.config))
