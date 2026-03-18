"""Generation cog — /generate-csv command."""
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from core.api_client import GuaardvarkClient, APIError
from core.rate_limiter import RateLimiter
from core.security import sanitize_input

logger = logging.getLogger(__name__)


class GenerationCog(commands.Cog):
    def __init__(self, bot, api_client, config):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["generate_csv"], window_seconds=60
        )

    @app_commands.command(name="generate-csv", description="Generate CSV data with AI")
    @app_commands.describe(description="Describe the data you want generated")
    async def generate_csv(self, interaction, description: str):
        await self._handle_generate(interaction, description)

    async def _handle_generate(self, interaction, description):
        allowed, _, retry_after = self.rate_limiter.check(
            interaction.user.id, "generate_csv"
        )
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True
            )
            return

        cleaned = sanitize_input(
            description, max_length=self.config["security"]["max_prompt_length"]
        )
        if not cleaned:
            await interaction.response.send_message(
                "Description was empty.", ephemeral=True
            )
            return

        await interaction.response.defer()
        output_filename = f"discord_{interaction.user.id}_{int(time.time())}.csv"

        try:
            result = await self.api.generate_csv(cleaned, output_filename)
            message = result.get("message", "Generation complete")
            stats = result.get("statistics", {})
            items = stats.get("generated_items", "?")
            duration = stats.get("processing_time", 0)

            embed = discord.Embed(
                title="CSV Generated",
                description=f"{message}\n\n**Items:** {items}\n**Time:** {duration:.1f}s",
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"File: {output_filename}")
            await interaction.followup.send(embed=embed)

        except APIError as e:
            await interaction.followup.send(content=f"CSV generation failed: {e}")
        except Exception as e:
            logger.exception("Unexpected error in /generate-csv")
            await interaction.followup.send(
                content="An unexpected error occurred."
            )


async def setup(bot):
    await bot.add_cog(GenerationCog(bot, bot.api_client, bot.config))
