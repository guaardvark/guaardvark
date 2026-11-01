"""Image cog — /imagine and /enhance-prompt commands.

Supports Cast Library LoRAs via:
  - optional ``character`` slash arg (name, trigger, or numeric ID + autocomplete)
  - backend auto-resolve from trigger tokens in the prompt (e.g. ``[batman_2]``)
"""
import asyncio
import io
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from core import public_mode
from core.api_client import GuaardvarkClient, APIError
from core.rate_limiter import RateLimiter
from core.security import sanitize_input

logger = logging.getLogger(__name__)
MAX_POLL_ATTEMPTS = 60
POLL_INTERVAL = 2
# Cache trained cast list for autocomplete (seconds)
_CAST_CACHE_TTL = 60


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
        self._cast_cache: list[dict] = []
        self._cast_cache_at: float = 0.0

    async def _trained_cast(self) -> list[dict]:
        """Return trained character subjects (id, name, trigger_word), cached briefly."""
        now = time.monotonic()
        if self._cast_cache and (now - self._cast_cache_at) < _CAST_CACHE_TTL:
            return self._cast_cache
        try:
            subjects = await self.api.list_cast_subjects()
        except Exception as e:
            logger.warning("Failed to list cast subjects: %s", e)
            return self._cast_cache or []
        trained = []
        for s in subjects or []:
            if not isinstance(s, dict):
                continue
            if s.get("kind") and s.get("kind") != "character":
                continue
            if not s.get("lora_path"):
                continue
            trained.append({
                "id": int(s["id"]),
                "name": (s.get("name") or f"Subject {s['id']}").strip(),
                "trigger_word": (s.get("trigger_word") or "").strip(),
            })
        self._cast_cache = trained
        self._cast_cache_at = now
        return trained

    def _resolve_character_arg(self, character: str, trained: list[dict]) -> list[int]:
        """Map character slash text → subject_ids (numeric ID, name, or trigger)."""
        if not character or not character.strip():
            return []
        raw = character.strip()
        # Autocomplete values are "Name (trigger)" or "Name (#id)"
        # Prefer exact id if user typed a number
        try:
            sid = int(raw)
            if any(t["id"] == sid for t in trained):
                return [sid]
        except ValueError:
            pass

        key = raw.lower()
        # Strip trailing " (#123)" / " (trigger)" from autocomplete labels
        if " (" in key and key.endswith(")"):
            key = key.rsplit(" (", 1)[0].strip()

        for t in trained:
            names = {
                t["name"].lower(),
                t["name"].lower().replace(" ", "_"),
            }
            if t["trigger_word"]:
                names.add(t["trigger_word"].lower())
                names.add(t["trigger_word"].lower().replace(" ", "_"))
            if key in names or key == str(t["id"]):
                return [t["id"]]
            # Prefix match for partial autocomplete picks
            if any(n.startswith(key) for n in names):
                return [t["id"]]
        return []

    async def character_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if public_mode.enabled(self.config):
            return []
        trained = await self._trained_cast()
        cur = (current or "").lower().strip()
        choices = []
        for t in trained:
            label = t["name"]
            if t["trigger_word"]:
                label = f"{t['name']} ({t['trigger_word']})"
            else:
                label = f"{t['name']} (#{t['id']})"
            hay = f"{t['name']} {t['trigger_word']} {t['id']}".lower()
            if cur and cur not in hay:
                continue
            # Choice value must be ≤100 chars; use id for unambiguous resolve
            choices.append(app_commands.Choice(name=label[:100], value=str(t["id"])))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="imagine", description="Generate an image with AI (supports Cast LoRAs)")
    @app_commands.describe(
        prompt="What to generate — include [trigger_word] or pick a character",
        character="Trained Cast character (LoRA) — autocomplete by name",
        steps="Inference steps (default 9 for modern models)",
        size="Image size: 768 or 1024 (default 1024)",
    )
    @app_commands.autocomplete(character=character_autocomplete)
    @app_commands.choices(
        size=[
            app_commands.Choice(name="1024", value=1024),
            app_commands.Choice(name="768", value=768),
        ]
    )
    async def imagine(
        self,
        interaction: discord.Interaction,
        prompt: str,
        character: str = None,
        steps: int = None,
        size: app_commands.Choice[int] = None,
    ):
        size_val = size.value if size is not None else None
        await self._handle_imagine(interaction, prompt, steps, size_val, character)

    async def _handle_imagine(
        self, interaction, prompt, steps=None, size=None, character=None
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Image generation is not available in DMs.", ephemeral=True
            )
            return

        public = public_mode.enabled(self.config)
        if public and interaction.channel_id not in public_mode.channels(self.config):
            await interaction.response.send_message(
                "/imagine is not available in this channel.", ephemeral=True
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
        queue_depth = public_mode.MAX_QUEUE_DEPTH if public else img_config.get("max_queue_depth", 5)
        if self._active_jobs >= queue_depth:
            await interaction.response.send_message(
                "GPU queue is full. Please wait.", ephemeral=True
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

        if public and public_mode.refuses(cleaned):
            logger.info("Public mode refused a prompt from user %s", interaction.user.id)
            await interaction.response.send_message(
                "That prompt can't run on this server.", ephemeral=True
            )
            return
        if public:
            character = None

        await interaction.response.defer()
        self._active_jobs += 1
        if public:
            public_mode.mark_job(True)

        subject_ids: list[int] = []
        cast_label = None
        try:
            if character:
                trained = await self._trained_cast()
                subject_ids = self._resolve_character_arg(character, trained)
                if not subject_ids:
                    await interaction.followup.send(
                        content=(
                            f"Unknown character `{character}`. "
                            "Pick from the autocomplete list, or include a "
                            "`[trigger_word]` in the prompt."
                        )
                    )
                    return
                match = next((t for t in trained if t["id"] == subject_ids[0]), None)
                cast_label = match["name"] if match else str(subject_ids[0])

            dim = size or img_config.get("default_size", 1024)
            # LoRA path floors below 768 → 1024; bump small sizes when casting
            if subject_ids and dim < 768:
                dim = 1024

            result = await self.api.generate_image(
                cleaned,
                steps=steps if steps is not None else img_config.get("default_steps", 9),
                width=dim,
                height=dim,
                subject_ids=subject_ids or None,
                # A Cast character renders on its own LoRA's base model.
                model=None if subject_ids else (img_config.get("model") or None),
                auto_cast=not public,
            )
            batch_id = result.get("batch_id")
            if not batch_id:
                await interaction.followup.send(
                    content="Failed to start image generation."
                )
                return

            # Surface auto-resolved cast from backend validation if we didn't pick one
            if not cast_label:
                warnings = (result.get("validation") or {}).get("warnings") or []
                for w in warnings:
                    if "auto-resolved cast" in str(w).lower():
                        cast_label = "auto from prompt"
                        break

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
                        header = f"**Prompt:** {cleaned[:200]}"
                        if cast_label:
                            header = f"**Cast:** {cast_label}\n{header}"
                        await interaction.followup.send(
                            content=header, file=file
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
            await interaction.followup.send(
                content=f"Image generation error: {e}"
            )
        except Exception:
            logger.exception("Unexpected error in /imagine")
            await interaction.followup.send(
                content="An unexpected error occurred."
            )
        finally:
            self._active_jobs = max(0, self._active_jobs - 1)
            if public and self._active_jobs == 0:
                public_mode.mark_job(False)

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
            await interaction.response.send_message(
                "Prompt was empty.", ephemeral=True
            )
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
            embed.set_footer(
                text="Use /imagine with the enhanced prompt — add a Cast character for LoRA"
            )
            await interaction.followup.send(embed=embed)

        except APIError as e:
            await interaction.followup.send(
                content=f"Prompt enhancement failed: {e}"
            )


async def setup(bot):
    await bot.add_cog(ImageCog(bot, bot.api_client, bot.config))
