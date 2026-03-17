"""Guaardvark Discord Bot — entry point."""

import asyncio
import logging
import os
import re
import signal
import sys

import discord
from discord.ext import commands
import yaml

from discord_bot.core.api_client import GuaardvarkClient

# Setup logging
log_dir = os.path.join(
    os.environ.get(
        "GUAARDVARK_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
    "logs",
)
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "discord_bot.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("discord_bot")


def load_config(path: str = None) -> dict:
    """Load config.yaml, resolving ${ENV_VAR} and ${ENV_VAR:-default} patterns."""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path, "r") as f:
        raw = f.read()

    def env_sub(match):
        var = match.group(1)
        if ":-" in var:
            name, default = var.split(":-", 1)
            return os.environ.get(name, default)
        return os.environ.get(var, match.group(0))

    resolved = re.sub(r"\$\{([^}]+)\}", env_sub, raw)
    return yaml.safe_load(resolved)


COG_MODULES = [
    "discord_bot.commands.chat",
    "discord_bot.commands.search",
    "discord_bot.commands.image",
    "discord_bot.commands.generation",
    "discord_bot.commands.system",
]


class GuaardvarkBot(commands.Bot):
    def __init__(self, config: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(
            command_prefix=config.get("bot", {}).get("prefix", "!"), intents=intents
        )
        self.config = config
        self.api_client = GuaardvarkClient(base_url=config["api"]["base_url"])

    async def setup_hook(self):
        await self.api_client.setup()
        try:
            await self.api_client.health_check()
            logger.info(
                "Guaardvark backend is reachable at %s", self.config["api"]["base_url"]
            )
        except Exception as e:
            logger.warning("Backend health check failed: %s (bot will start anyway)", e)

        for module in COG_MODULES:
            try:
                await self.load_extension(module)
                logger.info("Loaded cog: %s", module)
            except Exception as e:
                logger.error("Failed to load cog %s: %s", module, e)

        if self.config.get("voice", {}).get("enabled", False):
            try:
                await self.load_extension("discord_bot.commands.voice")
                logger.info("Loaded cog: discord_bot.commands.voice")
            except Exception as e:
                logger.warning("Failed to load voice cog: %s (voice disabled)", e)

        guild_id = self.config.get("bot", {}).get("guild_id")
        if guild_id:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced commands to guild %s", guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced commands globally (may take up to 1 hour)")

    async def on_ready(self):
        logger.info("Bot is ready! Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Connected to %d guilds", len(self.guilds))

    async def close(self):
        logger.info("Shutting down...")
        await self.api_client.close()
        await super().close()


def main():
    config = load_config()
    token = config.get("bot", {}).get("token", "")
    if not token or token.startswith("$"):
        logger.error(
            "DISCORD_BOT_TOKEN not set. Export it: export DISCORD_BOT_TOKEN=your_token"
        )
        sys.exit(1)
    bot = GuaardvarkBot(config)
    loop = asyncio.new_event_loop()

    def handle_signal():
        logger.info("Received shutdown signal")
        loop.create_task(bot.close())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    try:
        loop.run_until_complete(bot.start(token))
    except KeyboardInterrupt:
        loop.run_until_complete(bot.close())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
