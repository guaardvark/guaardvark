"""CLI proxy cog — /guaardvark command to run CLI from Discord."""
import asyncio
import io
import logging
import os
import shlex

import discord
from discord import app_commands
from discord.ext import commands

from core.security import sanitize_input

logger = logging.getLogger(__name__)
GUAARDVARK_ROOT = os.environ.get("GUAARDVARK_ROOT", "")

BLOCKED_SUBCOMMANDS = {"system shutdown"}


class CLIProxyCog(commands.Cog):
    def __init__(self, bot, api_client, config):
        self.bot = bot
        self.config = config
        self.admin_roles = set(config["security"]["admin_roles"])

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            return False
        return any(role.name in self.admin_roles for role in member.roles)

    @app_commands.command(name="guaardvark", description="Run a Guaardvark CLI command (admin only)")
    @app_commands.describe(command="The CLI command to run (e.g. 'system status', 'rag status', 'agents list')")
    async def guaardvark_cli(self, interaction: discord.Interaction, command: str):
        await self._handle_cli(interaction, command)

    async def _handle_cli(self, interaction, command: str):
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "This command requires Admin or Bot Admin role.", ephemeral=True
            )
            return

        if ".." in command:
            await interaction.response.send_message(
                "Invalid command: path traversal not allowed.", ephemeral=True
            )
            return

        cmd_lower = command.strip().lower()
        for blocked in BLOCKED_SUBCOMMANDS:
            if cmd_lower.startswith(blocked):
                await interaction.response.send_message(
                    f"Command `{blocked}` is blocked from Discord.", ephemeral=True
                )
                return

        await interaction.response.defer()

        try:
            args = ["guaardvark", "--json"] + shlex.split(command)

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=GUAARDVARK_ROOT or None,
                env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                await interaction.followup.send(content="Command timed out (30s limit).")
                return

            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                err_text = stderr.decode("utf-8", errors="replace")
                if err_text.strip():
                    output += f"\n\nSTDERR:\n{err_text}"

            if not output.strip():
                output = "(no output)"

            if proc.returncode != 0:
                output = f"Exit code: {proc.returncode}\n\n{output}"

            if len(output) > 1900:
                file = discord.File(
                    io.BytesIO(output.encode()), filename="cli_output.txt"
                )
                await interaction.followup.send(
                    content=f"`guaardvark {command}`", file=file
                )
            else:
                await interaction.followup.send(
                    content=f"```\n$ guaardvark {command}\n\n{output}\n```"
                )

        except FileNotFoundError:
            await interaction.followup.send(
                content="CLI not found. Is `guaardvark` installed? (`pip install -e cli/`)"
            )
        except Exception as e:
            logger.exception("Error in /guaardvark CLI proxy")
            await interaction.followup.send(content=f"Error: {e}")


async def setup(bot):
    await bot.add_cog(CLIProxyCog(bot, bot.api_client, bot.config))
