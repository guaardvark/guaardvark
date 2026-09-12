"""Episode 18 — Your Agent, Your Studio (≈4:30; 90 s social cut). DRAFT — dry-run before shooting.

Two lines install the plugin in Claude Code; one sentence asks for a music video; the
agent runs the skill: preflight, queue, the approval gate, the swap on the GPU, the
finished file, one honest caveat.

GPU cast: ComfyUI (wan22-5b image-to-video) + Audio Foundry off camera (the song is
pre-produced in an asset session). Requires: `python -m backend.mcp doctor` all PASS;
Claude Code logged in on the stage display; a 20–30 s song document in the library
(EP18_SONG_DOC_ID); backend restarted with private extensions parked; the plugin
installable from GitHub (`claude plugin marketplace add guaardvark/guaardvark`).

Numbers are read, not typed: skill count from .agents/skills, tool counts from
`python -m backend.mcp list-tools`, the card from inspect_gpu.

Run from scripts/demo_director/:  venv/bin/python episodes/ep18_agent.py
Dry-run:                          venv/bin/python dryrun.py episodes/ep18_agent.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage  # noqa: E402
from helpers import (  # noqa: E402
    REPO, api_get, close_dialogs, focus_window, goto, kill_stage_terminal, require,
    set_nav_chrome, stage_claude, stage_terminal, type_into_stage_terminal,
    verify_no_private_names, verify_path)

PLUGIN_TOOLS = "mcp__plugin_guaardvark_guaardvark__*,Skill"

PY = "backend/venv/bin/python"
SONG_DOC_ID = os.environ.get("EP18_SONG_DOC_ID", "")
STYLE = "grainy 16 millimetre, neon rain on wet asphalt, slow dolly, 1984"

# ---- numbers, read at load ---------------------------------------------------
SKILLS = sorted(p.name for p in (REPO / ".agents" / "skills").iterdir()
                if (p / "SKILL.md").is_file() and not p.name.startswith(("_", ".")))
_lt = subprocess.run([PY, "-m", "backend.mcp", "list-tools"], cwd=REPO,
                     capture_output=True, text=True, timeout=300)
_m = re.search(r"exposing (\d+) of (\d+) registered", _lt.stderr + _lt.stdout)
EXPOSED, REGISTERED = (int(_m.group(1)), int(_m.group(2))) if _m else (0, 0)
require(len(SKILLS) == 15, f"expected 15 skills, found {len(SKILLS)}: {SKILLS}")
require(EXPOSED > 0, "list-tools did not report the exposed count")

WORDS = {15: "fifteen", 46: "forty-six", 47: "forty-seven", 90: "ninety", 91: "ninety-one"}
def say(n: int) -> str:
    return WORDS.get(n, str(n))


# ---- resets / actions -------------------------------------------------------
def reset_terminal(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/dashboard")
    time.sleep(0.5)


def reset_install(st: Stage):
    reset_terminal(st)
    # A clean plugin state so the install is real on camera.
    subprocess.run(["claude", "plugin", "uninstall", "guaardvark@guaardvark"],
                   capture_output=True, text=True)
    subprocess.run(["claude", "plugin", "marketplace", "remove", "guaardvark"],
                   capture_output=True, text=True)


def act_install(st: Stage):
    stage_terminal(
        "claude plugin marketplace add guaardvark/guaardvark && "
        "claude plugin install guaardvark@guaardvark; sleep 30")
    time.sleep(16.0)


def v_installed(st: Stage):
    r = subprocess.run(["claude", "plugin", "list"], capture_output=True, text=True)
    require("guaardvark@guaardvark" in r.stdout, "plugin not listed after install")
    verify_no_private_names(st)


def reset_ask(st: Stage):
    reset_terminal(st)
    require(SONG_DOC_ID, "EP18_SONG_DOC_ID is not set (a 20–30 s song in the library)")
    ok = api_get("/api/health")
    require(ok.get("status") == "ok", "backend not healthy")
    plugins = api_get("/api/plugins/status").get("status", {})
    require(plugins.get("comfyui") == "running", "ComfyUI plugin must be running")


def act_ask(st: Stage):
    # One interactive session, kept alive across the ask, gate and file beats,
    # so the skill loading, the streamed tool calls and the permission prompt
    # are all on camera. The agent must stop at the gate; it never approves.
    stage_claude(PLUGIN_TOOLS, boot=8.0)
    type_into_stage_terminal(
        f"Make a music video from song document {SONG_DOC_ID} in this style: {STYLE}. "
        "Follow the guaardvark skills. Stop at the approval gate and tell me the cost "
        "before you ask me to approve.", delay_ms=40)
    time.sleep(75.0)


def reset_studio(st: Stage):
    # Keep the Claude session alive; bring the Studio window forward.
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/music-video")
    focus_window("Chromium|Guaardvark")
    time.sleep(1.5)


def act_studio(st: Stage):
    # The newest project's cut plan appears in the Studio; hover the first cuts.
    row = st.page.get_by_text(re.compile("cut", re.I)).first
    if row.count():
        st.hover_over(row, dur=0.9)
    time.sleep(6.0)


def reset_keep_session(st: Stage):
    # The interactive session from the ask beat stays up.
    close_dialogs(st)


def act_gate(st: Stage):
    # The user answers the agent in the same session: yes. The approve route is
    # a curl the skill names, so Claude Code asks permission for it on camera;
    # the "y" is typed live. Then the GPU HUD shows the swap.
    type_into_stage_terminal("Yes, approve it.", delay_ms=50)
    time.sleep(12.0)
    type_into_stage_terminal("y", delay_ms=120, settle=25.0)   # the permission prompt
    focus_window("Chromium|Guaardvark")
    goto(st, "/dashboard", settle=2.0)
    time.sleep(8.0)


def act_file(st: Stage):
    type_into_stage_terminal(
        "Poll until the music video is finished and give me the file.", delay_ms=40)
    time.sleep(60.0)
    focus_window("Chromium|Guaardvark")
    goto(st, "/media", settle=2.5)
    time.sleep(6.0)


def act_caveat(st: Stage):
    # The honesty beat, same session: a clamp or a refusal read back verbatim.
    type_into_stage_terminal(
        "Generate a 512x512 image of a paper boat with generate_image at 4 steps, then "
        "poll it and tell me verbatim what the server changed or refused.", delay_ms=40)
    time.sleep(50.0)


def v_any(st: Stage):
    verify_no_private_names(st)


BEATS = [
    Beat(name="install",
         narration=[
             "Two lines. No clone.",
             "",
             f"The marketplace is the repository itself. The install brings {say(len(SKILLS))} "
             "skills and the M C P server, and asks where your checkout lives.",
         ],
         action=act_install, verify=v_installed, reset=reset_install),
    Beat(name="ask",
         narration=[
             "One sentence. A song, a style, and the word skills.",
             "",
             "The agent names the skill it is following, checks the backend, the plugins and "
             "the models, and asks the card what it is.",
             f"{say(EXPOSED)} tools are exposed of {say(REGISTERED)} registered. The rest stay "
             "behind a policy that says no by default.",
         ],
         action=act_ask, verify=v_any, reset=reset_ask),
    Beat(name="studio",
         narration=[
             "Queued is not done. The Director has analysed the song, cut it on the beat, and "
             "written one prompt per cut. Nothing has touched the G P U yet.",
         ],
         action=act_studio, verify=lambda st: verify_path(st, "/music-video"),
         reset=reset_studio),
    Beat(name="gate",
         narration=[
             "The gate. The agent says how many cuts, how many seconds each, which model, "
             "and how long. Then it asks.",
             "",
             "Yes. The approve route fires. Watch the card: the chat model leaves, the video "
             "model arrives. One heavy job at a time.",
             "One machine. No cloud.",
         ],
         action=act_gate, verify=v_any, reset=reset_keep_session),
    Beat(name="file",
         narration=[
             "The agent polls by batch i d until the status route says completed, and hands "
             "back a file, not a promise.",
             "It is in the media library, on this disk.",
         ],
         action=act_file, verify=lambda st: verify_path(st, "/media"), reset=reset_keep_session),
    Beat(name="caveat",
         narration=[
             "One honest beat. Four steps is below what this model needs. The server raises "
             "it, says so, and the agent reads that back to you word for word.",
             "",
             f"{say(len(SKILLS))} skills. Your G P U. Your agent.",
         ],
         action=act_caveat, verify=v_any, reset=reset_keep_session),
]


def main():
    ep = Episode("ep18_agent", BEATS, out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        for warm in ("/", "/music-video", "/media", "/dashboard"):
            goto(stage, warm, settle=2.0)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        print(f"\nEP18 COMPLETE: {ep.produce(stage)}")
    finally:
        kill_stage_terminal()


if __name__ == "__main__":
    main()
