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


def main():
    root = tk.Tk()
    root.title("Shortcuts")
    root.geometry("160x240+10+10")
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

    tk.Label(root, text="Shortcuts", bg="#1a1a2e", fg="#888888", font=("Sans", 7)).pack(pady=(4, 2))

    tk.Button(root, text="Firefox", command=launch_firefox, **firefox_style).pack(pady=4, padx=8)
    tk.Button(root, text="Agent Files", command=launch_agent_files, **style).pack(pady=2, padx=8)
    tk.Button(root, text="Drawing", command=launch_drawing, **style).pack(pady=2, padx=8)
    tk.Button(root, text="Terminal", command=launch_terminal, **style).pack(pady=2, padx=8)

    root.mainloop()


if __name__ == "__main__":
    main()
