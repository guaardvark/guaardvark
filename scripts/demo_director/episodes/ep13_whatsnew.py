"""Episode 13 — The New Front Door (≈5:00).

The Workspaces top bar replaces the sidebar on camera, then a tour of what
landed since the first series: the shortcuts overlay, fresh renders in the
Media Library, MiniMax H3 with its compiled prompt, chat reasoning as its own
channel with inline artifacts, product profiles / Export Chats / Delete
History, the guaardvark REPL, and a closer on live GPU numbers.

GPU cast: Ollama (chat beats) + Audio Foundry (narration). ComfyUI may be
resident but nothing renders on camera. Shoot only when /api/gpu/status
reports the lock free.

Run from scripts/demo_director/:  venv/bin/python episodes/ep13_whatsnew.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage  # noqa: E402
from helpers import (  # noqa: E402
    REPO, api_get, close_dialogs, goto, kill_stage_terminal, open_workspace,
    require, set_nav_chrome, stage_terminal, tool_tab, type_line,
    verify_no_private_names, verify_path, workspace_button)

# The folder of renders the operator produced right before the shoot; the
# beat refuses to run if the Media Library has no such folder.
MEDIA_FOLDER = os.environ.get("EP13_MEDIA_FOLDER", "ImageBatch_09-04-2026_224306_027")
CHAT_QUESTION = os.environ.get(
    "EP13_CHAT_QUESTION",
    "Why would a local-first AI platform keep its model downloads behind an explicit Install button?")
CHAT_FILE_ASK = os.environ.get(
    "EP13_CHAT_FILE_ASK",
    "Make me a CSV file with two columns, workspace and tools, listing the eight workspaces "
    "Home, Chat, Studio, Library, Code, Work, Agents and System.")

CHAT_INPUT = "Type your message, paste an image, or use voice..."


def press(st: Stage, key: str, settle: float = 0.6):
    st.cursor._xdo("key", key)
    time.sleep(settle)


def mui_select(st: Stage, label: str):
    """The MUI Select whose FormControl label reads exactly `label` (these
    selects carry no accessible name, so role+name lookups miss them)."""
    return st.page.locator(".MuiFormControl-root").filter(
        has=st.page.locator(f"label:text-is('{label}')")).get_by_role("combobox").first


def native_select(st: Stage, label: str):
    return st.page.locator(".MuiFormControl-root").filter(
        has=st.page.locator(f"label:text-is('{label}')")).locator("select").first


# ------------------------------------------------------------- beat 0: flip

def reset_flip(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "sidebar", path="/settings")
    st.page.get_by_text("Navigation", exact=True).first.wait_for(
        state="visible", timeout=30_000)
    require(st.page.locator("a[aria-label='Dashboard']").count(),
            "sidebar chrome not rendered")


def act_flip(st: Stage):
    # A slow pass down the sidebar first: this is the last time it is seen.
    for label in ("Chat", "Video Gen", "Swarm", "System Map"):
        item = st.page.locator(f"a[aria-label='{label}']")
        if item.count():
            st.hover_over(item.first, dur=0.7)
            time.sleep(0.6)
    row = st.page.get_by_text("Navigation", exact=True).first
    st.hover_over(row, dur=0.9)
    time.sleep(1.0)
    toggle = st.page.locator("[aria-label='Navigation chrome']").get_by_role(
        "button", name="Workspaces")
    st.glide_click(toggle.first, dur=0.8)
    st.page.locator("nav[aria-label='Workspace']").wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.5)
    st.hover_over(workspace_button(st, "Studio"), dur=1.0)
    time.sleep(1.5)


def v_flip(st: Stage):
    require(st.page.locator("nav[aria-label='Workspace']").count(),
            "Workspaces bar did not appear")
    verify_path(st, "/settings")


# ------------------------------------------------------- beat 1: workspaces

def reset_workspaces(st: Stage):
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/dashboard")
    st.page.locator("nav[aria-label='Workspace']").wait_for(
        state="visible", timeout=30_000)


def act_workspaces(st: Stage):
    open_workspace(st, "Studio", "/video")
    time.sleep(1.2)
    strip = st.page.get_by_role("tablist", name="Workspace tools")
    strip.wait_for(state="visible", timeout=10_000)
    for label in ("Image Gen", "Audio Studio", "Film Crew", "Music Video"):
        st.hover_over(tool_tab(st, label), dur=0.5)
        time.sleep(0.5)
    open_workspace(st, "Library", "/documents")
    time.sleep(1.2)
    open_workspace(st, "Agents", "/agents")
    time.sleep(1.2)
    open_workspace(st, "System", "/settings")
    time.sleep(1.0)
    for aria in ("System Metrics", "Agent Screen"):
        st.hover_over(st.page.locator(f"button[aria-label='{aria}']"), dur=0.6)
        time.sleep(0.9)
    st.hover_over(st.page.locator("a[aria-label='Settings'][href='/settings']"),
                  dur=0.6)
    time.sleep(1.2)


def v_workspaces(st: Stage):
    verify_path(st, "/settings")


# -------------------------------------------------------- beat 2: shortcuts

def reset_shortcuts(st: Stage):
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/dashboard")
    time.sleep(0.5)


def act_shortcuts(st: Stage):
    st.cursor.glide(960, 600, dur=0.6)
    st.cursor.click()
    time.sleep(0.5)
    press(st, "question", settle=1.2)
    dlg = st.page.get_by_text("Keyboard shortcuts", exact=True).first
    dlg.wait_for(state="visible", timeout=10_000)
    for label in ("System Map", "Video Editor"):
        hit = st.page.locator("div[role='dialog']").get_by_text(label, exact=True)
        if hit.count():
            st.hover_over(hit.first, dur=0.8)
            time.sleep(1.4)
    time.sleep(1.0)
    st.glide_click(st.page.locator("button[aria-label='Close shortcuts']"), dur=0.7)
    time.sleep(0.6)


def v_shortcuts(st: Stage):
    verify_path(st, "/dashboard")


# ------------------------------------------------------------ beat 3: media

def media_card(st: Stage):
    return st.page.locator(".desktop-item-card").filter(
        has=st.page.get_by_text(MEDIA_FOLDER, exact=True))


def folder_window(st: Stage):
    return st.page.locator(".react-grid-item").filter(has_text=MEDIA_FOLDER)


def reset_media(st: Stage):
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/images")
    st.page.get_by_text(re.compile(r"\d+ folders?, \d+ images?")).first.wait_for(
        state="visible", timeout=60_000)
    time.sleep(0.8)
    # Folder windows persist across reloads; close any left open (off
    # camera) so the batch is back on the desktop as a card.
    for _ in range(6):
        closers = st.page.locator(".react-grid-item [data-testid='CloseIcon']")
        if not closers.count():
            break
        closers.first.locator("xpath=ancestor::button[1]").click(timeout=5_000)
        time.sleep(0.6)
    time.sleep(0.5)
    require(media_card(st).count(), f"Media Library has no folder {MEDIA_FOLDER!r}")


def act_media(st: Stage):
    st.glide_click(media_card(st).first, dur=0.9, double=True)
    folder_window(st).first.wait_for(state="visible", timeout=15_000)
    time.sleep(2.0)
    target = folder_window(st).first.locator("img")
    target.first.wait_for(state="visible", timeout=20_000)
    require(target.count(), "folder window shows no images")
    st.glide_click(target.nth(min(2, target.count() - 1)), dur=0.9, double=True)
    edit = st.page.locator("[aria-label='Edit image (E)'], [title='Edit image (E)']")
    edit.first.wait_for(state="visible", timeout=15_000)
    time.sleep(1.5)
    st.hover_over(edit.first, dur=0.6)
    for _ in range(4):
        press(st, "Right", settle=2.2)
    press(st, "Escape", settle=1.0)


def v_media(st: Stage):
    verify_path(st, "/images")


# --------------------------------------------------------------- beat 4: h3

def reset_h3(st: Stage):
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/video")
    mui_select(st, "Model").wait_for(state="visible", timeout=60_000)
    models = api_get("/api/batch-video/models")
    entries = models.get("models", models) if isinstance(models, dict) else models
    ready = {m.get("id"): m.get("is_ready") for m in entries if isinstance(m, dict)}
    require(ready.get("minimax-h3-int8"), "minimax-h3-int8 is not installed")


def act_h3(st: Stage):
    st.glide_click(mui_select(st, "Model"), dur=0.8)
    time.sleep(1.0)
    st.glide_click(st.page.get_by_role("option", name=re.compile(
        r"MiniMax H3 Int8 \(16GB\)")).first, dur=0.8)
    time.sleep(1.5)
    preset = mui_select(st, "Prompt preset")
    preset.wait_for(state="visible", timeout=20_000)
    st.glide_click(preset, dur=0.8)
    time.sleep(1.0)
    st.glide_click(st.page.get_by_role("option", name=re.compile(
        "Two-line dialogue scene")).first, dur=0.8)
    time.sleep(1.8)
    eff = st.page.get_by_text("Effective settings", exact=True)
    if eff.count():
        st.hover_over(eff.first, dur=0.8)
        time.sleep(0.8)
        audio = st.page.get_by_text("native audio", exact=True)
        if audio.count():
            st.hover_over(audio.first, dur=0.6)
            time.sleep(1.2)
    preview = st.page.get_by_role("button", name=re.compile("Preview enhanced prompt"))
    st.glide_click(preview.first, dur=0.8)
    st.page.get_by_label("Enhanced prompt (what will be sent to the model)").wait_for(
        state="visible", timeout=90_000)
    time.sleep(1.0)
    st.hover_over(st.page.get_by_label(
        "Enhanced prompt (what will be sent to the model)"), dur=0.9)
    time.sleep(4.0)


def v_h3(st: Stage):
    require(st.page.get_by_label(
        "Enhanced prompt (what will be sent to the model)").count(),
        "compiled prompt never appeared")
    verify_path(st, "/video")


# ---------------------------------------------------------- beat 5: honesty

def reset_honesty(st: Stage):
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/audio")
    # Scope to the page: the workspace strip also has a "Music Video" tab.
    music_tab = st.page.locator("[data-main-content]").get_by_role(
        "tab", name="Music", exact=True)
    music_tab.first.wait_for(state="visible", timeout=60_000)
    music_tab.first.click(timeout=10_000)
    native_select(st, "Music model").wait_for(state="visible", timeout=60_000)


def act_honesty(st: Stage):
    sel = native_select(st, "Music model")
    st.hover_over(sel, dur=0.9)
    time.sleep(0.8)
    st.glide_click(sel, dur=0.5)
    time.sleep(2.5)
    press(st, "Escape", settle=1.0)
    lyrics = st.page.get_by_text("Instrumental only")
    if lyrics.count():
        st.hover_over(lyrics.first, dur=0.7)
        time.sleep(1.5)


def v_honesty(st: Stage):
    verify_path(st, "/audio")


# -------------------------------------------------------- beat 6: reasoning

def chat_box(st: Stage):
    return st.page.get_by_placeholder(CHAT_INPUT).first


def new_chat(st: Stage):
    btn = st.page.locator("button[aria-label='Start a new chat session']")
    if btn.count():
        btn.first.click(timeout=10_000)
        time.sleep(1.5)


def reset_reasoning(st: Stage):
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/chat")
    chat_box(st).wait_for(state="visible", timeout=60_000)
    new_chat(st)
    ps = api_get("/api/plugins")
    plugins = ps.get("plugins", ps) if isinstance(ps, dict) else ps
    if isinstance(plugins, dict):
        plugins = list(plugins.values())
    ollama = [p for p in plugins if isinstance(p, dict) and p.get("id") == "ollama"]
    require(ollama and ollama[0].get("status") == "running", "Ollama is not running")


def act_reasoning(st: Stage):
    st.glide_click(chat_box(st), dur=0.8)
    st.cursor.type_text(CHAT_QUESTION, delay_ms=22)
    time.sleep(0.5)
    press(st, "Return", settle=0.5)
    card = st.page.locator("[data-testid='thinking-card']").last
    card.wait_for(state="visible", timeout=120_000)
    st.hover_over(card, dur=0.9)
    # Watch the reasoning stream, then the answer; the narration covers a
    # real think of 20–60 s.
    body = st.page.locator("[data-testid='thinking-body']").last
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        if st.page.locator("[data-testid='thinking-spinner']").count() == 0:
            break
        time.sleep(0.5)
    time.sleep(2.0)
    if body.count() and not body.first.is_visible():
        st.glide_click(card, dur=0.7)
        time.sleep(2.5)


def v_reasoning(st: Stage):
    require(st.page.locator("[data-testid='thinking-card']").count(),
            "no thinking card rendered")
    verify_path(st, "/chat")


# --------------------------------------------------------- beat 7: artifact

def reset_artifact(st: Stage):
    close_dialogs(st)
    if "/chat" not in st.path():
        set_nav_chrome(st, "software", path="/chat")
    chat_box(st).wait_for(state="visible", timeout=60_000)
    new_chat(st)
    ps = api_get("/api/plugins")
    plugins = ps.get("plugins", ps) if isinstance(ps, dict) else ps
    if isinstance(plugins, dict):
        plugins = list(plugins.values())
    ollama = [p for p in plugins if isinstance(p, dict) and p.get("id") == "ollama"]
    require(ollama and ollama[0].get("status") == "running", "Ollama is not running")


def act_artifact(st: Stage):
    st.glide_click(chat_box(st), dur=0.8)
    st.cursor.type_text(CHAT_FILE_ASK, delay_ms=20)
    press(st, "Return", settle=0.5)
    card = st.page.locator("[data-testid='artifact-card']").last
    card.wait_for(state="visible", timeout=150_000)
    st.hover_over(card, dur=0.9)
    time.sleep(1.5)
    body = st.page.locator("[data-testid='artifact-body']").last
    if body.count():
        st.hover_over(body, dur=0.8)
    for aria in ("Copy content", "Download"):
        b = card.locator(f"button[aria-label='{aria}']")
        if b.count():
            st.hover_over(b.first, dur=0.5)
            time.sleep(0.8)
    time.sleep(1.0)


def v_artifact(st: Stage):
    require(st.page.locator("[data-testid='artifact-card']").count(),
            "no artifact card rendered")
    verify_path(st, "/chat")


# ----------------------------------------------------- beat 8: housekeeping

def reset_housekeeping(st: Stage):
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/settings")
    st.page.get_by_text("Product Profile", exact=True).first.wait_for(
        state="visible", timeout=60_000)


def act_housekeeping(st: Stage):
    st.hover_over(st.page.get_by_text("Product Profile", exact=True).first, dur=0.9)
    time.sleep(0.8)
    profile = st.page.get_by_role("combobox", name="Profile")
    if profile.count():
        st.glide_click(profile.first, dur=0.7)
        time.sleep(2.2)
        press(st, "Escape", settle=0.8)
    export = st.page.get_by_role("button", name="Export chats")
    st.glide_click(export.first, dur=0.9)
    st.page.get_by_text(re.compile(r"Exported \d+ chats")).first.wait_for(
        state="visible", timeout=60_000)
    time.sleep(2.0)
    delete = st.page.get_by_role("button", name="Delete History")
    st.hover_over(delete.first, dur=0.9)
    time.sleep(2.0)


def v_housekeeping(st: Stage):
    verify_path(st, "/settings")


# -------------------------------------------------------------- beat 9: cli

def reset_cli(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/dashboard")
    time.sleep(0.5)


# The REPL status line prints the working directory's basename; run it
# from a directory named for the product rather than the checkout.
CLI_CWD = REPO / "docs" / "local-workspace-only" / "guaardvark"


def act_cli(st: Stage):
    CLI_CWD.mkdir(parents=True, exist_ok=True)
    stage_terminal("guaardvark; sleep 20", cwd=CLI_CWD)
    time.sleep(7.0)
    st.cursor.glide(960, 540, dur=0.8)
    st.cursor.click()
    time.sleep(0.8)
    type_line(st, "/help gpu", settle=3.0)
    type_line(st, "gpu status", settle=3.5)
    type_line(st, "plugins list", settle=4.0)
    type_line(st, "/quit", settle=1.5)


def v_cli(st: Stage):
    verify_no_private_names(st)


# ----------------------------------------------------------- beat 10: closer

def reset_closer(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/dashboard")
    st.page.locator("button[aria-label='System Metrics']").wait_for(
        state="visible", timeout=30_000)


def act_closer(st: Stage):
    st.glide_click(st.page.locator("button[aria-label='System Metrics']"), dur=0.9)
    time.sleep(2.0)
    gpu = st.page.locator("div[role='dialog']").get_by_text("GPU", exact=True)
    if gpu.count():
        st.hover_over(gpu.first, dur=0.9)
    time.sleep(3.0)


def v_closer(st: Stage):
    verify_no_private_names(st)


BEATS = [
    Beat(
        name="flip",
        narration=[
            "Thirty-four pages. Four groups. One sidebar. That was the front "
            "door for twelve episodes.",
            "",
            "It is still here. But now it is a setting.",
            "Eight workspaces. One row of tools. Same pages, same routes, and "
            "the sidebar is one click away if you miss it.",
        ],
        action=act_flip, verify=v_flip, reset=reset_flip,
    ),
    Beat(
        name="workspaces",
        narration=[
            "Studio holds ten tools. Library, your files and media. Agents, "
            "everything that acts on its own. System, everything that "
            "watches it.",
            "",
            "One catalog drives both looks. A distribution ships its own "
            "catalog, and the bar follows it.",
            "Three things stay pinned on the right: live system metrics, the "
            "agent's screen, and settings.",
        ],
        action=act_workspaces, verify=v_workspaces, reset=reset_workspaces,
    ),
    Beat(
        name="shortcuts",
        narration=[
            "Press the question mark anywhere, and the keyboard shortcuts "
            "come up. Chat, the system map, the video editor. One overlay.",
        ],
        action=act_shortcuts, verify=v_shortcuts, reset=reset_shortcuts,
        min_hold=9.0,
    ),
    Beat(
        name="media",
        narration=[
            "The Media Library is a desktop. Every batch is a folder. Every "
            "folder is a window.",
            "",
            "These were rendered on this card an hour ago. Open one, and the "
            "arrow keys page through the batch.",
            "Nothing was uploaded to see them. Nothing was downloaded to "
            "keep them.",
        ],
        action=act_media, verify=v_media, reset=reset_media,
    ),
    Beat(
        name="h3",
        narration=[
            "The video generator learned a new model. MiniMax H3 makes clips "
            "that talk: the dialogue and the soundtrack come out of the same "
            "pass as the picture.",
            "",
            "Pick a preset, and the effective settings tell you what you "
            "really get: native audio, one hundred twenty four frames, "
            "twenty four a second.",
            "Then preview the prompt. Guaardvark compiles your idea into the "
            "model's own format: numbered shots, cut times that add up to the "
            "clip, and a speaker id for every line.",
            "",
            "Measured here, on a sixteen gigabyte card: a five second clip in "
            "one hundred eighty six seconds on the eight step profile. Peak "
            "memory, fourteen and a half gigabytes.",
        ],
        action=act_h3, verify=v_h3, reset=reset_h3,
    ),
    Beat(
        name="honesty",
        narration=[
            "Two honest notes.",
            "The audio studio lists MiniMax Music 3, a model that sings "
            "lyrics. On this machine it is not installed, and the menu says "
            "so instead of pretending.",
            "",
            "And the L T X video models can decode a soundtrack they already "
            "sample. That switch ships off, because nobody here has listened "
            "to the result yet. A knob that might sound bad does not ship.",
        ],
        action=act_honesty, verify=v_honesty, reset=reset_honesty,
    ),
    Beat(
        name="reasoning",
        narration=[
            "Chat changed underneath too.",
            "Thinking models reason before they answer. That reasoning now "
            "streams as its own channel, in its own card, and folds away "
            "when the answer starts.",
            "",
            "It used to leak. When a model ran out of answer, its chain of "
            "thought became the reply, and got saved as one. Now the two "
            "are never confused.",
            "",
            "And the chat model runs with a context window sized from a "
            "measurement, not a guess. It had been running with four "
            "thousand tokens on a sixteen gigabyte card.",
        ],
        action=act_reasoning, verify=v_reasoning, reset=reset_reasoning,
    ),
    Beat(
        name="artifact",
        narration=[
            "Ask for a file, and the file lands in the conversation. A "
            "table you can read, a download you can keep, saved under your "
            "outputs.",
            "",
            "This one did not work the first time we shot it: the router "
            "read the word agents in the request and sent it to the screen "
            "agent. That is fixed, and this is the fixed path.",
        ],
        action=act_artifact, verify=v_artifact, reset=reset_artifact,
        min_hold=12.0,
    ),
    Beat(
        name="housekeeping",
        narration=[
            "Three small things people asked for.",
            "A product profile. Creator is the media studio with the agents "
            "and the knowledge index out of the way. Workstation is "
            "everything. Nothing is removed, only unlisted.",
            "",
            "Export chats writes every conversation to disk as J S O N and "
            "as markdown.",
            "And delete history clears every generated image, video and "
            "audio file, with an audit row for each purge. Film crew "
            "productions and chats are not touched.",
        ],
        action=act_housekeeping, verify=v_housekeeping, reset=reset_housekeeping,
    ),
    Beat(
        name="cli",
        narration=[
            "The command line grew up. Guaardvark in a shell is now a peer "
            "of the web app, not a subset.",
            "One command catalog feeds the slash router, tab completion and "
            "help. Plugins, G P U, M C P, audio, swarm and lessons all have "
            "commands.",
            "",
            "Ask it what is on the card, and it tells you who holds the lock.",
        ],
        action=act_cli, verify=v_cli, reset=reset_cli,
    ),
    Beat(
        name="closer",
        narration=[
            "Those are the live numbers for the card everything you just "
            "saw ran on.",
            "",
            "One machine. No cloud.",
            "",
            "Next: the system map, a chart of every module in this product. "
            "Then Guaardvark writing code, plugging into other tools, and "
            "running a swarm.",
        ],
        action=act_closer, verify=v_closer, reset=reset_closer,
    ),
]


def main():
    ep = Episode("ep13_whatsnew", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        goto(stage, "/", settle=2.0)
        for warm in ("/settings", "/dashboard", "/images", "/video", "/audio",
                     "/chat", "/agents"):
            goto(stage, warm, settle=2.0)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP13 COMPLETE: {final}")
    finally:
        kill_stage_terminal()
        stage.close()


if __name__ == "__main__":
    main()
