#!/usr/bin/env python3
"""
Agent Display Utilities — shared helpers for detecting and targeting the
agent's virtual display (:99).

Used by app_launch, browser_navigate, and agent control tools to determine
whether to route operations to the Xvfb virtual display or the host machine.
"""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

AGENT_DISPLAY = os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99")
GUAARDVARK_ROOT = os.environ.get("GUAARDVARK_ROOT", "")


def is_agent_display_active() -> bool:
    """Check if the Xvfb virtual display is running."""
    display_num = AGENT_DISPLAY.lstrip(":")
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"Xvfb :{display_num}"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def is_firefox_on_agent_display() -> bool:
    """Check if Firefox has a window on the agent display."""
    env = {**os.environ, "DISPLAY": AGENT_DISPLAY}
    try:
        result = subprocess.run(
            ["xdotool", "search", "--name", "Mozilla Firefox"],
            env=env, capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


def get_agent_display_env() -> dict:
    """Return environment dict for subprocess targeting the agent display."""
    return {
        **os.environ,
        "DISPLAY": AGENT_DISPLAY,
        "MOZ_ENABLE_WAYLAND": "0",
        "GDK_BACKEND": "x11",
    }


def get_firefox_profile_path() -> str:
    """Return the path to the agent's persistent Firefox profile."""
    root = GUAARDVARK_ROOT or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "agent", "firefox_profile")


def wait_for_firefox_on_display(timeout: float = 8.0, poll_interval: float = 0.5) -> bool:
    """Poll until Firefox appears on the agent display, or timeout."""
    import time
    start = time.time()
    while time.time() - start < timeout:
        if is_firefox_on_agent_display():
            return True
        time.sleep(poll_interval)
    return False
