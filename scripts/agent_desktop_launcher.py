#!/usr/bin/env python3
"""Agent Desktop Launcher — visible shortcut icons on the virtual display.

Renders a small panel with clickable icons in the top-left corner of DISPLAY=:99.
Runs as a background process, started by start_agent_display.sh.
"""

import os
import subprocess
import tkinter as tk
from pathlib import Path

GUAARDVARK_ROOT = os.environ.get("GUAARDVARK_ROOT", str(Path(__file__).parent.parent))
AGENT_FILES_DIR = os.path.join(GUAARDVARK_ROOT, "data", "agent", "files")
PROFILE_DIR = os.path.join(GUAARDVARK_ROOT, "data", "agent", "firefox_profile")


def launch_firefox():
    subprocess.Popen(
        ["firefox", "--no-remote", "--remote-debugging-port", "9222", "--profile", PROFILE_DIR],
        env=_agent_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _agent_env():
    """Environment that forces X11 on the agent display, not Wayland."""
    env = {**os.environ, "DISPLAY": ":99", "GDK_BACKEND": "x11",
           "MOZ_ENABLE_WAYLAND": "0", "WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"}
    return env


def launch_agent_files():
    subprocess.Popen(
        ["pcmanfm", AGENT_FILES_DIR],
        env=_agent_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def launch_terminal():
    subprocess.Popen(
        ["xterm", "-fa", "Monospace", "-fs", "12"],
        env=_agent_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def launch_drawing():
    subprocess.Popen(
        ["drawing"],
        env=_agent_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _launch_browser_to(url: str):
    """Launch Firefox (or focus it) and navigate to a URL.

    If Firefox is already running on :99, sending --new-tab tells the existing
    process to open a tab. If not, it cold-starts. Either way the agent ends
    up on the page without needing to drive the URL bar.
    """
    subprocess.Popen(
        ["firefox", "--no-remote", "--remote-debugging-port", "9222",
         "--profile", PROFILE_DIR, "--new-tab", url],
        env=_agent_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def launch_youtube():       _launch_browser_to("https://www.youtube.com/")
def launch_reddit():        _launch_browser_to("https://www.reddit.com/r/LocalLLaMA/")
def launch_guaardvark():    _launch_browser_to("http://localhost:5175/")
def launch_outreach():      _launch_browser_to("http://localhost:5175/outreach")


def main():
    root = tk.Tk()
    root.title("Shortcuts")
    # Taller — accommodates the new "Sites" section below the launchers.
    # Keep the top-left anchor + width identical so existing recipes that
    # click (92, 103) for Firefox keep working.
    root.geometry("160x420+10+10")
    root.resizable(False, False)
    root.configure(bg="#1a1a2e")
    root.attributes("-topmost", False)

    style = {
        "bg": "#2d2d44",
        "fg": "#e0e0e0",
        "activebackground": "#4a4a6a",
        "activeforeground": "#ffffff",
        "font": ("Sans", 9),
        "relief": "flat",
        "bd": 0,
        "width": 18,
        "height": 1,
        "cursor": "hand2",
    }

    # Firefox gets the spotlight — big orange button so the agent can't miss it
    firefox_style = {
        **style,
        "bg": "#e65100",
        "fg": "#ffffff",
        "activebackground": "#ff6d00",
        "activeforeground": "#ffffff",
        "font": ("Sans", 12, "bold"),
        "height": 2,
        "width": 18,
    }

    # Per-site button colors — distinctive enough that vision models can
    # pick them out by description ("the red YouTube button", etc.) without
    # needing exact pixel coords.
    site_styles = {
        "youtube":    {"bg": "#cc0000", "activebackground": "#e53935"},
        "reddit":     {"bg": "#ff4500", "activebackground": "#ff6f3c"},
        "guaardvark": {"bg": "#008080", "activebackground": "#26a6a6"},
        "outreach":   {"bg": "#2e7d32", "activebackground": "#43a047"},
    }
    def site_btn(color_key):
        return {**style, **site_styles[color_key], "fg": "#ffffff"}

    tk.Label(root, text="Shortcuts", bg="#1a1a2e", fg="#888888", font=("Sans", 7)).pack(pady=(4, 2))

    tk.Button(root, text="Firefox", command=launch_firefox, **firefox_style).pack(pady=4, padx=8)
    tk.Button(root, text="Agent Files", command=launch_agent_files, **style).pack(pady=2, padx=8)
    tk.Button(root, text="Drawing", command=launch_drawing, **style).pack(pady=2, padx=8)
    tk.Button(root, text="Terminal", command=launch_terminal, **style).pack(pady=2, padx=8)

    # Visual divider so the agent's vision model groups Sites separately
    # from Apps. Plain text label is enough — colored bars can confuse vision.
    tk.Label(root, text="Sites", bg="#1a1a2e", fg="#888888", font=("Sans", 7)).pack(pady=(10, 2))

    tk.Button(root, text="YouTube",    command=launch_youtube,    **site_btn("youtube")).pack(pady=2, padx=8)
    tk.Button(root, text="Reddit",     command=launch_reddit,     **site_btn("reddit")).pack(pady=2, padx=8)
    tk.Button(root, text="Guaardvark", command=launch_guaardvark, **site_btn("guaardvark")).pack(pady=2, padx=8)
    tk.Button(root, text="Outreach",   command=launch_outreach,   **site_btn("outreach")).pack(pady=2, padx=8)

    root.mainloop()


if __name__ == "__main__":
    main()
