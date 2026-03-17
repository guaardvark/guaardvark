"""System cog — /status, /models, /switch-model commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.core.api_client import GuaardvarkClient, APIError
from discord_bot.core.security import is_admin

logger = logging.getLogger(__name__)


class SystemCog(commands.Cog):
    def __init__(self, bot, api_client, config):
        self.bot = bot
        self.api = api_client
        self.config = config

    @app_commands.command(name="status", description="Show Guaardvark system status")
    @app_commands.describe(detailed="Show detailed diagnostics (admin only)")
    async def status(self, interaction, detailed: bool = False):
        await self._handle_status(interaction, detailed)

    async def _handle_status(self, interaction, detailed=False):
        await interaction.response.defer()

        try:
            data = await self.api.get_diagnostics()
            embed = discord.Embed(
                title="Guaardvark Status", color=discord.Color.green()
            )
            embed.add_field(
                name="Model", value=data.get("active_model", "N/A"), inline=True
            )
            embed.add_field(
                name="Ollama",
                value="Online" if data.get("ollama_reachable") else "Offline",
                inline=True,
            )
            embed.add_field(
                name="Models", value=str(data.get("model_count", "?")), inline=True
            )
            embed.add_field(
                name="Documents",
                value=str(data.get("document_count", "?")),
                inline=True,
            )
            embed.add_field(name="Version", value=data.get("version", "?"), inline=True)
            embed.add_field(
                name="Platform", value=data.get("platform", "?"), inline=True
            )

            if detailed:
                if not is_admin(
                    interaction.user, self.config["security"]["admin_roles"]
                ):
                    embed.set_footer(text="Detailed view requires Admin role.")
                else:
                    try:
                        metrics = await self.api.get_detailed_diagnostics()
                        for key, val in list(metrics.items())[:10]:
                            embed.add_field(name=key, value=str(val)[:100], inline=True)
                    except APIError:
                        embed.set_footer(text="Could not fetch detailed metrics.")

            await interaction.followup.send(embed=embed)

        except APIError as e:
            await interaction.followup.send(content=f"Failed to get status: {e}")

    @app_commands.command(name="models", description="List available LLM models")
    async def models(self, interaction):
        await self._handle_models(interaction)

    async def _handle_models(self, interaction):
        await interaction.response.defer()

        try:
            data = await self.api.get_models()
            models = data.get("models", [])
            if not models:
                await interaction.followup.send(content="No models available.")
                return

            embed = discord.Embed(title="Available Models", color=discord.Color.blue())
            for m in models[:25]:
                name = m.get("name", "unknown")
                details = m.get("details", {})
                size = details.get("parameter_size", "?")
                quant = details.get("quantization_level", "?")
                embed.add_field(name=name, value=f"{size} ({quant})", inline=True)
            await interaction.followup.send(embed=embed)

        except APIError as e:
            await interaction.followup.send(content=f"Failed to get models: {e}")

    @app_commands.command(
        name="switch-model",
        description="Switch the active LLM model (admin only)",
    )
    @app_commands.describe(model_name="Name of the model to switch to")
    async def switch_model(self, interaction, model_name: str):
        await self._handle_switch_model(interaction, model_name)

    async def _handle_switch_model(self, interaction, model_name):
        if not is_admin(interaction.user, self.config["security"]["admin_roles"]):
            await interaction.response.send_message(
                "You need an Admin role to switch models.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            result = await self.api.switch_model(model_name)
            await interaction.followup.send(
                content=f"Model switch initiated: {result.get('message', 'Switching...')}"
            )
        except APIError as e:
            await interaction.followup.send(content=f"Model switch failed: {e}")


async def setup(bot):
    await bot.add_cog(SystemCog(bot, bot.api_client, bot.config))
