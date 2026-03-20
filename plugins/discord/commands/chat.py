"""Chat cog — /ask command for LLM conversation."""
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.api_client import GuaardvarkClient, APIError
from core.rate_limiter import RateLimiter
from core.security import sanitize_input, is_channel_allowed

logger = logging.getLogger("discord_bot")
DISCORD_MAX_LENGTH = 2000


def split_message(text: str, max_length: int = DISCORD_MAX_LENGTH) -> list[str]:
    """Split a long message into chunks that fit Discord's limit."""
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


class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["ask"], window_seconds=60
        )
        # In-memory conversation history per user
        self._history: dict[int, list[dict]] = {}
        self._max_history = 20

    def _get_history(self, user_id: int) -> list[dict]:
        return self._history.get(user_id, [])

    def _add_to_history(self, user_id: int, role: str, content: str):
        if user_id not in self._history:
            self._history[user_id] = []
        self._history[user_id].append({"role": role, "content": content})
        if len(self._history[user_id]) > self._max_history:
            self._history[user_id] = self._history[user_id][-self._max_history:]

    @app_commands.command(name="ask", description="Chat with Guaardvark AI")
    @app_commands.describe(prompt="Your message or question")
    async def ask(self, interaction: discord.Interaction, prompt: str):
        await self._handle_ask(interaction, prompt)

    async def _handle_ask(self, interaction, prompt: str):
        # Channel allowlist check
        if interaction.guild and not is_channel_allowed(
            interaction.channel.id, self.config["security"]["allowed_channels"]
        ):
            await interaction.response.send_message(
                "Bot not allowed in this channel.", ephemeral=True
            )
            return

        # Rate limit
        allowed, remaining, retry_after = self.rate_limiter.check(
            interaction.user.id, "ask"
        )
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True
            )
            return

        # Sanitize
        cleaned = sanitize_input(
            prompt, max_length=self.config["security"]["max_prompt_length"]
        )
        if not cleaned:
            await interaction.response.send_message(
                "Your message was empty after sanitization.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            logger.info("[/ask] user=%s msg=%r", interaction.user, cleaned[:100])
            history = self._get_history(interaction.user.id)
            result = await self.api.chat_claude(cleaned, history=history)
            response_text = result.get("response", "No response received.")
            logger.info("[/ask] response=%r", response_text[:100])

            # Update history
            self._add_to_history(interaction.user.id, "user", cleaned)
            self._add_to_history(interaction.user.id, "assistant", response_text)

            if len(response_text) > 4000:
                file = discord.File(
                    io.BytesIO(response_text.encode()), filename="response.md"
                )
                await interaction.followup.send(
                    content=f"Response too long ({len(response_text)} chars). See attached file.",
                    file=file,
                )
            else:
                for chunk in split_message(response_text):
                    await interaction.followup.send(content=chunk)

        except APIError as e:
            logger.error("Chat API error: %s", e)
            await interaction.followup.send(
                content=f"Failed to get a response. The backend may be offline. ({e})"
            )
        except Exception as e:
            logger.exception("Unexpected error in /ask")
            await interaction.followup.send(
                content="An unexpected error occurred. Please try again."
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ChatCog(bot, bot.api_client, bot.config))
