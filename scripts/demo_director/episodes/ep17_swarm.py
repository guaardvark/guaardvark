"""Episode 17 — Five Agents, One Repo (≈5:00). DRAFT — dry-run before shooting.

The swarm sidecar, six templates, the launch dialog, a real launch of the
three-task JSX inventory template, the live graph, one worktree's diff, the
worktree layout in a terminal, merge / clean up, and what the launch proved.

GPU cast: Ollama + Audio Foundry. Requires: swarm plugin running on 8210,
a clean tree on a scratch branch, one end-to-end run of the same template
completed before the shoot (asset session), backend restarted with private
extensions parked (the Plugins page is on camera).

Run from scripts/demo_director/:  venv/bin/python episodes/ep17_swarm.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage  # noqa: E402
from helpers import (  # noqa: E402
    REPO, api_get, close_dialogs, goto, kill_stage_terminal, require,
    set_nav_chrome, stage_terminal, verify_no_private_names, verify_path)

TEMPLATE = "Inventory Frontend JSX Files"


def reset_on(path: str):
    def _reset(st: Stage):
        close_dialogs(st)
        kill_stage_terminal()
        set_nav_chrome(st, "software", path=path)
        time.sleep(1.0)
    return _reset


def reset_swarm(st: Stage):
    reset_on("/swarm")(st)
    health = api_get("/api/swarm/health")
    require(health.get("online"), "swarm sidecar offline")
    tmpl = api_get("/api/swarm/templates")
    require(tmpl.get("count", 0) >= 6, "fewer than six templates")


def act_sidecar(st: Stage):
    card = st.page.locator(".MuiCard-root").filter(has_text="Swarm").first
    st.hover_over(card, dur=0.9)
    time.sleep(2.0)
    port = card.get_by_text(re.compile("8210"))
    if port.count():
        st.hover_over(port.first, dur=0.6)
        time.sleep(1.5)


def act_templates(st: Stage):
    st.hover_over(st.page.get_by_text("Quick Launch Templates").first, dur=0.8)
    time.sleep(1.0)
    for title in ("Swarm Plan: Autoresearch Code-Tuning Run", TEMPLATE,
                  "Swarm Plan: Flight Mode Demo"):
        c = st.page.get_by_text(title, exact=True)
        if c.count():
            st.hover_over(c.first, dur=0.7)
            time.sleep(1.4)


def act_launch(st: Stage):
    st.glide_click(st.page.get_by_text(TEMPLATE, exact=True).first, dur=0.8)
    st.page.get_by_text("Launch Swarm", exact=True).first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.0)
    for label in ("Max concurrent agents", "Flight Mode", "Auto-merge"):
        hit = st.page.locator("div[role='dialog']").get_by_text(label)
        if hit.count():
            st.hover_over(hit.first, dur=0.6)
            time.sleep(1.2)
    fm = st.page.locator("div[role='dialog']").get_by_role("checkbox").first
    st.glide_click(fm, dur=0.6)
    time.sleep(2.0)
    st.glide_click(fm, dur=0.4)
    time.sleep(0.8)
    launch = st.page.locator("div[role='dialog']").get_by_role(
        "button", name=re.compile(r"^Launch"))
    st.glide_click(launch.first, dur=0.8)
    st.page.get_by_text("Active Swarms").first.wait_for(state="visible", timeout=60_000)
    time.sleep(3.0)


def reset_graph(st: Stage):
    reset_on("/swarm")(st)
    status = api_get("/api/swarm/status")
    require(status.get("count", 0) >= 1, "no swarm running — launch beat must precede")


def act_graph(st: Stage):
    hdr = st.page.get_by_text("DEPENDENCY GRAPH")
    if not hdr.count():
        st.glide_click(st.page.locator(".MuiCard-root").filter(
            has_text=re.compile(r"Running|Completed")).first, dur=0.8)
        time.sleep(1.5)
    if st.page.get_by_text("DEPENDENCY GRAPH").count():
        st.hover_over(st.page.get_by_text("DEPENDENCY GRAPH").first, dur=0.8)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if st.page.get_by_text(re.compile(r"\bRunning\b")).count():
            break
        time.sleep(1.0)
    time.sleep(6.0)


def act_worktrees(st: Stage):
    task = st.page.locator(".MuiCard-root").filter(has_text=re.compile(r"task", re.I)).last
    if task.count():
        st.glide_click(task, dur=0.8)
        tab = st.page.get_by_role("tab", name="Live Diff")
        if tab.count():
            st.glide_click(tab.first, dur=0.7)
            time.sleep(4.0)
        close_dialogs(st)
    stage_terminal("git worktree list; echo; ls .swarm-worktrees/*/ | head; sleep 30")
    time.sleep(8.0)


def act_merge(st: Stage):
    for label in ("Merge All", "Clean Up"):
        b = st.page.get_by_role("button", name=label)
        if b.count():
            st.hover_over(b.first, dur=0.8)
            time.sleep(1.5)
    st.hover_over(st.page.get_by_text("Recent Swarms").first, dur=0.8)
    time.sleep(2.0)


def act_proof(st: Stage):
    stage_terminal("sed -n 24,30p plugins/swarm/service/resource_monitor.py; sleep 30")
    time.sleep(6.0)


def v_any(st: Stage):
    verify_no_private_names(st)


BEATS = [
    Beat(name="sidecar",
         narration=[
             "Five agents. One repository. Every one of them in its own copy.",
             "",
             "The swarm is a sidecar on port eighty-two ten. Off by default, "
             "one toggle to start.",
         ],
         action=act_sidecar, verify=lambda st: verify_path(st, "/plugins"),
         reset=reset_on("/plugins")),
    Beat(name="templates",
         narration=[
             "Six templates. The first one is what the overnight director "
             "launches, and its ground rules are on screen: one experiment "
             "per arm, the eval harness is the fitness, never self-score, "
             "never edit a test to make it pass.",
         ],
         action=act_templates, verify=lambda st: verify_path(st, "/swarm"),
         reset=reset_swarm),
    Beat(name="launch",
         narration=[
             "Launch. Five agents at most. Flight mode keeps every backend "
             "that needs the internet out, and says so. Auto-merge stays "
             "off.",
             "",
             "Three tasks, one inventory of the frontend. Go.",
         ],
         action=act_launch, verify=lambda st: verify_path(st, "/swarm"),
         reset=reset_swarm),
    Beat(name="graph",
         narration=[
             "The dependency graph, live. Grey is pending, blue is running, "
             "green is done. A task with a blocker waits for it.",
         ],
         action=act_graph, verify=lambda st: verify_path(st, "/swarm"),
         reset=reset_graph),
    Beat(name="worktrees",
         narration=[
             "Every task works in its own git worktree, on its own branch. "
             "Live diff shows what it has changed so far.",
             "",
             "In the shell, worktree list shows the copies. Nothing touches "
             "your branch until you merge.",
         ],
         action=act_worktrees, verify=v_any, reset=reset_graph),
    Beat(name="merge",
         narration=[
             "Merge all runs the tests first. Clean up removes the worktrees. "
             "History keeps the cost, or says free when it ran on the local "
             "model.",
         ],
         action=act_merge, verify=lambda st: verify_path(st, "/swarm"),
         reset=reset_on("/swarm")),
    Beat(name="proof",
         narration=[
             "What this launch proved, and no more. The offline backend is "
             "reported two ways by two health routes on this box, so this "
             "run used the online one.",
             "The resource monitor stops spawning at eighty-five percent "
             "C P U, ninety percent R A M, or five hundred megabytes of free "
             "video memory.",
             "",
             "That is series two. Thirteen to seventeen. One machine. No cloud.",
         ],
         action=act_proof, verify=v_any, reset=reset_on("/swarm")),
]


def main():
    ep = Episode("ep17_swarm", BEATS, out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        for warm in ("/", "/plugins", "/swarm"):
            goto(stage, warm, settle=2.0)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        print(f"\nEP17 COMPLETE: {ep.produce(stage)}")
    finally:
        kill_stage_terminal()
        stage.close()


if __name__ == "__main__":
    main()
