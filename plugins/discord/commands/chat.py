"""Chat cog — /ask command for LLM conversation via Guaardvark unified chat."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.api_client import GuaardvarkClient, APIError
from core.approvals import make_approval_handler
from core.chat_reply import (
    attachment_to_b64,
    files_from_generated,
    send_chunks,
    user_session_id,
)
from core.rate_limiter import RateLimiter
from core.security import sanitize_input, is_channel_allowed

logger = logging.getLogger("discord_bot")


class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["ask"], window_seconds=60
        )

    def _auto_approve(self) -> bool:
        return bool(self.config.get("tools", {}).get("auto_approve", False))

    @app_commands.command(name="ask", description="Chat with Guaardvark AI")
    @app_commands.describe(
        prompt="Your message or question",
        image="Optional image to send with the message",
    )
    async def ask(
        self,
        interaction: discord.Interaction,
        prompt: str,
        image: discord.Attachment | None = None,
    ):
        await self._handle_ask(interaction, prompt, image=image)

    async def _handle_ask(self, interaction, prompt: str, image=None):
        if interaction.guild and not is_channel_allowed(
            interaction.channel.id, self.config["security"]["allowed_channels"]
        ):
            await interaction.response.send_message(
                "Bot not allowed in this channel.", ephemeral=True
            )
            return

        allowed, _, retry_after = self.rate_limiter.check(
            interaction.user.id, "ask"
        )
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True
            )
            return

        cleaned = sanitize_input(
            prompt, max_length=self.config["security"]["max_prompt_length"]
        )
        if not cleaned and image is None:
            await interaction.response.send_message(
                "Your message was empty after sanitization.", ephemeral=True
            )
            return
        if not cleaned:
            cleaned = "Describe this image."

        image_b64 = None
        if image is not None:
            image_b64 = await attachment_to_b64(image)
            if image_b64 is None:
                await interaction.response.send_message(
                    "Could not read that image (missing or too large).",
                    ephemeral=True,
                )
                return

        await interaction.response.defer()

        async def send_fn(content, *, files=None, view=None):
            kwargs = {"content": content}
            if files:
                kwargs["files"] = files
            if view is not None:
                kwargs["view"] = view
            return await interaction.followup.send(**kwargs)

        try:
            logger.info("[/ask] user=%s msg=%r", interaction.user, cleaned[:100])
            session_id = user_session_id(interaction.user.id)
            result = await self.api.unified_chat(
                cleaned,
                session_id=session_id,
                image=image_b64,
                approval_handler=make_approval_handler(
                    send_fn, interaction.user.id, auto_approve=self._auto_approve()
                ),
            )
            response_text = result.get("response", "No response received.")
            logger.info("[/ask] response=%r", response_text[:100])
            files = await files_from_generated(
                self.api, result.get("generated_images") or []
            )
            await send_chunks(send_fn, response_text, files=files)

        except APIError as e:
            logger.error("Chat API error: %s", e)
            await interaction.followup.send(
                content=f"Failed to get a response. Guaardvark may be offline. ({e})"
            )
        except Exception:
            logger.exception("Unexpected error in /ask")
            await interaction.followup.send(
                content="An unexpected error occurred. Please try again."
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ChatCog(bot, bot.api_client, bot.config))
