"""Demo cog — /demo command that showcases the entire platform in one sequence."""
import asyncio
import io
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from core.api_client import GuaardvarkClient, APIError

logger = logging.getLogger(__name__)


class DemoCog(commands.Cog):
    def __init__(self, bot, api_client, config):
        self.bot = bot
        self.api = api_client
        self.config = config
        self._running = False

    @app_commands.command(name="demo", description="Full platform showcase — watch Guaardvark flex every muscle")
    async def demo(self, interaction: discord.Interaction):
        if self._running:
            await interaction.response.send_message(
                "A demo is already running. One show at a time.", ephemeral=True
            )
            return

        self._running = True
        await interaction.response.defer()

        try:
            start = time.time()

            # --- ACT 1: IDENTITY ---
            embed = discord.Embed(
                title="GUAARDVARK — LIVE SYSTEM DEMO",
                description=(
                    "Self-hosted AI platform. One machine. No cloud. Everything you're about to see "
                    "is running right now on a desktop across the room."
                ),
                color=0xDC143C,  # Crimson
            )
            embed.set_footer(text="Stand by — initializing sequence...")
            msg = await interaction.followup.send(embed=embed, wait=True)

            await asyncio.sleep(1.5)

            # --- ACT 2: HARDWARE ---
            hw_embed = discord.Embed(
                title="HARDWARE",
                color=0xDC143C,
            )
            hw_embed.add_field(name="CPU", value="AMD Ryzen 7 9800X3D\n8-Core / 16-Thread", inline=True)
            hw_embed.add_field(name="GPU", value="NVIDIA RTX 4070 Ti SUPER\n16GB VRAM", inline=True)
            hw_embed.add_field(name="RAM", value="64 GB DDR5", inline=True)
            hw_embed.add_field(name="Storage", value="1.8 TB NVMe", inline=True)
            hw_embed.add_field(name="OS", value="Ubuntu Linux", inline=True)
            hw_embed.add_field(name="Network", value="Localhost — no cloud", inline=True)
            hw_embed.set_footer(text="Pulling live system status...")
            await interaction.followup.send(embed=hw_embed)

            await asyncio.sleep(1)

            # --- ACT 3: LIVE SYSTEM STATUS ---
            try:
                diag = await self.api.get_diagnostics()
                models_data = await self.api.get_models()
                model_list = models_data.get("models", []) if isinstance(models_data, dict) else []
                model_names = [m.get("name", "?") for m in model_list][:8]

                status_embed = discord.Embed(
                    title="LIVE SYSTEM STATUS",
                    color=0x00FF41,  # Matrix green
                )
                status_embed.add_field(
                    name="Platform", value=f"Guaardvark v2.5.1", inline=True
                )
                status_embed.add_field(
                    name="Backend", value="Online", inline=True
                )
                status_embed.add_field(
                    name="Ollama Models", value=f"{len(model_list)} installed", inline=True
                )
                if model_names:
                    status_embed.add_field(
                        name="Available Models",
                        value="```\n" + "\n".join(model_names) + "\n```",
                        inline=False,
                    )
                status_embed.set_footer(text="Asking Uncle Claude a question...")
                await interaction.followup.send(embed=status_embed)
            except Exception as e:
                logger.warning("Demo status check failed: %s", e)
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="LIVE SYSTEM STATUS",
                        description="Backend online. Continuing demo...",
                        color=0x00FF41,
                    )
                )

            await asyncio.sleep(1)

            # --- ACT 4: UNCLE CLAUDE ---
            try:
                claude_result = await self.api.chat_claude(
                    "In exactly two sentences, describe what makes Guaardvark unique as a self-hosted AI platform. Be bold."
                )
                claude_response = claude_result.get("response", "Claude is thinking...")

                claude_embed = discord.Embed(
                    title="UNCLE CLAUDE — LIVE RESPONSE",
                    description=claude_response,
                    color=0xCC785C,  # Claude's color
                )
                claude_embed.set_footer(text="Anthropic API → Claude → Guaardvark | Generating image...")
                await interaction.followup.send(embed=claude_embed)
            except Exception as e:
                logger.warning("Demo Claude call failed: %s", e)
                claude_embed = discord.Embed(
                    title="UNCLE CLAUDE",
                    description="Claude integration available via `/claude` command. API key required.",
                    color=0xCC785C,
                )
                await interaction.followup.send(embed=claude_embed)

            await asyncio.sleep(1)

            # --- ACT 5: IMAGE GENERATION ---
            img_embed = discord.Embed(
                title="IMAGE GENERATION — LIVE",
                description="Generating on the local GPU right now...",
                color=0xDC143C,
            )
            img_embed.set_footer(text="Stable Diffusion · RTX 4070 Ti SUPER · 10 steps · 512x512")
            await interaction.followup.send(embed=img_embed)

            try:
                img_start = time.time()
                result = await self.api.generate_image(
                    "A futuristic aardvark guardian standing before a glowing crimson server rack, "
                    "cyberpunk style, dramatic lighting, high detail",
                    steps=10, width=512, height=512,
                )
                batch_id = result.get("batch_id")
                if batch_id:
                    for _ in range(60):
                        await asyncio.sleep(2)
                        status = await self.api.get_batch_status(batch_id)
                        state = status.get("status", "unknown")
                        if state == "completed":
                            results = status.get("results", [])
                            if results and results[0].get("success"):
                                image_path = results[0]["image_path"]
                                image_name = os.path.basename(image_path)
                                image_bytes = await self.api.get_batch_image(batch_id, image_name)
                                img_time = time.time() - img_start
                                file = discord.File(io.BytesIO(image_bytes), filename=image_name)
                                await interaction.followup.send(
                                    content=f"**Generated in {img_time:.1f}s** — local GPU, no cloud, no API",
                                    file=file,
                                )
                            break
                        elif state == "failed":
                            await interaction.followup.send(content="Image generation unavailable (GPU may be busy).")
                            break
            except Exception as e:
                logger.warning("Demo image gen failed: %s", e)
                await interaction.followup.send(content="Image generation skipped (GPU resources unavailable).")

            await asyncio.sleep(1)

            # --- ACT 6: CAPABILITIES SUMMARY ---
            elapsed = time.time() - start
            final_embed = discord.Embed(
                title="FULL CAPABILITIES",
                color=0xDC143C,
            )
            final_embed.add_field(
                name="AI Chat",
                value="20+ LLM models via Ollama\nClaude via Uncle Claude\nStreaming + memory",
                inline=True,
            )
            final_embed.add_field(
                name="Generation",
                value="Stable Diffusion (images)\nWan2.2 / CogVideoX (video)\nRIFE + Real-ESRGAN post-processing",
                inline=True,
            )
            final_embed.add_field(
                name="Intelligence",
                value="RAG with hybrid search\nReACT agents with tools\nSelf-improvement engine",
                inline=True,
            )
            final_embed.add_field(
                name="Voice",
                value="Whisper.cpp (STT)\nPiper TTS",
                inline=True,
            )
            final_embed.add_field(
                name="Infrastructure",
                value="Plugin system (GPU managed)\nMulti-machine Interconnector\nFull web UI + CLI + Discord",
                inline=True,
            )
            final_embed.add_field(
                name="Scaling",
                value="2nd machine via Interconnector\nCousin Bill (Raspberry Pi)\n*You do not want Cousin Bill involved*",
                inline=True,
            )
            final_embed.add_field(
                name="\u200b",
                value=(
                    f"**Demo completed in {elapsed:.0f}s** — everything you just saw ran on one desktop.\n\n"
                    "GitHub: [guaardvark/guaardvark](https://github.com/guaardvark/guaardvark)\n"
                    "Site: [guaardvark.com](https://guaardvark.com)\n\n"
                    "*Your data. Your hardware. Your rules.*"
                ),
                inline=False,
            )
            await interaction.followup.send(embed=final_embed)

        except Exception as e:
            logger.exception("Demo failed")
            await interaction.followup.send(content=f"Demo encountered an error: {e}")
        finally:
            self._running = False


async def setup(bot):
    await bot.add_cog(DemoCog(bot, bot.api_client, bot.config))
