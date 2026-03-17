"""Search cog — /search command for semantic RAG search."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.core.api_client import GuaardvarkClient, APIError
from discord_bot.core.rate_limiter import RateLimiter
from discord_bot.core.security import sanitize_input

logger = logging.getLogger(__name__)


class SearchCog(commands.Cog):
    def __init__(self, bot, api_client, config):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["search"], window_seconds=60
        )

    @app_commands.command(
        name="search", description="Search Guaardvark's knowledge base"
    )
    @app_commands.describe(query="What to search for")
    async def search(self, interaction: discord.Interaction, query: str):
        await self._handle_search(interaction, query)

    async def _handle_search(self, interaction, query: str):
        allowed, _, retry_after = self.rate_limiter.check(interaction.user.id, "search")
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True
            )
            return

        cleaned = sanitize_input(
            query, max_length=self.config["security"]["max_prompt_length"]
        )
        if not cleaned:
            await interaction.response.send_message(
                "Query was empty after sanitization.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            result = await self.api.semantic_search(cleaned)
            answer = result.get("answer") or "No matching results found."
            sources = result.get("sources", [])

            embed = discord.Embed(
                title=f"Search: {cleaned[:100]}",
                description=answer[:4096],
                color=discord.Color.blue(),
            )
            if sources:
                source_text = "\n".join(
                    f"**{i+1}.** {s.get('content', 'N/A')[:200]}"
                    for i, s in enumerate(sources[:5])
                )
                if source_text:
                    embed.add_field(
                        name="Sources", value=source_text[:1024], inline=False
                    )
            embed.set_footer(text="Guaardvark Semantic Search")
            await interaction.followup.send(embed=embed)

        except APIError as e:
            await interaction.followup.send(content=f"Search failed: {e}")


async def setup(bot):
    await bot.add_cog(SearchCog(bot, bot.api_client, bot.config))
