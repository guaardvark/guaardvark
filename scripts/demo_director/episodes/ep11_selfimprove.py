"""Episode 11 — The System That Fixes Itself (≈3:00).

The live System Map constellation, the real self-improvement fix queue,
the autoresearch surface with the runaway retro told straight, the swarm
launch machinery (flight mode) without a live launch, and the codebase
lock as the closer.

GPU cast: none beyond an idle Ollama. Kokoro narration via audio foundry.
Requires: swarm plugin running (service Online), backend healthy.

Run from scripts/demo_director/:  venv/bin/python episodes/ep11_selfimprove.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage, FRONTEND  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


def goto(st: Stage, path: str, settle: float = 2.5):
    st.page.goto(FRONTEND + path, wait_until="load", timeout=60_000)
    st.page.wait_for_timeout(int(settle * 1000))


def close_dialogs(st: Stage):
    for _ in range(2):
        if st.page.locator("div[role='dialog']").count():
            st.cursor._xdo("key", "Escape")
            time.sleep(0.8)


# ---------------------------------------------------------------- beats

def reset_map(st: Stage):
    close_dialogs(st)
    goto(st, "/system-map", settle=2.0)
    st.page.get_by_text(re.compile(r"\d+ modules · \d+ edges")).first.wait_for(
        state="visible", timeout=90_000)
    time.sleep(1.0)


def act_map(st: Stage):
    st.hover_over(st.page.get_by_text(
        re.compile(r"\d+ modules · \d+ edges")).first, dur=0.9)
    time.sleep(1.5)
    search = st.page.get_by_placeholder("Search ( / )").first
    st.glide_click(search, dur=0.8)
    st.cursor.type_text("agent", delay_ms=70)
    time.sleep(2.5)
    st.cursor._xdo("key", "Escape")
    time.sleep(0.6)
    st.cursor._xdo("key", "Escape")
    time.sleep(0.8)
    findings_tab = st.page.get_by_role("button", name="Findings")
    if findings_tab.count():
        st.glide_click(findings_tab.first, dur=0.7)
        time.sleep(2.0)
    send = st.page.locator("[data-testid='SendIcon']")
    if send.count():
        st.glide_click(send.first.locator("xpath=ancestor::button[1]"),
                       dur=0.8)
        try:
            st.page.get_by_text(re.compile(
                "Dispatched to the self-improvement agent")).first.wait_for(
                state="visible", timeout=20_000)
        except Exception:
            pass
        time.sleep(1.5)
    st.cursor._xdo("key", "r")
    time.sleep(1.5)


def v_map(st: Stage):
    assert "/system-map" in st.path(), st.path()


def reset_settings(st: Stage):
    close_dialogs(st)
    goto(st, "/settings", settle=2.5)


def open_uncle_claude(st: Stage):
    """Scroll to the Uncle Claude card and expand it if collapsed."""
    anchor = st.page.get_by_text("Uncle Claude", exact=True).first
    x, y = st.screen_xy(anchor)
    st.cursor.glide(x, y, dur=0.8)
    time.sleep(1.0)
    try:
        st.page.get_by_text("Codebase Protection").first.wait_for(
            state="visible", timeout=15_000)
        return
    except Exception:
        pass
    st.glide_click(anchor, dur=0.5)
    time.sleep(1.5)
    st.page.get_by_text("Codebase Protection").first.wait_for(
        state="visible", timeout=15_000)


def act_fixes(st: Stage):
    open_uncle_claude(st)
    time.sleep(0.5)
    view = st.page.get_by_role("button", name=re.compile(r"view details"))
    st.glide_click(view.first, dur=0.8)
    st.page.get_by_text("Self-Improvement Fixes").first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.5)
    rows = st.page.locator("div[role='dialog'] li, div[role='dialog'] "
                           ".MuiListItemButton-root")
    if rows.count():
        st.glide_click(rows.first, dur=0.8)
        time.sleep(2.5)
    diff = st.page.get_by_text("Proposed diff")
    if diff.count():
        st.hover_over(diff.first, dur=0.8)
        time.sleep(1.5)
    close_btn = st.page.locator("div[role='dialog']").get_by_role(
        "button", name="Close")
    if close_btn.count():
        st.glide_click(close_btn.last, dur=0.7)
    else:
        st.cursor._xdo("key", "Escape")
    time.sleep(0.8)


def v_settings(st: Stage):
    assert "/settings" in st.path(), st.path()


def reset_autoresearch(st: Stage):
    close_dialogs(st)
    goto(st, "/autoresearch", settle=2.5)


def act_autoresearch(st: Stage):
    st.hover_over(st.page.get_by_role(
        "button", name="Research Tonight").first, dur=0.9)
    time.sleep(1.8)
    st.hover_over(st.page.get_by_label("Budget (hours)").first, dur=0.7)
    time.sleep(1.2)
    runs = st.page.get_by_text("Runs", exact=True)
    if runs.count():
        st.hover_over(runs.first, dur=0.8)
        time.sleep(1.2)
    revert = st.page.get_by_role("button", name="Revert to previous")
    if revert.count():
        st.hover_over(revert.first, dur=0.9)
        time.sleep(2.0)


def v_autoresearch(st: Stage):
    assert "/autoresearch" in st.path(), st.path()


def reset_swarm(st: Stage):
    close_dialogs(st)
    goto(st, "/swarm", settle=2.5)


def act_swarm(st: Stage):
    launch = st.page.get_by_role("button", name="Launch Swarm")
    launch.first.wait_for(state="visible", timeout=20_000)
    tmpl = st.page.get_by_text(re.compile(r"\d+ tasks")).first
    if tmpl.count():
        st.hover_over(tmpl, dur=0.9)
        time.sleep(1.5)
    st.glide_click(launch.first, dur=0.9)
    st.page.get_by_text("Launch Swarm", exact=True).last.wait_for(
        state="visible", timeout=10_000)
    time.sleep(1.5)
    flight = st.page.get_by_text("Flight Mode", exact=True).last
    st.glide_click(flight, dur=0.8)
    time.sleep(1.0)
    try:
        st.page.get_by_text(re.compile(
            "offline backends only")).first.wait_for(
            state="visible", timeout=5_000)
    except Exception:
        pass
    st.hover_over(st.page.get_by_role(
        "button", name=re.compile(r"Launch \(Flight Mode\)")).first, dur=0.8)
    time.sleep(2.0)
    cancel = st.page.locator("div[role='dialog']").get_by_role(
        "button", name="Cancel")
    st.glide_click(cancel.first, dur=0.7)
    time.sleep(0.8)


def v_swarm(st: Stage):
    assert "/swarm" in st.path(), st.path()


def act_lock(st: Stage):
    open_uncle_claude(st)
    prot = st.page.get_by_text("Codebase Protection").first
    x, y = st.screen_xy(prot)
    st.cursor.glide(x, y, dur=0.8)
    time.sleep(1.0)
    lock = st.page.locator("button:has(svg[data-testid='LockIcon'])")
    if not lock.count():
        st.page.locator(
            "button:has(svg[data-testid='LockOpenIcon'])").first.click(
            timeout=10_000)
        time.sleep(2.0)
        lock = st.page.locator("button:has(svg[data-testid='LockIcon'])")
    st.glide_click(lock.first, dur=0.8)
    st.page.get_by_text(
        "Codebase is locked. Autonomous edits are blocked.").first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(2.5)
    unlock = st.page.locator("button:has(svg[data-testid='LockOpenIcon'])")
    st.glide_click(unlock.first, dur=0.8)
    time.sleep(1.5)


BEATS = [
    Beat(
        name="hook_map",
        narration=[
            "Every night, this system runs its own test suite. When "
            "something fails, it proposes its own fix.",
            "",
            "Before you trust that, you need this: the system map. Over a "
            "thousand modules, computed from the real code, live.",
            "Search it. Rank its findings. And send any finding straight "
            "to the self-improvement agent.",
        ],
        action=act_map,
        verify=v_map,
        reset=reset_map,
    ),
    Beat(
        name="fixes",
        narration=[
            "Here's the fix queue. These are real: file, cause, and a "
            "proposed diff for each.",
            "",
            "It never fabricates a change. Every fix waits as a diff you "
            "can read, approve, or reject.",
            "An outside guardian model reviews the risky ones. And you "
            "are the last gate.",
        ],
        action=act_fixes,
        verify=v_settings,
        reset=reset_settings,
    ),
    Beat(
        name="autoresearch",
        narration=[
            "Autoresearch tunes the retrieval system overnight, on a "
            "budget you set.",
            "",
            "And here's the honesty beat. An early version of this ran "
            "away: three point four days, a hundred and thirty four "
            "million database rows.",
            "We found it, fixed it, and built the kill switches you'll "
            "see in episode twelve because of it.",
            "It keeps the wins. It reverts the regressions. And now it "
            "knows when to stop.",
        ],
        action=act_autoresearch,
        verify=v_autoresearch,
        reset=reset_autoresearch,
    ),
    Beat(
        name="swarm",
        narration=[
            "When one agent isn't enough, launch a swarm: parallel "
            "agents, each in its own isolated git worktree.",
            "",
            "And flip on flight mode, and they run entirely on local "
            "models. Network cable optional.",
        ],
        action=act_swarm,
        verify=v_swarm,
        reset=reset_swarm,
    ),
    Beat(
        name="lock_closer",
        narration=[
            "One more thing. The codebase lock.",
            "One click, and the system cannot edit its own code. Not "
            "even to fix itself.",
            "",
            "The AI never holds the keys to its own guardrails.",
            "Autonomy needs a leash. Next: the command center, and every "
            "way to pull the plug.",
            "",
            "One machine. No cloud.",
        ],
        action=act_lock,
        verify=v_settings,
        reset=reset_settings,
    ),
]


def main():
    ep = Episode("ep11_selfimprove", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=60_000)
        stage.page.wait_for_timeout(2000)
        for warm in ("/system-map", "/settings", "/autoresearch", "/swarm"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=90_000)
            stage.page.wait_for_timeout(2500)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP11 COMPLETE: {final}")
    finally:
        stage.close()


if __name__ == "__main__":
    main()
