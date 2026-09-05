"""Desktop / terminal notifications for long-running CLI jobs."""

from __future__ import annotations

import shutil
import subprocess
import sys


def notify(title: str, body: str = "", *, bell: bool = True) -> str:
    """Fire a notification. Returns the channel used."""
    if shutil.which("notify-send"):
        try:
            subprocess.Popen(
                ["notify-send", title, body or title],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return "notify-send"
        except OSError:
            pass
    # OSC 9 — iTerm / some Kitty builds
    try:
        msg = body or title
        with open("/dev/tty", "w", encoding="utf-8") as tty:
            tty.write(f"\x1b]9;{msg}\x07")
            tty.flush()
        return "osc9"
    except OSError:
        pass
    if bell:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
            return "bell"
        except Exception:
            pass
    return "none"
