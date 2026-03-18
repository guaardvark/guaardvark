"""Guaardvark Discord Bot — entry point."""
import asyncio
import logging
import os
import re
import signal
import sys
import time

import discord
from discord.ext import commands
from aiohttp import web
import yaml
import json
from pathlib import Path

from core.api_client import GuaardvarkClient

# Setup logging — configure only our logger, prevent propagation to root to avoid duplicates
log_dir = os.path.join(os.environ.get("GUAARDVARK_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs")
os.makedirs(log_dir, exist_ok=True)

logger = logging.getLogger("discord_bot")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # Only use FileHandler — start.sh already redirects stdout to the same log file
    _fh = logging.FileHandler(os.path.join(log_dir, "discord_bot.log"))
    _fh.setFormatter(_fmt)
    logger.addHandler(_fh)


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
    "commands.chat",
    "commands.claude_chat",
    "commands.search",
    "commands.image",
    "commands.generation",
    "commands.system",
    "commands.video",
    "commands.cli_proxy",
    "commands.channel_chat",
    "commands.demo",
]


class GuaardvarkBot(commands.Bot):
    def __init__(self, config: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix=config.get("bot", {}).get("prefix", "!"), intents=intents)
        self.config = config
        self.api_client = GuaardvarkClient(base_url=config["api"]["base_url"])

    async def setup_hook(self):
        await self.api_client.setup()
        try:
            await self.api_client.health_check()
            logger.info("Guaardvark backend is reachable at %s", self.config["api"]["base_url"])
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
                await self.load_extension("commands.voice")
                logger.info("Loaded cog: commands.voice")
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

        # Start health server for plugin manager
        await self._start_health_server()

        # VIP greeting — fires on every interaction without replacing default handler
        @self.listen("on_interaction")
        async def _vip_listener(interaction: discord.Interaction):
            asyncio.create_task(self._check_vip_greeting(interaction))

    async def on_ready(self):
        logger.info("Bot is ready! Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Connected to %d guilds", len(self.guilds))

    async def _check_vip_greeting(self, interaction: discord.Interaction):
        """Send one-time DM greeting to VIP users."""
        vip_config = self.config.get("vip", {})
        vip_ids = vip_config.get("user_ids", [])
        if not vip_ids or interaction.user.id not in vip_ids:
            return

        greeted_file = Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "data" / "context" / "vip_greeted.json"
        greeted = set()
        if greeted_file.exists():
            try:
                greeted = set(json.loads(greeted_file.read_text()).get("greeted", []))
            except Exception:
                pass

        if interaction.user.id in greeted:
            return

        greeting = vip_config.get("greeting", "Welcome to Guaardvark.")
        try:
            await interaction.user.send(greeting)
            logger.info("Sent VIP greeting to user %s", interaction.user.id)
        except discord.Forbidden:
            logger.warning("Cannot DM VIP user %s (DMs disabled)", interaction.user.id)
        except Exception as e:
            logger.warning("Failed to send VIP greeting: %s", e)

        greeted.add(interaction.user.id)
        try:
            greeted_file.parent.mkdir(parents=True, exist_ok=True)
            greeted_file.write_text(json.dumps({"greeted": list(greeted)}))
        except Exception as e:
            logger.warning("Failed to save VIP greeted state: %s", e)

    async def _start_health_server(self):
        """Start a lightweight HTTP health server on port 8200."""
        self._start_time = time.time()
        app = web.Application()
        app.router.add_get("/health", self._health_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("DISCORD_HEALTH_PORT", "8200"))
        try:
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            logger.info("Health endpoint listening on port %d", port)
        except OSError as e:
            logger.warning("Could not start health server on port %d: %s", port, e)

    async def _health_handler(self, request):
        """Return bot health status."""
        connected = self.is_ready() and not self.is_closed()
        status = "healthy" if connected else "degraded"
        return web.json_response({
            "status": status,
            "service": "discord-bot",
            "version": "1.0.0",
            "discord_connected": connected,
            "guild_count": len(self.guilds) if connected else 0,
            "uptime_seconds": int(time.time() - self._start_time),
            "latency_ms": round(self.latency * 1000, 1) if connected else None,
        })

    async def close(self):
        logger.info("Shutting down...")
        await self.api_client.close()
        await super().close()


def main():
    config = load_config()
    token = config.get("bot", {}).get("token", "")
    if not token or token.startswith("$"):
        logger.error("DISCORD_BOT_TOKEN not set. Export it: export DISCORD_BOT_TOKEN=your_token")
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
