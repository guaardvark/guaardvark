"""Voice cog — /voice join, /voice leave, /voice status commands."""
import logging
import discord
from discord import app_commands
from discord.ext import commands
from core.api_client import GuaardvarkClient
from core.voice_handler import VoiceHandler

logger = logging.getLogger(__name__)


class VoiceCog(commands.Cog):
    def __init__(self, bot, api_client, config):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.handlers: dict[int, VoiceHandler] = {}

    voice_group = app_commands.Group(name="voice", description="Voice channel commands")

    @voice_group.command(name="join", description="Join your voice channel")
    async def voice_join(self, interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "Join a voice channel first (click one in the channel list, e.g. under the speaker icon) — "
                "then run `/voice join` and I'll hop into it with you.",
                ephemeral=True,
            )
            return
        guild_id = interaction.guild.id
        channel = interaction.user.voice.channel
        if guild_id in self.handlers:
            await self.handlers[guild_id].leave()
        handler = VoiceHandler(self.api, self.config)
        success = await handler.join(channel, interaction.channel)
        if success:
            self.handlers[guild_id] = handler
            embed = discord.Embed(title="Voice Connected", description=f"Joined **{channel.name}**. Speak and I'll respond!", color=discord.Color.green())
            embed.set_footer(text="Use /voice leave to disconnect")
            await interaction.response.send_message(embed=embed)
            # Spoken welcome — announce in the voice channel via TTS, alongside the embed.
            greeting = (self.config.get("voice", {}).get("join_greeting") or "").strip()
            if greeting:
                try:
                    await handler.speak(greeting)
                except Exception:
                    logger.exception("Failed to speak join greeting")
        else:
            await interaction.response.send_message("Failed to join voice channel.", ephemeral=True)

    @voice_group.command(name="leave", description="Leave the voice channel")
    async def voice_leave(self, interaction):
        guild_id = interaction.guild.id
        handler = self.handlers.pop(guild_id, None)
        if handler:
            await handler.leave()
            await interaction.response.send_message("Disconnected from voice channel.")
        else:
            await interaction.response.send_message("Not connected to a voice channel.", ephemeral=True)

    @voice_group.command(name="say", description="Speak the given text in the voice channel (TTS test)")
    async def voice_say(self, interaction, text: str):
        handler = self.handlers.get(interaction.guild.id)
        if not handler or not handler.voice_client or not handler.voice_client.is_connected():
            await interaction.response.send_message("Not connected — use /voice join first.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await handler.speak(text)
            await interaction.followup.send(f"Spoke {len(text)} chars via TTS.")
        except Exception as e:
            logger.exception("/voice say failed")
            await interaction.followup.send(f"TTS failed: {e}")

    @voice_group.command(name="status", description="Show voice session info")
    async def voice_status(self, interaction):
        guild_id = interaction.guild.id
        handler = self.handlers.get(guild_id)
        if not handler or not handler.voice_client or not handler.voice_client.is_connected():
            await interaction.response.send_message("Not connected to a voice channel.", ephemeral=True)
            return
        channel = handler.voice_client.channel
        embed = discord.Embed(title="Voice Status", color=discord.Color.blue())
        embed.add_field(name="Channel", value=channel.name, inline=True)
        embed.add_field(name="Members", value=str(len(channel.members)), inline=True)
        embed.add_field(name="Processing", value="Yes" if handler._processing else "Idle", inline=True)
        embed.add_field(name="Listening", value="Yes" if handler.listening else "No (playback-only)", inline=True)
        if handler.sink:
            with handler.sink.lock:
                buffered = sum(1 for b in handler.sink.buffers.values() if b)
            embed.add_field(name="Speakers buffered", value=str(buffered), inline=True)
        await interaction.response.send_message(embed=embed)

    async def cog_unload(self):
        for handler in self.handlers.values():
            await handler.leave()
        self.handlers.clear()


async def setup(bot):
    await bot.add_cog(VoiceCog(bot, bot.api_client, bot.config))
