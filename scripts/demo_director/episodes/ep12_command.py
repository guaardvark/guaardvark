"""Episode 12 — Command Center (≈3:30).

The VRAM budget bar with live per-plugin segments, the on-camera GPU
conflict warning (starting ComfyUI beside Ollama), Jobs vs Activity, the
Emergency Kill Switch modal plus the real killswitch.sh in a terminal,
the guaardvark CLI REPL, the backup manager, and the series closer.

GPU cast: Ollama + Audio Foundry resident; ComfyUI is STARTED on camera
(the conflict beat) and left running. Kokoro narration via audio foundry.

Run from scripts/demo_director/:  venv/bin/python episodes/ep12_command.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests as rq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import API, Beat, Episode, Stage, FRONTEND  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


def goto(st: Stage, path: str, settle: float = 2.5):
    st.page.goto(FRONTEND + path, wait_until="load", timeout=60_000)
    st.page.wait_for_timeout(int(settle * 1000))


def close_dialogs(st: Stage):
    for _ in range(2):
        if st.page.locator("div[role='dialog']").count():
            st.cursor._xdo("key", "Escape")
            time.sleep(0.8)


def stage_terminal(cmd: str):
    """Spawn a terminal on the stage display running cmd (GTK needs the
    Wayland handle scrubbed or it opens on the host desktop)."""
    env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
    env["DISPLAY"] = ":98"
    env["GDK_BACKEND"] = "x11"
    subprocess.Popen(
        ["ptyxis", "--standalone", "--", "bash", "-c", cmd],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def kill_stage_terminal():
    subprocess.run(["pkill", "-f", "ptyxis --standalon[e]"], check=False)


# ---------------------------------------------------------------- beats

def reset_plugins(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    goto(st, "/plugins", settle=3.0)
    st.page.get_by_text(re.compile("GPU VRAM")).first.wait_for(
        state="visible", timeout=30_000)
    time.sleep(0.8)


def act_vram(st: Stage):
    st.hover_over(st.page.get_by_text(re.compile("GPU VRAM")).first, dur=0.9)
    time.sleep(1.5)
    free = st.page.get_by_text(re.compile(r"[\d.]+ GB free"))
    if free.count():
        st.hover_over(free.first, dur=0.8)
        time.sleep(1.5)
    legend = st.page.get_by_text(re.compile(r"~[\d.]+GB est\."))
    if legend.count():
        st.hover_over(legend.first, dur=0.8)
        time.sleep(1.5)


def v_plugins(st: Stage):
    assert "/plugins" in st.path(), st.path()


def reset_conflict(st: Stage):
    # ComfyUI must be OFF so the on-camera toggle triggers the warning
    try:
        rq.post(f"{API}/api/plugins/comfyui/stop", timeout=120)
    except Exception:
        pass
    reset_plugins(st)


def act_conflict(st: Stage):
    card = st.page.locator(".MuiCard-root").filter(has_text="ComfyUI").first
    x, y = st.screen_xy(card)
    st.cursor.glide(x, y, dur=0.8)
    time.sleep(1.0)
    st.glide_click(card.get_by_role("checkbox").first, dur=0.8)
    try:
        st.page.get_by_text(re.compile(
            "is also using the GPU")).first.wait_for(
            state="visible", timeout=20_000)
    except Exception:
        pass
    time.sleep(2.0)
    try:
        card.get_by_text(re.compile("Starting|Running")).first.wait_for(
            state="visible", timeout=60_000)
    except Exception:
        pass
    time.sleep(2.0)


def reset_jobs(st: Stage):
    close_dialogs(st)
    goto(st, "/tasks", settle=2.5)


def act_jobs(st: Stage):
    st.hover_over(st.page.get_by_text("Job Scheduler").first, dur=0.8)
    time.sleep(1.2)
    newjob = st.page.get_by_role("button", name="New Job")
    if newjob.count():
        st.glide_click(newjob.first, dur=0.8)
        time.sleep(2.0)
        st.cursor._xdo("key", "Escape")
        time.sleep(0.8)
    st.nav_via_sidebar("Activity", "/activity",
                       st.page.get_by_placeholder(
                           "Search by label, kind, status..."))
    time.sleep(1.5)
    tab = st.page.get_by_role("tab", name=re.compile(r"History"))
    if tab.count():
        st.glide_click(tab.first, dur=0.8)
        time.sleep(2.0)


def v_activity(st: Stage):
    assert "/activity" in st.path(), st.path()


def reset_settings(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    goto(st, "/settings", settle=2.5)


def act_killswitch(st: Stage):
    ks = st.page.get_by_role("button", name="Kill Switch")
    st.glide_click(ks.first, dur=0.9)
    st.page.get_by_text("Emergency Kill Switch").first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.2)
    status = st.page.get_by_role("button", name="Get System Status")
    st.glide_click(status.first, dur=0.8)
    try:
        st.page.get_by_text(re.compile(r"CPU Usage")).first.wait_for(
            state="visible", timeout=20_000)
    except Exception:
        pass
    time.sleep(2.0)
    st.hover_over(st.page.get_by_role(
        "button", name="KILL ALL PROCESSES").first, dur=0.9)
    time.sleep(1.5)
    st.glide_click(st.page.locator("div[role='dialog']").get_by_role(
        "button", name="Close").first, dur=0.7)
    time.sleep(0.8)
    # the layer below the app: the real killswitch script, on screen
    stage_terminal(
        f"cd {REPO} && echo '$ head -24 killswitch.sh' && "
        "head -24 killswitch.sh; sleep 60")
    time.sleep(4.0)


def v_settings(st: Stage):
    assert "/settings" in st.path(), st.path()


def reset_cli(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    time.sleep(0.5)


def act_cli(st: Stage):
    stage_terminal(
        f"cd {REPO} && backend/venv/bin/guaardvark; sleep 30")
    time.sleep(7.0)
    st.cursor.glide(960, 540, dur=0.8)
    st.cursor.click()
    time.sleep(1.0)
    st.cursor.type_text("help", delay_ms=90)
    st.cursor._xdo("key", "Return")
    time.sleep(5.0)
    st.cursor.type_text("exit", delay_ms=90)
    st.cursor._xdo("key", "Return")
    time.sleep(1.5)


def v_any(st: Stage):
    pass


def act_backup(st: Stage):
    # several "Manage" controls exist on Settings — take the one beside
    # the backup row's Restore button
    restore = st.page.get_by_role("button", name="Restore", exact=True).first
    manage = restore.locator("xpath=following-sibling::button[1]")
    st.glide_click(manage.first, dur=0.9)
    st.page.get_by_text("Manage Backups").first.wait_for(
        state="visible", timeout=20_000)
    time.sleep(2.5)
    st.glide_click(st.page.locator("div[role='dialog']").get_by_role(
        "button", name="Close").first, dur=0.7)
    time.sleep(0.8)


def act_closer(st: Stage):
    goto(st, "/plugins", settle=2.5)
    st.hover_over(st.page.get_by_text(re.compile("GPU VRAM")).first, dur=1.0)
    time.sleep(2.0)


BEATS = [
    Beat(
        name="hook_vram",
        narration=[
            "Eleven episodes of AI doing whatever it wants would be "
            "terrifying...",
            "",
            "if you couldn't see everything. Gate everything. And kill "
            "everything.",
            "Welcome to the command center. This bar is the GPU, live: "
            "every service, its budget, and what's actually free.",
        ],
        action=act_vram,
        verify=v_plugins,
        reset=reset_plugins,
    ),
    Beat(
        name="conflict",
        narration=[
            "One card. Everyone wants it. The system referees.",
            "",
            "Start the video engine while the chat model holds the card, "
            "and it says so, out loud, instead of failing later.",
            "Renders queue and wait their turn. Nothing silently dies.",
        ],
        action=act_conflict,
        verify=v_plugins,
        reset=reset_conflict,
    ),
    Beat(
        name="jobs_activity",
        narration=[
            "Two ledgers keep it honest.",
            "Jobs is what you queued.",
            "",
            "Activity is what the system is doing on its own: indexing, "
            "training, self-improvement. Both, with history.",
        ],
        action=act_jobs,
        verify=v_activity,
        reset=reset_jobs,
    ),
    Beat(
        name="killswitch",
        narration=[
            "And when you want it to stop... it stops.",
            "The kill switch reads the machine first: CPU, memory, every "
            "live process.",
            "",
            "And below the app there's a shell script that talks straight "
            "to the database and the operating system.",
            "It works even when the app doesn't.",
        ],
        action=act_killswitch,
        verify=v_settings,
        reset=reset_settings,
        min_hold=4.0,
    ),
    Beat(
        name="cli",
        narration=[
            "Prefer a terminal? The whole platform ships as a command "
            "line.",
            "",
            "Chat, search, files, generation. Same backend, no browser.",
        ],
        action=act_cli,
        verify=v_any,
        reset=reset_cli,
    ),
    Beat(
        name="closer",
        narration=[
            "See everything. Gate everything. Kill everything.",
            "That's the deal that makes the rest of this series possible.",
            "",
            "Every episode ran on the card you're looking at.",
            "",
            "One machine. No cloud.",
        ],
        action=act_closer,
        verify=v_plugins,
        reset=reset_settings,
    ),
]


def main():
    ep = Episode("ep12_command", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=60_000)
        stage.page.wait_for_timeout(2000)
        for warm in ("/plugins", "/tasks", "/activity", "/settings"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP12 COMPLETE: {final}")
    finally:
        kill_stage_terminal()
        stage.close()


if __name__ == "__main__":
    main()
