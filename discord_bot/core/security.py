"""Input sanitization, admin checks, and channel allowlists."""

import re
from typing import Optional


def sanitize_input(text: str, max_length: int = 2000) -> Optional[str]:
    """Sanitize user input: strip mentions, code blocks, enforce length limit. Returns None if empty."""
    if not text:
        return None
    cleaned = re.sub(r"<@[!&]?\d+>", "", text)
    cleaned = re.sub(r"@(everyone|here)", "", cleaned)
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned if cleaned else None


def is_admin(member, admin_roles: list[str]) -> bool:
    """Check if a guild member has any of the configured admin roles."""
    if member is None:
        return False
    member_role_names = {role.name for role in getattr(member, "roles", [])}
    return bool(member_role_names & set(admin_roles))


def is_channel_allowed(channel_id: int, allowed_channels: list[int]) -> bool:
    """Check if a channel is in the allowlist. Empty list = all allowed."""
    if not allowed_channels:
        return True
    return channel_id in allowed_channels
