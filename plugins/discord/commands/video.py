"""Video cog — /video command for AI video generation."""
import asyncio
import io
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from core.api_client import GuaardvarkClient, APIError
from core.rate_limiter import RateLimiter
from core.security import sanitize_input

logger = logging.getLogger(__name__)
MAX_POLL_ATTEMPTS = 150
POLL_INTERVAL = 2


class VideoCog(commands.Cog):
    def __init__(self, bot, api_client, config):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["video"], window_seconds=60
        )
        self._active_jobs = 0

    @app_commands.command(name="video", description="Generate a video with AI")
    @app_commands.describe(
        prompt="What to generate",
        steps="Inference steps (default 20)",
    )
    async def video(self, interaction, prompt: str, steps: int = 20):
        await self._handle_video(interaction, prompt, steps)

    async def _handle_video(self, interaction, prompt, steps=20):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Video generation is not available in DMs.", ephemeral=True
            )
            return

        allowed, _, retry_after = self.rate_limiter.check(
            interaction.user.id, "video"
        )
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True
            )
            return

        if self._active_jobs >= 2:
            await interaction.response.send_message(
                "Video generation queue is full. Please wait.", ephemeral=True
            )
            return

        cleaned = sanitize_input(
            prompt, max_length=self.config["security"]["max_image_prompt_length"]
        )
        if not cleaned:
            await interaction.response.send_message(
                "Prompt was empty.", ephemeral=True
            )
            return

        await interaction.response.defer()
        self._active_jobs += 1

        try:
            result = await self.api.generate_video([cleaned], num_inference_steps=steps)
            batch_id = result.get("batch_id")
            if not batch_id:
                await interaction.followup.send(content="Failed to start video generation.")
                return

            await interaction.followup.send(
                content=f"Video generation started. This may take a few minutes...\n**Prompt:** {cleaned[:200]}"
            )

            for i in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)
                status = await self.api.get_video_status(batch_id)
                state = status.get("status", "unknown")

                if state == "completed":
                    results = status.get("results", [])
                    if results and results[0].get("success"):
                        video_path = results[0]["video_path"]
                        video_name = os.path.basename(video_path)
                        video_bytes = await self.api.get_video_bytes(batch_id, video_name)

                        if len(video_bytes) > 25 * 1024 * 1024:
                            await interaction.followup.send(
                                content=f"Video generated but too large to upload ({len(video_bytes) // 1024 // 1024}MB). Access it from the web UI."
                            )
                        else:
                            file = discord.File(io.BytesIO(video_bytes), filename=video_name)
                            await interaction.followup.send(file=file)
                    else:
                        error = results[0].get("error", "Unknown") if results else "No results"
                        await interaction.followup.send(content=f"Video generation failed: {error}")
                    return
                elif state == "failed":
                    await interaction.followup.send(
                        content=f"Video generation failed: {status.get('error', 'Unknown')}"
                    )
                    return

            await interaction.followup.send(content="Video generation timed out (5 min limit).")

        except APIError as e:
            await interaction.followup.send(content=f"Video generation error: {e}")
        except Exception as e:
            logger.exception("Unexpected error in /video")
            await interaction.followup.send(content="An unexpected error occurred.")
        finally:
            self._active_jobs = max(0, self._active_jobs - 1)


async def setup(bot):
    await bot.add_cog(VideoCog(bot, bot.api_client, bot.config))
