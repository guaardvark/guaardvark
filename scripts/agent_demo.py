#!/usr/bin/env python3
"""
GUAARDVARK SELF-DEMO
The agent tours its own application, demonstrating features for screen recording.

Usage:
    python3 scripts/agent_demo.py                    # Full demo with recording
    python3 scripts/agent_demo.py --section chat     # Just one section
    python3 scripts/agent_demo.py --pause 1.5        # Slower pauses
    python3 scripts/agent_demo.py --no-record        # No video recording
    python3 scripts/agent_demo.py --dry-run           # Print plan only
"""

import requests
import time
import json
import subprocess
import signal
import argparse
import sys
import os

API = os.environ.get("GUAARDVARK_API", "http://localhost:5002")
DISPLAY = os.environ.get("AGENT_DISPLAY", ":99")

C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "green": "\033[92m", "yellow": "\033[93m", "cyan": "\033[96m",
    "magenta": "\033[95m", "red": "\033[91m",
}


def c(color, text):
    return f"{C[color]}{text}{C['reset']}"


def xdo(*args):
    """Run xdotool command on the virtual display."""
    subprocess.run(["xdotool"] + list(args), env={**os.environ, "DISPLAY": DISPLAY},
                   capture_output=True, timeout=5)


def xdo_type(text, delay=30):
    """Type text on the virtual display."""
    subprocess.run(["xdotool", "type", "--delay", str(delay), "--clearmodifiers", text],
                   env={**os.environ, "DISPLAY": DISPLAY}, capture_output=True, timeout=10)


class ScreenRecorder:
    """Record the virtual display using ffmpeg."""

    def __init__(self, output_path, display=DISPLAY, resolution="1280x720", fps=30):
        self.output_path = output_path
        self.display = display
        self.resolution = resolution
        self.fps = fps
        self.process = None

    def start(self):
        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-f", "x11grab",
            "-draw_mouse", "1",
            "-framerate", str(self.fps),
            "-video_size", self.resolution,
            "-i", self.display,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            self.output_path,
        ]
        self.process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": self.display}
        )
        print(c("green", f"    Recording: {self.output_path}"))
        time.sleep(0.5)

    def stop(self):
        if self.process:
            self.process.send_signal(signal.SIGINT)
            self.process.wait(timeout=10)
            size = os.path.getsize(self.output_path) if os.path.exists(self.output_path) else 0
            print(c("green", f"    Saved: {self.output_path} ({size / 1048576:.1f}MB)"))
            self.process = None


def generate_narration(text, voice="libritts", output_dir=None):
    """Generate TTS audio for narration text. Returns (filepath, duration)."""
    try:
        r = requests.post(f"{API}/api/voice/narrate",
                          json={"script": text, "voice": voice, "output_format": "wav"},
                          timeout=30)
        data = r.json()
        if "audio_url" not in data:
            return None, 0

        audio_url = data["audio_url"]
        duration = data.get("duration_seconds", 3)
        filename = data.get("filename", "")

        # Download the audio file — add /api prefix if not present
        if not audio_url.startswith("/api"):
            audio_url = f"/api{audio_url}"
        audio_r = requests.get(f"{API}{audio_url}", timeout=10)
        if audio_r.status_code == 200 and output_dir:
            filepath = os.path.join(output_dir, filename)
            with open(filepath, "wb") as f:
                f.write(audio_r.content)
            return filepath, duration
        return None, duration
    except Exception as e:
        print(c("red", f"    Narration failed: {e}"))
        return None, 0


def kill_agent():
    try:
        requests.post(f"{API}/api/agent-control/kill", timeout=5)
    except Exception:
        pass
    time.sleep(1)


def navigate(url):
    """Navigate via recipe — uses current tab, no new tabs."""
    kill_agent()
    r = requests.post(f"{API}/api/agent-control/execute",
                      json={"task": f"Navigate to {url}"}, timeout=15)
    if not r.json().get("success"):
        return False
    for _ in range(15):
        time.sleep(2)
        s = requests.get(f"{API}/api/agent-control/status", timeout=10).json()
        st = s.get("data", s.get("status", {}))
        if not st.get("active", True):
            return True
    return False


def agent_task(task, timeout_s=60):
    """Execute an agent task and wait for completion."""
    kill_agent()
    r = requests.post(f"{API}/api/agent-control/execute",
                      json={"task": task}, timeout=15)
    if not r.json().get("success"):
        return None
    for _ in range(timeout_s // 3):
        time.sleep(3)
        s = requests.get(f"{API}/api/agent-control/status", timeout=10).json()
        st = s.get("data", s.get("status", {}))
        if not st.get("active", True):
            return st.get("last_result", {})
    return {"success": False, "reason": "timeout"}


def move_mouse_smoothly(x, y, steps=20):
    """Move mouse cursor smoothly to a position for visual effect."""
    # Get current position
    try:
        result = subprocess.run(
            ["xdotool", "getmouselocation"],
            env={**os.environ, "DISPLAY": DISPLAY},
            capture_output=True, text=True, timeout=3
        )
        parts = result.stdout.strip().split()
        cx = int(parts[0].split(":")[1])
        cy = int(parts[1].split(":")[1])
    except Exception:
        cx, cy = 640, 360

    for i in range(1, steps + 1):
        nx = cx + int((x - cx) * i / steps)
        ny = cy + int((y - cy) * i / steps)
        xdo("mousemove", str(nx), str(ny))
        time.sleep(0.02)
    time.sleep(0.1)


def close_all_tabs_except_one():
    """Close all tabs except the current one."""
    for _ in range(20):
        # Ctrl+W closes current tab; when only one remains, it stays
        xdo("key", "ctrl+w")
        time.sleep(0.3)
    # Open a blank tab if we closed everything
    time.sleep(1)


# ─── DEMO SECTIONS ────────────────────────────────────────────────

DEMO_SECTIONS = {
    "desktop": {
        "title": "Desktop — Starting Fresh",
        "pause": 3,
        "narration": "This is Guaardvark running on a local workstation. Everything happens on your hardware — your AI, your data, your rules.",
        "action": "show_desktop",
    },
    "open_browser": {
        "title": "Opening the Browser",
        "pause": 2,
        "narration": "Let's open the browser and take a tour.",
        "action": "open_firefox",
    },
    "dashboard": {
        "title": "Dashboard — Command Center",
        "route": "localhost:5175/",
        "pause": 6,
        "narration": "The dashboard gives you a bird's eye view — projects, clients, system health, recent activity, all at a glance.",
    },
    "chat": {
        "title": "Chat — Your Local AI",
        "route": "localhost:5175/chat",
        "pause": 3,
        "narration": "Chat with your local AI. No cloud, no data leaving your machine.",
        "interaction": "Click the text input at the bottom of the chat page, type What can Guaardvark do? and press Return.",
    },
    "chat_response": {
        "title": "Chat — Live Response",
        "pause": 10,
        "narration": "Watch the AI think and respond in real time. Tool calls, reasoning, everything transparent.",
    },
    "chat_narrate": {
        "title": "Chat — Voice Narration",
        "pause": 6,
        "narration": "Every response can be narrated with text-to-speech. Built-in Piper TTS, runs locally.",
        "interaction": "Click the small speaker icon at the bottom right of the assistant's response message to narrate it.",
    },
    "images": {
        "title": "Media — AI Image Generation",
        "route": "localhost:5175/images",
        "pause": 6,
        "narration": "Generate images with Stable Diffusion. No API keys, no limits, no restrictions. Your GPU, your creativity.",
    },
    "video": {
        "title": "Video Generation",
        "route": "localhost:5175/video",
        "pause": 6,
        "narration": "AI video generation with CogVideoX through ComfyUI. Full production pipeline on your own hardware.",
    },
    "documents": {
        "title": "Documents — Knowledge Base",
        "route": "localhost:5175/documents",
        "pause": 5,
        "narration": "Upload documents, build a knowledge base. RAG-powered search so your AI understands your files.",
    },
    "code_editor": {
        "title": "Code Editor — Built-in IDE",
        "route": "localhost:5175/code-editor",
        "pause": 5,
        "narration": "Full Monaco code editor. Edit files, get AI explanations, fix bugs, generate features — all integrated.",
    },
    "notes": {
        "title": "Notes — Quick Capture",
        "route": "localhost:5175/notes",
        "pause": 5,
        "narration": "Drag-and-drop sticky notes for quick ideas. Simple but useful.",
    },
    "agents": {
        "title": "Agents — Autonomous AI Workers",
        "route": "localhost:5175/agents",
        "pause": 5,
        "narration": "Nine specialized agents — each with their own tools, personality, and expertise. From code assistants to content creators.",
    },
    "tools": {
        "title": "Agent Tools — 50+ Capabilities",
        "route": "localhost:5175/tools",
        "pause": 5,
        "narration": "Fifty tools the AI uses autonomously. Web search, browser control, code execution, image generation, and more.",
    },
    "rules": {
        "title": "Rules & Prompts",
        "route": "localhost:5175/rules",
        "pause": 5,
        "narration": "System prompts, behavioral rules, guardrails. Shape how your AI thinks and responds.",
    },
    "settings": {
        "title": "Settings — Full Control",
        "route": "localhost:5175/settings",
        "pause": 5,
        "narration": "Every aspect configurable. Models, RAG, voice, system — all in one place.",
        "interaction": "Scroll down slowly to show more settings cards.",
    },
    "plugins": {
        "title": "Plugins — Extensible",
        "route": "localhost:5175/plugins",
        "pause": 5,
        "narration": "Extend with plugins. Ollama for LLMs, ComfyUI for video, Discord bot — plug in what you need.",
    },
    "projects": {
        "title": "Projects — Organized Workflow",
        "route": "localhost:5175/projects",
        "pause": 5,
        "narration": "Organize into projects. Each gets its own chat context, documents, and knowledge base.",
    },
}

DEMO_ORDER = [
    "desktop", "open_browser",
    "dashboard",
    "chat", "chat_response", "chat_narrate",
    "images", "video",
    "documents", "code_editor", "notes",
    "agents", "tools", "rules",
    "settings", "plugins", "projects",
]


def run_demo(sections=None, pause_multiplier=1.0, dry_run=False, record=True):
    order = sections if sections else DEMO_ORDER

    print()
    print(c("cyan", "=" * 70))
    print(c("cyan", "  GUAARDVARK SELF-DEMO"))
    print(c("cyan", "  The AI workstation that shows itself off"))
    print(c("cyan", "=" * 70))
    print(c("dim", f"  Sections: {len(order)} | Pause: {pause_multiplier}x | Record: {record}"))
    print()

    if dry_run:
        print(c("yellow", "  DRY RUN — plan only"))
        print()
        for name in order:
            s = DEMO_SECTIONS.get(name, {})
            print(f"  {c('bold', s.get('title', name))}")
            print(f"    {s.get('narration', '')[:90]}...")
            if s.get("interaction"):
                print(f"    Interaction: {s['interaction'][:70]}...")
            print()
        return

    # Set up output directory
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "outputs", "demos")
    os.makedirs(output_dir, exist_ok=True)
    audio_dir = os.path.join(output_dir, f"audio_{timestamp}")
    os.makedirs(audio_dir, exist_ok=True)

    # Pre-generate all narration audio
    print(c("cyan", "  Generating narration audio..."))
    audio_files = {}
    for name in order:
        section = DEMO_SECTIONS.get(name, {})
        narration = section.get("narration", "")
        if narration:
            filepath, duration = generate_narration(narration, output_dir=audio_dir)
            if filepath:
                audio_files[name] = {"path": filepath, "duration": duration}
                print(c("dim", f"    {name}: {duration:.1f}s"))
            else:
                print(c("dim", f"    {name}: text only (TTS skipped)"))
    print(c("green", f"  Generated {len(audio_files)} audio clips"))
    print()

    # Close Firefox for desktop shot
    print(c("dim", "  Preparing: closing Firefox for desktop shot..."))
    # Only kill the agent's Firefox, not the user's
    agent_pids = subprocess.run(
        ["pgrep", "-f", "firefox.*agent_firefox_profile"],
        capture_output=True, text=True
    ).stdout.strip().split()
    for pid in agent_pids:
        if pid:
            subprocess.run(["kill", pid], capture_output=True)
    time.sleep(3)

    # Start recording
    recorder = None
    video_path = None
    if record:
        video_path = os.path.join(output_dir, f"guaardvark_demo_{timestamp}.mp4")
        recorder = ScreenRecorder(video_path)
        print(c("yellow", "  Recording starts in 3..."))
        time.sleep(3)
        recorder.start()
    else:
        print(c("yellow", "  Starting in 3..."))
        time.sleep(3)

    firefox_launched = False

    for i, name in enumerate(order):
        section = DEMO_SECTIONS.get(name)
        if not section:
            continue

        progress = f"[{i+1}/{len(order)}]"
        print(f"\n  {c('magenta', progress)} {c('bold', section['title'])}")

        # Special actions
        action = section.get("action")
        if action == "show_desktop":
            # Just show the desktop with mouse movement
            move_mouse_smoothly(640, 360)
            time.sleep(1)
            move_mouse_smoothly(200, 500)
            time.sleep(0.5)
            move_mouse_smoothly(640, 360)

        elif action == "open_firefox":
            # Launch Firefox from the taskbar/desktop
            profile = "/tmp/agent_firefox_profile2"
            for lock in [".parentlock", "lock"]:
                try:
                    os.remove(os.path.join(profile, lock))
                except FileNotFoundError:
                    pass
            subprocess.Popen(
                ["firefox", "--profile", profile, "--width", "1280", "--height", "690",
                 "http://localhost:5175/"],
                env={**os.environ, "DISPLAY": DISPLAY, "MOZ_ENABLE_WAYLAND": "0", "GDK_BACKEND": "x11"},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            firefox_launched = True
            print(f"    {c('dim', 'Launching Firefox...')}")
            time.sleep(8)  # Wait for Firefox to load

        # Navigate (if route specified and Firefox is open)
        if section.get("route") and firefox_launched:
            print(f"    {c('dim', 'Navigating to ' + section['route'])}")
            navigate(section["route"])
            time.sleep(2)

        # Interaction (before narration so the screen shows the result)
        if section.get("interaction"):
            print(f"    {c('yellow', section['interaction'][:70] + '...')}")
            result = agent_task(section["interaction"], timeout_s=45)
            if result:
                ok = result.get("success", False)
                print(f"    {c('green' if ok else 'red', 'OK' if ok else 'FAIL')}")

        # Pause — use narration duration if available, otherwise section default
        audio_info = audio_files.get(name)
        if audio_info:
            pause = max(audio_info["duration"] + 1, section["pause"] * pause_multiplier)
            dur = audio_info["duration"]
            print(f"    {c('dim', f'Narrating ({dur:.1f}s audio, {pause:.0f}s pause)')}")
        else:
            pause = max(1, int(section["pause"] * pause_multiplier))
            print(f"    {c('dim', f'({pause}s)')}")
        time.sleep(pause)

    # Stop recording
    time.sleep(3)
    if recorder:
        recorder.stop()

    # Post-process: merge narration audio with video
    final_path = None
    if video_path and audio_files:
        print()
        print(c("cyan", "  Post-processing: merging narration audio..."))

        # Concatenate all audio clips with gaps matching the video timing
        concat_list = os.path.join(audio_dir, "concat.txt")
        audio_paths = []
        for name in order:
            if name in audio_files:
                audio_paths.append(audio_files[name]["path"])

        if audio_paths:
            # Concatenate all audio clips
            with open(concat_list, "w") as f:
                for ap in audio_paths:
                    f.write(f"file '{ap}'\n")

            combined_audio = os.path.join(audio_dir, "narration_combined.wav")
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_list, "-c", "copy", combined_audio
            ], capture_output=True, timeout=30)

            # Merge video + audio
            final_path = video_path.replace(".mp4", "_narrated.mp4")
            merge_result = subprocess.run([
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", combined_audio,
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest",
                final_path,
            ], capture_output=True, timeout=60)

            if merge_result.returncode == 0 and os.path.exists(final_path):
                size_mb = os.path.getsize(final_path) / 1048576
                print(c("green", f"  Narrated video: {final_path} ({size_mb:.1f}MB)"))
            else:
                print(c("red", f"  Audio merge failed — raw video available at {video_path}"))
                final_path = video_path
        else:
            final_path = video_path

    print()
    print(c("cyan", "=" * 70))
    print(c("cyan", "  DEMO COMPLETE"))
    if final_path:
        print(c("cyan", f"  Video: {final_path}"))
    elif video_path:
        print(c("cyan", f"  Video: {video_path}"))
    print(c("cyan", "  Audio clips: " + audio_dir))
    print(c("cyan", "=" * 70))
    print()


def main():
    parser = argparse.ArgumentParser(description="Guaardvark Self-Demo")
    parser.add_argument("--section", "-s", nargs="+", help="Run specific section(s)")
    parser.add_argument("--pause", "-p", type=float, default=1.0, help="Pause multiplier")
    parser.add_argument("--dry-run", "-d", action="store_true", help="Print plan only")
    parser.add_argument("--no-record", action="store_true", help="Skip video recording")
    parser.add_argument("--list", "-l", action="store_true", help="List sections")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable sections:")
        for name in DEMO_ORDER:
            s = DEMO_SECTIONS.get(name, {})
            print(f"  {name:20s} {s.get('title', '')}")
        return

    run_demo(
        sections=args.section,
        pause_multiplier=args.pause,
        dry_run=args.dry_run,
        record=not args.no_record,
    )


if __name__ == "__main__":
    main()
