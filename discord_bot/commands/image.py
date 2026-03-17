"""Image cog — /imagine and /enhance-prompt commands."""

import asyncio
import io
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.core.api_client import GuaardvarkClient, APIError
from discord_bot.core.rate_limiter import RateLimiter
from discord_bot.core.security import sanitize_input

logger = logging.getLogger(__name__)
MAX_POLL_ATTEMPTS = 60
POLL_INTERVAL = 2


class ImageCog(commands.Cog):
    def __init__(self, bot, api_client, config):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["imagine"], window_seconds=60
        )
        self.enhance_limiter = RateLimiter(
            max_requests=config["rate_limits"]["enhance_prompt"], window_seconds=60
        )
        self._active_jobs = 0

    @app_commands.command(name="imagine", description="Generate an image with AI")
    @app_commands.describe(
        prompt="What to generate",
        steps="Inference steps (default 20)",
        size="Image size: 512, 768, or 1024",
    )
    async def imagine(
        self, interaction, prompt: str, steps: int = None, size: int = None
    ):
        await self._handle_imagine(interaction, prompt, steps, size)

    async def _handle_imagine(self, interaction, prompt, steps=None, size=None):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Image generation is not available in DMs.", ephemeral=True
            )
            return

        allowed, _, retry_after = self.rate_limiter.check(
            interaction.user.id, "imagine"
        )
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True
            )
            return

        img_config = self.config["image"]
        if self._active_jobs >= img_config.get("max_queue_depth", 5):
            await interaction.response.send_message(
                "GPU queue is full. Please wait.", ephemeral=True
            )
            return

        cleaned = sanitize_input(
            prompt, max_length=self.config["security"]["max_image_prompt_length"]
        )
        if not cleaned:
            await interaction.response.send_message("Prompt was empty.", ephemeral=True)
            return

        await interaction.response.defer()
        self._active_jobs += 1

        try:
            result = await self.api.generate_image(
                cleaned,
                steps=steps or img_config["default_steps"],
                width=size or img_config["default_size"],
                height=size or img_config["default_size"],
            )
            batch_id = result.get("batch_id")
            if not batch_id:
                await interaction.followup.send(
                    content="Failed to start image generation."
                )
                return

            for _ in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)
                status = await self.api.get_batch_status(batch_id)
                state = status.get("status", "unknown")

                if state == "completed":
                    results = status.get("results", [])
                    if results and results[0].get("success"):
                        image_path = results[0]["image_path"]
                        image_name = os.path.basename(image_path)
                        image_bytes = await self.api.get_batch_image(
                            batch_id, image_name
                        )
                        file = discord.File(
                            io.BytesIO(image_bytes), filename=image_name
                        )
                        await interaction.followup.send(
                            content=f"**Prompt:** {cleaned[:200]}", file=file
                        )
                    else:
                        error = (
                            results[0].get("error", "Unknown")
                            if results
                            else "No results"
                        )
                        await interaction.followup.send(
                            content=f"Image generation failed: {error}"
                        )
                    return
                elif state == "failed":
                    await interaction.followup.send(
                        content=f"Image generation failed: {status.get('error', 'Unknown')}"
                    )
                    return

            await interaction.followup.send(content="Image generation timed out.")

        except APIError as e:
            await interaction.followup.send(content=f"Image generation error: {e}")
        except Exception as e:
            logger.exception("Unexpected error in /imagine")
            await interaction.followup.send(content="An unexpected error occurred.")
        finally:
            self._active_jobs = max(0, self._active_jobs - 1)

    @app_commands.command(
        name="enhance-prompt", description="Improve an image generation prompt"
    )
    @app_commands.describe(prompt="The prompt to enhance")
    async def enhance_prompt(self, interaction, prompt: str):
        await self._handle_enhance(interaction, prompt)

    async def _handle_enhance(self, interaction, prompt):
        allowed, _, retry_after = self.enhance_limiter.check(
            interaction.user.id, "enhance_prompt"
        )
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True
            )
            return

        cleaned = sanitize_input(
            prompt, max_length=self.config["security"]["max_image_prompt_length"]
        )
        if not cleaned:
            await interaction.response.send_message("Prompt was empty.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            result = await self.api.enhance_prompt(cleaned)
            enhanced = result.get("enhanced_prompt", "Enhancement failed.")
            negative = result.get("negative_prompt", "")

            embed = discord.Embed(title="Enhanced Prompt", color=discord.Color.green())
            embed.add_field(name="Original", value=cleaned[:1024], inline=False)
            embed.add_field(name="Enhanced", value=enhanced[:1024], inline=False)
            if negative:
                embed.add_field(
                    name="Negative Prompt", value=negative[:1024], inline=False
                )
            embed.set_footer(text="Use /imagine with the enhanced prompt")
            await interaction.followup.send(embed=embed)

        except APIError as e:
            await interaction.followup.send(content=f"Prompt enhancement failed: {e}")


async def setup(bot):
    await bot.add_cog(ImageCog(bot, bot.api_client, bot.config))
