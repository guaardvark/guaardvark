"""Episode 16 — Plug In Anything (≈4:30). DRAFT — dry-run before shooting.

MCP doctor, install --dry-run, the policy, an index profile for clients, a
Claude Code session calling Guaardvark tools, approvals, one recorded caveat.

GPU cast: ComfyUI (one image via generate_image) + Audio Foundry.
Requires: `python -m backend.mcp doctor` all PASS; Claude Code has the
guaardvark server installed; the AcmeCorp corpus indexed; backend restarted
with private extensions parked (the Connections page is on camera).

Run from scripts/demo_director/:  venv/bin/python episodes/ep16_mcp.py
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
    REPO, close_dialogs, goto, kill_stage_terminal, require, set_nav_chrome,
    stage_terminal, type_line, verify_no_private_names, verify_path)

PY = "backend/venv/bin/python"


def reset_terminal(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/dashboard")
    time.sleep(0.5)


def reset_doctor(st: Stage):
    reset_terminal(st)
    r = subprocess.run([PY, "-m", "backend.mcp", "doctor"], cwd=REPO,
                       capture_output=True, text=True, timeout=300)
    require(r.returncode == 0, f"mcp doctor failed:\n{r.stdout[-800:]}{r.stderr[-400:]}")


def act_doctor(st: Stage):
    stage_terminal(f"{PY} -m backend.mcp doctor; sleep 30")
    time.sleep(14.0)


def act_install(st: Stage):
    stage_terminal(f"{PY} -m backend.mcp install --dry-run; sleep 30")
    time.sleep(10.0)


def act_policy(st: Stage):
    stage_terminal(f"{PY} -m backend.mcp list-tools | tail -5; echo; "
                   f"sed -n 23,32p backend/mcp/config.py; sleep 30")
    time.sleep(12.0)


def reset_profiles(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/settings")
    st.page.get_by_text("Knowledge Index", exact=True).first.wait_for(
        state="visible", timeout=60_000)


def act_profiles(st: Stage):
    st.hover_over(st.page.get_by_text("Knowledge Index", exact=True).first, dur=0.9)
    time.sleep(1.0)
    for label in ("mcp", "local", "default"):
        hit = st.page.get_by_text(label, exact=True)
        if hit.count():
            st.hover_over(hit.first, dur=0.7)
            time.sleep(1.5)


def act_client(st: Stage):
    # Claude Code in the stage terminal, one-shot, tools restricted to the
    # guaardvark server. The corpus is synthetic; the repo is public.
    stage_terminal(
        "claude -p 'Using the guaardvark MCP tools only: search the knowledge base for "
        "AcmeCorp onboarding steps and quote two passages with their sources; then call "
        "inspect_gpu and report the free VRAM in one line.' "
        "--allowedTools 'mcp__guaardvark__search_knowledge_base,mcp__guaardvark__inspect_gpu'; "
        "sleep 40")
    time.sleep(45.0)


def reset_approvals(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/connections")
    time.sleep(1.0)


def act_approvals(st: Stage):
    tab = st.page.get_by_role("tab", name="MCP")
    if tab.count():
        st.glide_click(tab.first, dur=0.8)
        time.sleep(2.0)
    req = st.page.get_by_text(re.compile("Require approval"))
    if req.count():
        st.hover_over(req.first, dur=0.8)
        time.sleep(2.0)
    goto(st, "/approvals", settle=2.5)
    time.sleep(2.0)


def act_caveat(st: Stage):
    stage_terminal("git show --stat 3fe3885 | sed -n 1,12p; sleep 30")
    time.sleep(8.0)


def v_any(st: Stage):
    verify_no_private_names(st)


BEATS = [
    Beat(name="doctor",
         narration=[
             "Forty-three tools. Any client that speaks the protocol. And a "
             "policy that says no by default.",
             "",
             "Start with doctor. It checks the interpreter, the S D K, the "
             "tool registry, builds the server, and runs a real handshake "
             "against a subprocess. Pass or fail, per check.",
         ],
         action=act_doctor, verify=v_any, reset=reset_doctor),
    Beat(name="install",
         narration=[
             "Install writes the server into every client it finds: Cursor, "
             "Claude Code, Zed, Claude Desktop, Gemini, Grok. Existing files "
             "are backed up once. Other servers are never touched.",
             "This is the dry run. It says what it would do.",
         ],
         action=act_install, verify=v_any, reset=reset_terminal),
    Beat(name="policy",
         narration=[
             "Eighty-seven tools are registered. Forty-three are exposed. "
             "Seven categories are denied by default: the desktop, agent "
             "control, the shell, code execution, the browser, and the "
             "M C P proxies themselves.",
             "",
             "Anything that needs a human approval stays hidden too. That is "
             "why it is forty-three and not forty-six.",
         ],
         action=act_policy, verify=v_any, reset=reset_terminal),
    Beat(name="profiles",
         narration=[
             "Clients read differently from people. The M C P index profile "
             "wants twelve finer passages of four hundred characters, in its "
             "own vector table, so a client can chain over them.",
         ],
         action=act_profiles, verify=lambda st: verify_path(st, "/settings"),
         reset=reset_profiles),
    Beat(name="client",
         narration=[
             "A client on camera. Claude Code, restricted to two Guaardvark "
             "tools, against a synthetic corpus.",
             "",
             "It searches the knowledge base and gets cited passages, not a "
             "summary. Then it asks the card how much memory is free. Both "
             "calls run here. Only the words leave.",
         ],
         action=act_client, verify=v_any, reset=reset_terminal),
    Beat(name="approvals",
         narration=[
             "Connections lists the M C P servers. And the rule on the "
             "publish switch is worth reading: requests from chat or M C P "
             "always require approval, whatever this setting says.",
             "",
             "The approvals page is where they wait.",
         ],
         action=act_approvals, verify=lambda st: verify_path(st, "/approvals"),
         reset=reset_approvals),
    Beat(name="caveat",
         narration=[
             "One recorded caveat. The log tool returns the path of the log "
             "it read, and that path crosses the boundary. It is written in "
             "the commit that added the tool, not hidden in it.",
             "",
             "Next: five agents on one repository.",
         ],
         action=act_caveat, verify=v_any, reset=reset_terminal),
]


def main():
    ep = Episode("ep16_mcp", BEATS, out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        for warm in ("/", "/settings", "/connections", "/approvals"):
            goto(stage, warm, settle=2.0)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        print(f"\nEP16 COMPLETE: {ep.produce(stage)}")
    finally:
        kill_stage_terminal()
        stage.close()


if __name__ == "__main__":
    main()
