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
    env = {**os.environ, "DISPLAY": ":99", "MOZ_ENABLE_WAYLAND": "0", "GDK_BACKEND": "x11"}
    subprocess.Popen(
        ["firefox", "--no-remote", "--profile", PROFILE_DIR],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def launch_agent_files():
    env = {**os.environ, "DISPLAY": ":99"}
    subprocess.Popen(
        ["pcmanfm", AGENT_FILES_DIR],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def launch_terminal():
    env = {**os.environ, "DISPLAY": ":99"}
    subprocess.Popen(
        ["xterm", "-fa", "Monospace", "-fs", "12"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main():
    root = tk.Tk()
    root.title("Shortcuts")
    root.geometry("120x160+10+10")
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
        "width": 14,
        "height": 1,
        "cursor": "hand2",
    }

    tk.Label(root, text="Shortcuts", bg="#1a1a2e", fg="#888888", font=("Sans", 7)).pack(pady=(4, 2))

    tk.Button(root, text="Firefox", command=launch_firefox, **style).pack(pady=2, padx=4)
    tk.Button(root, text="Agent Files", command=launch_agent_files, **style).pack(pady=2, padx=4)
    tk.Button(root, text="Terminal", command=launch_terminal, **style).pack(pady=2, padx=4)

    root.mainloop()


if __name__ == "__main__":
    main()
