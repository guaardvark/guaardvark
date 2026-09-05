"""Detect terminal capabilities: color depth, graphics protocol, hyperlinks, tmux."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class TermCaps:
    name: str
    truecolor: bool
    hyperlinks: bool
    graphics: str  # kitty | iterm | none
    tmux: bool
    color_count: int


def _env(name: str) -> str:
    return os.environ.get(name, "") or ""


def detect_terminal_name() -> str:
    term_program = _env("TERM_PROGRAM").lower()
    term = _env("TERM").lower()
    if _env("KITTY_WINDOW_ID") or "kitty" in term:
        return "kitty"
    if "ghostty" in term_program or "ghostty" in term:
        return "ghostty"
    if "wezterm" in term_program or "wezterm" in term:
        return "wezterm"
    if term_program == "iterm.app":
        return "iterm"
    if "warp" in term_program:
        return "warp"
    if "alacritty" in term:
        return "alacritty"
    if "vscode" in term_program or _env("TERM_PROGRAM") == "vscode":
        return "vscode"
    if "xterm" in term:
        return "xterm"
    return term_program or term or "unknown"


def detect_graphics(name: str | None = None) -> str:
    """Which inline-image protocol to use. 'none' if we should fall back."""
    name = name or detect_terminal_name()
    if _env("TMUX"):
        # Kitty protocol needs allow-passthrough; still try kitty if outer is kitty.
        pass
    if name in {"kitty", "ghostty", "wezterm"}:
        return "kitty"
    if name == "iterm":
        return "iterm"
    if shutil.which("chafa"):
        return "chafa"
    return "none"


def detect_truecolor() -> bool:
    if _env("NO_COLOR"):
        return False
    colorterm = _env("COLORTERM").lower()
    if "truecolor" in colorterm or colorterm == "24bit":
        return True
    term = _env("TERM").lower()
    return "truecolor" in term or "direct" in term or "ghostty" in term or "kitty" in term


def detect() -> TermCaps:
    name = detect_terminal_name()
    tmux = bool(_env("TMUX"))
    truecolor = detect_truecolor()
    graphics = detect_graphics(name)
    hyperlinks = name in {
        "kitty",
        "ghostty",
        "wezterm",
        "iterm",
        "vscode",
        "warp",
    } and not _env("NO_COLOR")
    color_count = 16777216 if truecolor else (256 if "256" in _env("TERM") else 16)
    if _env("NO_COLOR"):
        color_count = 0
    return TermCaps(
        name=name,
        truecolor=truecolor,
        hyperlinks=hyperlinks,
        graphics=graphics,
        tmux=tmux,
        color_count=color_count,
    )


def tmux_hints() -> list[str]:
    """Copy-pasteable tmux settings for graphics / truecolor / clipboard."""
    return [
        "set -g set-clipboard on",
        "set -wg allow-passthrough on",
        "set -g extended-keys on",
        'set -as terminal-features ",*:RGB"',
    ]
