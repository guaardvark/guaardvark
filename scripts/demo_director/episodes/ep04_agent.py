"""Episode 4 — The Agent Behind the Glass (≈3:00).

The only vision-driven episode: the AgentScreenViewer over the agent's
real XFCE desktop on :99, /agent mode, a live SEE-THINK-ACT-VERIFY task,
learn-by-demonstration in the Interactive Trainer (guided autonomy), and
the eye bake-off honesty beat with tonight's real miss distance.

GPU cast: Ollama (gemma4 vision) ONLY — no renders during this shoot.
Assets: data/demo_assets/ep04_bakeoff.txt (pre-run tonight, real numbers).
The :99 display must be running (scripts/start_agent_display.sh start).

Run from scripts/demo_director/:  venv/bin/python episodes/ep04_agent.py
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
BAKEOFF = REPO / "data" / "demo_assets" / "ep04_bakeoff.txt"
CHAT_PLACEHOLDER = "Type your message, paste an image, or use voice..."
AGENT_PLACEHOLDER = ("Describe a screen action — every message is a task "
                     "while in agent mode")
AGENT_CHIP = "AGENT MODE — type /chat to exit"
TASK = "Open the Documents folder on the desktop."

_A99 = {**os.environ, "DISPLAY": ":99"}


def a99(*args, check=False):
    return subprocess.run(["xdotool", *args], env=_A99, check=check,
                          capture_output=True, text=True)


def a99_gtk_env():
    env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
    env["DISPLAY"] = ":99"
    env["GDK_BACKEND"] = "x11"
    return env


def thunar_open() -> bool:
    r = a99("search", "--class", "Thunar")
    return bool(r.stdout.strip())


def kill_agent_windows():
    a99("search", "--class", "Thunar", "windowkill")
    subprocess.run(["pkill", "-f", "zenity --text-inf[o]"], check=False)


def chat_input(st: Stage):
    return st.page.get_by_placeholder(CHAT_PLACEHOLDER)


def agent_input(st: Stage):
    return st.page.get_by_placeholder(AGENT_PLACEHOLDER)


def any_input(st: Stage):
    loc = agent_input(st)
    return loc if loc.count() else chat_input(st)


def type_and_send(st: Stage, text: str, delay_ms: int = 26):
    box = any_input(st).first
    st.glide_click(box, dur=0.7)
    st.cursor.type_text(text, delay_ms=delay_ms)
    time.sleep(0.5)
    if st.page.locator("[data-command-row]").count():
        st.cursor._xdo("key", "Escape")
        time.sleep(0.3)
    st.cursor._xdo("key", "Return")


def close_viewer(st: Stage):
    """Close the Agent Screen card if open (off camera, for clean retakes)."""
    try:
        if st.page.locator("img[alt='Agent screen']").count():
            st.page.locator(
                ".header-btn:has(svg[data-testid='CloseIcon'])").last.click(
                timeout=3000)
            st.page.wait_for_timeout(600)
    except Exception:
        pass


def close_trainer(st: Stage):
    try:
        rq.post(f"{API}/api/agent-control/learn/stop", timeout=15)
    except Exception:
        pass
    try:
        if st.page.get_by_text("Trainer", exact=True).count():
            st.page.locator(
                ".header-btn:has(svg[data-testid='CloseIcon'])").last.click(
                timeout=3000)
            st.page.wait_for_timeout(600)
    except Exception:
        pass


def delete_demos():
    try:
        demos = rq.get(f"{API}/api/agent-control/learn/demonstrations",
                       timeout=15).json().get("demonstrations", [])
        for d in demos:
            rq.delete(
                f"{API}/api/agent-control/learn/demonstrations/{d['id']}",
                timeout=15)
    except Exception:
        pass


# ---------------------------------------------------------------- resets

def reset_hook(st: Stage):
    kill_agent_windows()
    st.page.goto(FRONTEND + "/chat", wait_until="load", timeout=60_000)
    st.page.wait_for_timeout(2500)
    close_viewer(st)
    try:
        st.page.locator("button:has(svg[data-testid='AddIcon'])").first.click(
            timeout=5000)
        st.page.wait_for_timeout(1000)
    except Exception:
        pass


def ensure_viewer(st: Stage):
    if not st.page.locator("img[alt='Agent screen']").count():
        rail = st.page.locator(
            ".MuiDrawer-paper [data-testid='DesktopWindowsIcon']").first
        st.glide_click(rail.locator("xpath=ancestor::button[1]"), dur=0.8)
        st.page.locator("img[alt='Agent screen']").wait_for(
            state="visible", timeout=30_000)
        time.sleep(1.0)


def ensure_agent_mode(st: Stage):
    """Off camera: make sure this session is in agent mode."""
    if st.page.get_by_text(AGENT_CHIP).count():
        return
    try:
        box = chat_input(st).first
        box.click(timeout=5000)
        box.type("/agent", delay=20)
        st.page.keyboard.press("Escape")
        st.page.keyboard.press("Enter")
        st.page.get_by_text(AGENT_CHIP).first.wait_for(
            state="visible", timeout=15_000)
        time.sleep(0.8)
    except Exception:
        pass


def kill_agent_task():
    try:
        rq.post(f"{API}/api/agent-control/kill", timeout=15)
    except Exception:
        pass
    # the task thread can take tens of seconds to unwind a vision call —
    # learn/start refuses while it's still active
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            status = rq.get(f"{API}/api/agent-control/status",
                            timeout=5).json()["status"]
            if not status.get("active"):
                break
        except Exception:
            break
        time.sleep(2.0)
    time.sleep(1.0)


def reset_soft(st: Stage):
    kill_agent_task()
    kill_agent_windows()
    if "/chat" not in st.path():
        st.nav_via_sidebar("Chat", "/chat", any_input(st))
    ensure_viewer(st)
    ensure_agent_mode(st)
    time.sleep(0.8)


def reset_trainer(st: Stage):
    kill_agent_task()
    kill_agent_windows()
    delete_demos()
    close_trainer(st)
    ensure_viewer(st)
    time.sleep(0.5)


# ---------------------------------------------------------------- beats

def act_hook(st: Stage):
    rail = st.page.locator(
        ".MuiDrawer-paper [data-testid='DesktopWindowsIcon']").first
    st.glide_click(rail.locator("xpath=ancestor::button[1]"), dur=0.9)
    st.page.locator("img[alt='Agent screen']").wait_for(
        state="visible", timeout=30_000)
    time.sleep(2.5)
    title = st.page.get_by_text("Agent Screen", exact=True).last
    hx, hy = st.screen_xy(title)
    st.cursor.glide(hx, hy, dur=0.7)
    time.sleep(0.3)
    st.cursor.drag(hx - 40, hy + 30, dur=0.9)
    time.sleep(1.5)


def v_viewer(st: Stage):
    assert st.page.locator("img[alt='Agent screen']").count() >= 1


def act_agent_mode(st: Stage):
    type_and_send(st, "/agent")
    st.page.get_by_text(AGENT_CHIP).first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.5)


def v_agent_mode(st: Stage):
    assert st.page.get_by_text(AGENT_CHIP).count() >= 1


def act_task(st: Stage):
    type_and_send(st, TASK, delay_ms=22)
    trail = st.page.get_by_text(re.compile(r"Agent thinking — \d+ steps?"))
    trail.first.wait_for(state="visible", timeout=150_000)
    # let the loop run on camera: labels stream, the servo clicks, the
    # verify gate judges — hold long enough for a few visible iterations
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        if thunar_open():
            break
        m = re.search(r"— (\d+) steps?",
                      trail.first.inner_text() if trail.count() else "")
        if m and int(m.group(1)) >= 3:
            time.sleep(6.0)
            break
        time.sleep(2.0)
    try:
        st.glide_click(trail.first, dur=0.8)
        time.sleep(2.5)
    except Exception:
        pass


def v_task(st: Stage):
    assert st.page.get_by_text(
        re.compile(r"Agent thinking — \d+ steps?")).count() >= 1


def act_trainer(st: Stage):
    st.nav_via_sidebar("Settings", "/settings",
                       st.page.get_by_role("button", name="Launch Trainer"))
    time.sleep(1.0)
    # the viewer card overlaps the Training card here — carry it down-left
    # so Launch Trainer is clickable and the desktop stays on camera
    try:
        title = st.page.get_by_text("Agent Screen", exact=True).last
        hx, hy = st.screen_xy(title)
        st.cursor.glide(hx, hy, dur=0.6)
        time.sleep(0.3)
        st.cursor.drag(430, 560, dur=1.0)
        time.sleep(0.8)
    except Exception:
        pass
    st.glide_click(
        st.page.get_by_role("button", name="Launch Trainer").first, dur=0.9)
    st.page.get_by_text("Trainer", exact=True).first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.2)
    st.glide_click(
        st.page.get_by_role("button", name="Start Recording").first, dur=0.8)
    st.page.get_by_text("Recording...").first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(2.0)
    # the human demonstration, performed on the agent display: open the
    # Pictures folder, give the recorder time to describe the click, close it
    a99("mousemove", "104", "460")
    time.sleep(0.6)
    a99("click", "1")
    time.sleep(18.0)
    a99("search", "--class", "Thunar", "windowactivate", "--sync")
    a99("key", "--clearmodifiers", "alt+F4")
    time.sleep(14.0)
    st.glide_click(
        st.page.get_by_role("button", name="Stop Recording").first, dur=0.8)
    time.sleep(3.0)
    st.page.get_by_text(re.compile(r"guided")).first.wait_for(
        state="visible", timeout=30_000)
    time.sleep(2.0)


def v_trainer(st: Stage):
    assert st.page.get_by_text(re.compile(r"guided")).count() >= 1


_CONFIRMS = {"n": 0}


def reset_attempt(st: Stage):
    kill_agent_task()
    kill_agent_windows()
    ensure_viewer(st)
    time.sleep(0.5)


def act_attempt(st: Stage):
    _CONFIRMS["n"] = 0
    floater = st.page.locator(".MuiPaper-root").filter(
        has_text="attempts").last
    play = floater.locator(
        "button:has(svg[data-testid='PlayArrowIcon'])").first
    st.glide_click(play, dur=0.9)
    time.sleep(1.0)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and _CONFIRMS["n"] < 2:
        btn = st.page.get_by_role("button", name="Confirm")
        if btn.count():
            st.glide_click(btn.first, dur=0.8)
            _CONFIRMS["n"] += 1
            time.sleep(3.0)
            continue
        time.sleep(1.5)
    time.sleep(3.0)


def v_attempt(st: Stage):
    assert _CONFIRMS["n"] >= 1, "no guided confirm happened"


def act_bakeoff(st: Stage):
    ensure_viewer(st)
    subprocess.Popen(
        ["zenity", "--text-info",
         "--title=Eye bake-off - click accuracy",
         f"--filename={BAKEOFF}",
         "--width=760", "--height=520", "--font=Monospace 13"],
        env=a99_gtk_env(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    time.sleep(3.0)
    img = st.page.locator("img[alt='Agent screen']").first
    st.hover_over(img, dur=1.0)
    time.sleep(3.0)


def v_bakeoff(st: Stage):
    r = a99("search", "--name", "bake-off")
    assert r.stdout.strip(), "bake-off panel not on :99"


def act_closer(st: Stage):
    subprocess.run(["pkill", "-f", "zenity --text-inf[o]"], check=False)
    time.sleep(1.0)
    if "/chat" not in st.path():
        st.nav_via_sidebar("Chat", "/chat", any_input(st))
    time.sleep(1.0)
    ensure_viewer(st)
    img = st.page.locator("img[alt='Agent screen']")
    if img.count():
        st.hover_over(img.first, dur=1.0)
    time.sleep(1.5)


def v_chat(st: Stage):
    assert "/chat" in st.path(), st.path()


BEATS = [
    Beat(
        name="hook_glass",
        narration=[
            "The hardest thing we ever built... is a mouse.",
            "",
            "This agent has its own desktop. Its own eyes. Its own hands.",
            "That window is its whole world: a virtual display, streaming "
            "live.",
            "Nothing it does can touch your screen.",
        ],
        action=act_hook,
        verify=v_viewer,
        reset=reset_hook,
    ),
    Beat(
        name="agent_mode",
        narration=[
            "Slash agent.",
            "",
            "From here on, every message in this chat is a task for "
            "those hands.",
        ],
        action=act_agent_mode,
        verify=v_agent_mode,
        reset=reset_soft,
    ),
    Beat(
        name="task_live",
        narration=[
            "Open the Documents folder.",
            "",
            "Watch the step labels. It looks at the screen. Decides. "
            "Clicks. Then looks again to check its own work.",
            "See. Think. Act. Verify.",
            "",
            "This is a brand new install, on real time. Its eye misses. "
            "A lot.",
            "But watch what it refuses to do: it will not call the job "
            "done without proof on the screen.",
            "No reporting intent as reality. That rule is the whole "
            "product.",
        ],
        action=act_task,
        verify=v_task,
        reset=reset_soft,
        retakes=3,
    ),
    Beat(
        name="trainer_demo",
        narration=[
            "So when the eye struggles, you teach the hands.",
            "Start a recording, and do the job once, by hand, on its "
            "screen.",
            "",
            "Every click is captured, described by the vision model, and "
            "distilled into replayable steps.",
            "That's a new skill. It starts at the bottom of the ladder: "
            "guided.",
        ],
        action=act_trainer,
        verify=v_trainer,
        reset=reset_trainer,
        retakes=3,
    ),
    Beat(
        name="attempt_guided",
        narration=[
            "Now the agent tries the job I just taught it.",
            "",
            "It starts guided: before every step, it shows its plan and "
            "waits for a yes.",
            "Approve enough clean runs and it earns supervised. Then "
            "autonomous.",
            "Promotion is earned, not granted.",
        ],
        action=act_attempt,
        verify=v_attempt,
        reset=reset_attempt,
        retakes=3,
    ),
    Beat(
        name="bakeoff",
        narration=[
            "One more thing, because honesty is the whole point.",
            "We test the agent's eye against ground truth. And we publish "
            "the number.",
            "",
            "Tonight: eighty pixels, mean error.",
            "That's why the verify step exists.",
        ],
        action=act_bakeoff,
        verify=v_bakeoff,
        reset=reset_soft,
    ),
    Beat(
        name="closer",
        narration=[
            "An agent you can watch, correct, and grade.",
            "What it does with those hands is up to you.",
            "",
            "Next: it makes pictures, and then it makes movies.",
            "",
            "One machine. No cloud.",
        ],
        action=act_closer,
        verify=v_chat,
        reset=reset_soft,
    ),
]


def main():
    ep = Episode("ep04_agent", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=60_000)
        stage.page.wait_for_timeout(1500)
        stage.page.evaluate(
            "localStorage.setItem('guaardvark_agent_screen_state',"
            " JSON.stringify({x:1150,y:150,w:720,h:800,fps:2,"
            "streaming:true,collapsed:false}))")
        for warm in ("/chat", "/settings", "/chat"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP04 COMPLETE: {final}")
    finally:
        kill_agent_windows()
        stage.close()


if __name__ == "__main__":
    main()
