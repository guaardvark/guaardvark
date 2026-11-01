"""Public mode: what the bot offers when strangers can reach an install that holds private data.

A stock bot answers from the install it runs on. /ask and /search read the knowledge
base, and /imagine can load trained Cast characters. That suits a spare box or a private
server. With DISCORD_PUBLIC_MODE=1 it offers /imagine alone, only in the channels named by
DISCORD_PUBLIC_CHANNELS, with no Cast characters, and refuses sexual prompts. No named
channels means no channel: this fails closed.

The prompt check reads words, not pictures. It stops the ask, not every possible output.
"""
import logging
import os
import re
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)

COGS = ("commands.image",)
COMMANDS = frozenset({"imagine"})
MAX_QUEUE_DEPTH = 2

REFUSED = re.compile(
    r"\b(nude\w*|naked|nsfw|porn\w*|sex|sexy|sexual\w*|hentai|erotic\w*|lewd|topless|bottomless|lingerie|"
    r"underwear|bikini|breasts?|boobs?|nipples?|genitals?|penis|vagina|undress\w*|fetish\w*|"
    r"loli|shota|underage|minors?|child|children|kids?|toddlers?|teens?|teenagers?|schoolgirls?|schoolboys?)\b",
    re.I,
)


def enabled(config: dict) -> bool:
    value = str(config.get("security", {}).get("public_mode") or "").strip().lower()
    return value not in ("", "0", "false", "no", "off")


def channels(config: dict) -> Set[int]:
    raw = str(config.get("security", {}).get("public_channels") or "")
    return {int(part) for part in re.split(r"[,\s]+", raw) if part.isdigit()}


def refuses(prompt: str) -> bool:
    return bool(REFUSED.search(prompt or ""))


def job_marker() -> Path:
    """Exists while /imagine renders, so a watcher can tell the bot's own work from anyone else's."""
    return Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "pids" / "discord_image_job"


def mark_job(active: bool) -> None:
    """Keep the marker in step with the running jobs. A lost marker must never break a render."""
    marker = job_marker()
    try:
        if active:
            marker.touch()
        else:
            marker.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not update %s: %s", marker, e)
