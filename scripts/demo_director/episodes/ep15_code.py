"""Episode 15 — Guaardvark Codes (≈6:00). DRAFT — dry-run before shooting.

The code editor, chat that runs workstation tools, the codebase lock, a
self-check run and the fix queue, the guardian's directives, the unified
overnight director and its morning report, and the scheduler gate.

GPU cast: Ollama + Audio Foundry. Requires: one completed research run on
/autoresearch (unified mode), at least one PendingFix, self-improvement
enabled and idle.

Run from scripts/demo_director/:  venv/bin/python episodes/ep15_code.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import FRONTEND, Beat, Episode, Stage  # noqa: E402
from helpers import (  # noqa: E402
    REPO, api_get, close_dialogs, goto, kill_stage_terminal, require,
    set_nav_chrome, stage_terminal, verify_no_private_names, verify_path)

CHAT_INPUT = "Type your message, paste an image, or use voice..."


def press(st: Stage, key: str, settle: float = 0.6):
    st.cursor._xdo("key", key)
    time.sleep(settle)


def reset_on(path: str, wait_text: str | None = None, timeout: int = 60_000):
    def _reset(st: Stage):
        close_dialogs(st)
        kill_stage_terminal()
        set_nav_chrome(st, "software", path=path)
        if wait_text:
            st.page.get_by_text(wait_text, exact=True).first.wait_for(
                state="visible", timeout=timeout)
    return _reset


def open_uncle_claude(st: Stage):
    anchor = st.page.get_by_text("Uncle Claude", exact=True).first
    st.hover_over(anchor, dur=0.8)
    time.sleep(0.8)
    if not st.page.get_by_text("Codebase Protection").count():
        st.glide_click(anchor, dur=0.5)
    st.page.get_by_text("Codebase Protection").first.wait_for(
        state="visible", timeout=15_000)


# ------------------------------------------------------------------ beats

def act_editor(st: Stage):
    for tip in ("Run Code (Ctrl+R)", "Format Code (Ctrl+Shift+F)",
                "Debug (F5)", "Build (Ctrl+B)"):
        b = st.page.locator(f"[aria-label='{tip}'], [title='{tip}']")
        if b.count():
            st.hover_over(b.first, dur=0.6)
            time.sleep(1.0)
    press(st, "ctrl+shift+o", settle=1.5)
    if st.page.get_by_text(re.compile("Find Symbol")).count():
        st.cursor.type_text("kickoff", delay_ms=70)
        time.sleep(2.0)
        press(st, "Escape", settle=0.8)
    time.sleep(1.0)


def act_tools(st: Stage):
    box = st.page.get_by_placeholder(CHAT_INPUT).first
    st.glide_click(box, dur=0.8)
    st.cursor.type_text("check the logs for errors in the last hour", delay_ms=30)
    press(st, "Return", settle=1.0)
    try:
        st.page.get_by_text(re.compile(r"read_logs")).first.wait_for(
            state="visible", timeout=90_000)
    except Exception:
        pass
    time.sleep(3.0)


def act_lock(st: Stage):
    open_uncle_claude(st)
    prot = st.page.get_by_text("Codebase Protection").first
    st.hover_over(prot, dur=0.8)
    time.sleep(0.8)
    lock = st.page.locator("button:has(svg[data-testid='LockIcon'])")
    require(lock.count(), "codebase already locked; unlock before the take")
    st.glide_click(lock.first, dur=0.8)
    st.page.get_by_text("Codebase is locked. Autonomous edits are blocked.").first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(2.5)
    unlock = st.page.locator("button:has(svg[data-testid='LockOpenIcon'])")
    st.glide_click(unlock.first, dur=0.8)
    time.sleep(1.5)


def act_selfcheck(st: Stage):
    open_uncle_claude(st)
    run = st.page.get_by_role("button", name=re.compile("Run Self-Check"))
    st.glide_click(run.first, dur=0.8)
    time.sleep(6.0)
    close_dialogs(st)
    view = st.page.get_by_role("button", name=re.compile(r"view details"))
    st.glide_click(view.first, dur=0.8)
    st.page.get_by_text("Self-Improvement Fixes").first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.5)
    for label in (r"Approve all", r"Apply all"):
        b = st.page.locator("div[role='dialog']").get_by_role(
            "button", name=re.compile(label))
        if b.count():
            st.hover_over(b.first, dur=0.7)
            time.sleep(1.2)
    close_btn = st.page.locator("div[role='dialog']").get_by_role("button", name="Close")
    if close_btn.count():
        st.glide_click(close_btn.last, dur=0.6)


def act_guardian(st: Stage):
    open_uncle_claude(st)
    for label in ("Escalation Mode", "Token Budget", "Self-Improvement"):
        hit = st.page.get_by_text(label, exact=True)
        if hit.count():
            st.hover_over(hit.first, dur=0.7)
            time.sleep(1.4)
    stage_terminal("sed -n 21,24p backend/services/claude_advisor_service.py; sleep 25")
    time.sleep(6.0)


def reset_overnight(st: Stage):
    reset_on("/autoresearch")(st)
    require(not st.page.get_by_text("No research runs yet.").count(),
            "no research run on /autoresearch — seed one first")


def act_overnight(st: Stage):
    for label in ("Unified", "Retrieval", "Code"):
        b = st.page.get_by_role("button", name=label, exact=True)
        if b.count():
            st.hover_over(b.first, dur=0.5)
            time.sleep(0.7)
    for pat in (r"Headline", r"Promotion", r"Director"):
        hit = st.page.get_by_text(re.compile(pat))
        if hit.count():
            st.hover_over(hit.first, dur=0.8)
            time.sleep(1.8)
    ledger = st.page.locator("[aria-label*='ledger'], [title*='ledger']")
    if ledger.count():
        st.hover_over(ledger.first, dur=0.7)
        time.sleep(1.2)


def act_gate(st: Stage):
    stage_terminal("git log --oneline -3 -- backend/celery_beat_gates.py; echo; "
                   "sed -n 101,106p backend/celery_beat_gates.py; sleep 30")
    time.sleep(8.0)


def act_closer(st: Stage):
    st.hover_over(st.page.get_by_text("Uncle Claude", exact=True).first, dur=0.9)
    time.sleep(2.0)


def v_any(st: Stage):
    verify_no_private_names(st)


BEATS = [
    Beat(name="editor",
         narration=[
             "This product edits its own code. That sentence should worry you.",
             "",
             "So here is every gate between an idea and a changed file. "
             "Starting with the editor: run, format, debug, build. Find any "
             "symbol with control shift O.",
         ],
         action=act_editor, verify=lambda st: verify_path(st, "/code-editor"),
         reset=reset_on("/code-editor")),
    Beat(name="tools",
         narration=[
             "Chat can run the workstation now, not just describe it. Say "
             "check the logs, and the tool runs. Eight tools: the mapper, "
             "the G P U, the logs, the swarm, self-improvement.",
             "",
             "The three that act, not report, carry an approval flag.",
         ],
         action=act_tools, verify=lambda st: verify_path(st, "/chat"),
         reset=reset_on("/chat")),
    Beat(name="lock",
         narration=[
             "The lock. One switch, and every writer gets a four twenty "
             "three. The nightly agent, the swarm, a person at the A P I. "
             "The AI cannot edit its own guardrails.",
         ],
         action=act_lock, verify=lambda st: verify_path(st, "/settings"),
         reset=reset_on("/settings", "Uncle Claude")),
    Beat(name="selfcheck",
         narration=[
             "Run a self-check and the tests run. Anything it wants to "
             "change lands in this queue as a diff: the file, the cause, "
             "the exact lines.",
             "",
             "Approve all, apply all, or one at a time. A directed run only "
             "counts as a success when a fix was actually staged.",
         ],
         action=act_selfcheck, verify=lambda st: verify_path(st, "/settings"),
         reset=reset_on("/settings", "Uncle Claude")),
    Beat(name="guardian",
         narration=[
             "Above the queue sits a guardian: an independent model that "
             "reviews the change and answers with one of six directives.",
             "Proceed. Proceed with caution. Reject. Halt self-improvement. "
             "Lock the codebase. Halt the family.",
         ],
         action=act_guardian, verify=v_any,
         reset=reset_on("/settings", "Uncle Claude")),
    Beat(name="overnight",
         narration=[
             "One director runs overnight with three hands: retrieval "
             "tuning, code tuning in a swarm, and a test snapshot. Seventy "
             "thirty at first, thirty seventy once retrieval plateaus.",
             "",
             "The morning report says what changed and why. Code arms "
             "cannot score their own keep, and nothing merges to main on "
             "its own.",
         ],
         action=act_overnight, verify=lambda st: verify_path(st, "/autoresearch"),
         reset=reset_overnight),
    Beat(name="gate",
         narration=[
             "One honest story. A settings toggle used to be advisory: the "
             "scheduler fired every ten minutes and the task early-returned.",
             "Now the scheduler reads the toggle itself. And the first "
             "version parked a closed entry on top of its heap, so nothing "
             "ran for eleven minutes after a restart. Fixed, and told "
             "straight.",
         ],
         action=act_gate, verify=v_any, reset=reset_on("/settings")),
    Beat(name="closer",
         narration=[
             "An editor, a lock, a queue, a guardian, and a director that "
             "reports to you in the morning.",
             "",
             "Next: plugging other tools into all of this, over M C P.",
         ],
         action=act_closer, verify=v_any,
         reset=reset_on("/settings", "Uncle Claude")),
]


def main():
    ep = Episode("ep15_code", BEATS, out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        for warm in ("/", "/code-editor", "/chat", "/settings", "/autoresearch"):
            goto(stage, warm, settle=2.0)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        print(f"\nEP15 COMPLETE: {ep.produce(stage)}")
    finally:
        kill_stage_terminal()
        stage.close()


if __name__ == "__main__":
    main()
