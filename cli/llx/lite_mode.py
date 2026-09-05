"""Lite vs full stack mode detection and command gating."""

from __future__ import annotations

# Sub-apps that need Postgres/Redis/Celery or other full-stack services.
FULL_STACK_SUBAPPS = frozenset(
    {
        "files",
        "projects",
        "rules",
        "agents",
        "jobs",
        "settings",
        "index",
        "backup",
        "family",
        "logs",
        "rag",
        "clients",
        "websites",
        "tasks",
        "images",
        "videos",
        "generate",
        "plugins",
        "gpu",
        "audio",
        "swarm",
        "lessons",
    }
)


def is_lite_mode() -> bool:
    try:
        from llx.launch_config import _config_path, load_launch_config

        if _config_path().exists() and load_launch_config().get("mode") == "lite":
            return True
    except Exception:
        pass
    return False


def lite_mode_block_message(command_name: str) -> str | None:
    """Return a user-facing block message when lite mode cannot run command_name."""
    if not is_lite_mode():
        return None
    if command_name not in FULL_STACK_SUBAPPS:
        return None
    return (
        f"/{command_name} requires the full Guaardvark stack "
        "(Postgres, Redis, Celery). Run: [bold]guaardvark launch --full[/bold] "
        "or [bold]guaardvark start[/bold]"
    )
