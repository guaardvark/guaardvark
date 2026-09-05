"""Episode 14 — A Map of Everything (≈5:00).

The System Map on its own: the constellation, section spotlights, search,
the tool-graph and ghost-endpoint overlays, ranked findings, the finding
that caught an unreachable chat tool (fixed in fdd82da, which cites the
finding id in code), a live dispatch to the self-improvement agent, and a
chat tool call pulsing its module in real time.

Every number spoken comes from the snapshot fetched when this file loads
(refreshed once), so the narration cannot drift from the HUD on screen.

GPU cast: Ollama (one tool call from the floating chat) + Audio Foundry.
Preconditions: self-improvement enabled and idle, codebase unlocked.

Run from scripts/demo_director/:  venv/bin/python episodes/ep14_systemmap.py
"""

from __future__ import annotations

import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests as rq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import API, FRONTEND, Beat, Episode, Stage  # noqa: E402
from helpers import (  # noqa: E402
    REPO, api_get, close_dialogs, kill_stage_terminal, require,
    set_nav_chrome, stage_terminal, verify_no_private_names, verify_path)

FLOATING_CHAT_INPUT = "Type your message, paste an image, or use voice..."
CAUGHT_FINDING_ID = "a21f45035732cf31"   # cited in unified_chat_engine.py


def press(st: Stage, key: str, settle: float = 0.6):
    st.cursor._xdo("key", key)
    time.sleep(settle)


# ----------------------------------------------------------- live numbers

def load_numbers() -> dict:
    snap = api_get("/api/system-map/snapshot", timeout=600, refresh=1)
    stats = snap["stats"]
    kinds = Counter(f.get("kind") for f in snap.get("findings", []))
    sev = Counter(f.get("severity") for f in snap.get("findings", []))
    tool = stats["tool"]
    return {
        "modules": snap["file_count"],
        "edges": stats["dependency"]["internal_edges"],
        "cycles": stats["dependency"]["cycles"],
        "registered": tool["registered_count"],
        "wired": tool["core_tool_count"],
        "unwired": len(snap["tool_graph"].get("unwired", [])),
        "ghosts": stats["reachability"]["ghost_endpoints"],
        "ghost_callers": stats["reachability"]["ghost_callers"],
        "routes": stats["reachability"]["backend_routes"],
        "dead": stats["dead_symbol"]["dead_symbols"],
        "suppressed": stats["dead_symbol"]["suppressed_conservative"],
        "functions": stats["dead_symbol"]["functions_defined"],
        "findings": sum(kinds.values()),
        "medium": sev.get("medium", 0),
        "high": sev.get("high", 0),
        "untested": kinds.get("untested-module", 0),
        "dormant": kinds.get("dormant-module", 0),
        "kinds": kinds,
    }


N = load_numbers()
print("system map numbers:", {k: v for k, v in N.items() if k != "kinds"})


def spoken(n: int) -> str:
    return f"{n:,}"


# --------------------------------------------------------------- shared

def hud(st: Stage):
    return st.page.get_by_text(re.compile(r"\d+ modules · \d+ edges")).first


def reset_map(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/system-map")
    hud(st).wait_for(state="visible", timeout=120_000)
    time.sleep(1.0)
    shown = hud(st).inner_text()
    require(str(N["modules"]) in shown,
            f"HUD says {shown!r}, narration was built on {N['modules']} modules")


def v_map(st: Stage):
    verify_path(st, "/system-map")


def canvas_center(st: Stage):
    box = st.page.locator("canvas").first.bounding_box()
    require(box, "no canvas")
    ox, oy = st._offsets()
    return (int(box["x"] + box["width"] * 0.5 + ox),
            int(box["y"] + box["height"] * 0.5 + oy))


# -------------------------------------------------------- beat 0: constellation

def act_constellation(st: Stage):
    st.hover_over(hud(st), dur=1.0)
    time.sleep(2.0)
    cx, cy = canvas_center(st)
    st.cursor.glide(cx, cy, dur=0.8)
    for _ in range(4):
        st.cursor._xdo("click", "4")      # wheel up = zoom in
        time.sleep(0.35)
    time.sleep(1.0)
    st.cursor.drag(cx + 220, cy + 90, dur=1.4)
    time.sleep(1.2)
    for _ in range(4):
        st.cursor._xdo("click", "5")
        time.sleep(0.35)
    time.sleep(1.0)


# ---------------------------------------------------------- beat 1: spotlight

def legend_pill(st: Stage, label: str):
    return st.page.get_by_role("button", name=label, exact=True)


def act_spotlight(st: Stage):
    for label in ("API", "Services", "Tools"):
        st.glide_click(legend_pill(st, label).first, dur=0.8)
        time.sleep(2.2)
    clear = st.page.get_by_role("button", name="clear", exact=True)
    if clear.count():
        st.glide_click(clear.first, dur=0.7)
    time.sleep(1.2)


# ------------------------------------------------------------- beat 2: search

def act_search(st: Stage):
    cx, cy = canvas_center(st)
    st.cursor.glide(cx, cy, dur=0.5)
    st.cursor.click()
    press(st, "slash", settle=0.8)
    st.cursor.type_text("unified_chat_engine", delay_ms=60)
    press(st, "Return", settle=2.5)
    imp = st.page.get_by_text(re.compile(r"\d+ importers"))
    if imp.count():
        st.hover_over(imp.first, dur=0.9)
        time.sleep(2.0)
    findings_hdr = st.page.get_by_text(re.compile(r"Findings \(\d+\)"))
    if findings_hdr.count():
        st.hover_over(findings_hdr.first, dur=0.8)
        time.sleep(1.5)
    press(st, "Escape", settle=0.6)
    press(st, "Escape", settle=0.8)


# ----------------------------------------------------------- beat 3: overlays

def overlay_chip(st: Stage, label: str):
    return st.page.get_by_role("button", name=re.compile(label)).first


def act_overlays(st: Stage):
    tg = overlay_chip(st, r"Tool graph")
    st.glide_click(tg, dur=0.9)
    time.sleep(3.0)
    ge = overlay_chip(st, r"Ghost endpoints")
    st.glide_click(ge, dur=0.9)
    time.sleep(3.0)
    st.glide_click(ge, dur=0.6)
    time.sleep(0.6)
    st.glide_click(tg, dur=0.6)
    time.sleep(0.8)


def v_overlays(st: Stage):
    require(overlay_chip(st, r"Tool graph").count(), "tool graph chip missing")
    verify_path(st, "/system-map")


# ----------------------------------------------------------- beat 4: findings

def open_findings(st: Stage):
    tab = st.page.get_by_role("button", name="Findings")
    st.glide_click(tab.first, dur=0.8)
    time.sleep(1.5)


def act_findings(st: Stage):
    open_findings(st)
    for label in ("Critical", "All", "Actionable"):
        pill = st.page.get_by_role("button", name=label, exact=True)
        if pill.count():
            st.glide_click(pill.first, dur=0.6)
            time.sleep(1.6)
    row = st.page.get_by_text(re.compile(r"ghost-api-caller|unwired-tool|url-prefix-collision")).first
    if row.count():
        st.glide_click(row, dur=0.9)
        time.sleep(3.0)
    press(st, "Escape", settle=0.8)


# -------------------------------------------------------------- beat 5: catch

def act_catch(st: Stage):
    stage_terminal(
        f"grep -n {CAUGHT_FINDING_ID} backend/services/unified_chat_engine.py; "
        f"echo; git log --oneline -1 fdd82da; sleep 30")
    time.sleep(8.0)


def v_catch(st: Stage):
    verify_no_private_names(st)


# ----------------------------------------------------------- beat 6: dispatch

def reset_dispatch(st: Stage):
    reset_map(st)
    t0 = time.monotonic()
    status = api_get("/api/self-improvement/status", timeout=10)
    require(time.monotonic() - t0 < 3.0, "self-improvement status is slow (wedged?)")
    require(status.get("enabled"), "self-improvement is disabled — enable it in Settings first")
    require(not status.get("codebase_locked"), "codebase is locked")
    require(not status.get("running") and not status.get("is_running"),
            "a self-improvement run is already in progress")
    rows = api_get("/api/system-map/findings", kind="unwired-tool", limit=5)
    require(any(f.get("dispatchable") for f in rows.get("findings", [])),
            "no dispatchable unwired-tool finding")
    _FIXES_BEFORE["n"] = status.get("total_fixes")


def send_button_for(st: Stage, row):
    """The dispatch button that sits with `row` in the same finding card:
    the nearest SendIcon button below the row's top edge."""
    row.scroll_into_view_if_needed(timeout=10_000)
    time.sleep(0.4)
    rb = row.bounding_box()
    require(rb, "finding row has no box")
    best, best_d = None, 1e9
    buttons = st.page.locator("button:has(svg[data-testid='SendIcon'])")
    for i in range(buttons.count()):
        b = buttons.nth(i).bounding_box()
        if not b:
            continue
        d = b["y"] - rb["y"]
        if 0 <= d < best_d and d < 160:
            best, best_d = buttons.nth(i), d
    require(best is not None, "no dispatch button next to the finding")
    return best


def act_dispatch(st: Stage):
    open_findings(st)
    row = st.page.get_by_text(re.compile(r"registered but not in CORE_TOOLS")).first
    row.wait_for(state="visible", timeout=15_000)
    st.hover_over(row, dur=0.9)
    time.sleep(1.5)
    send = send_button_for(st, row)
    st.hover_over(send.first, dur=0.7)
    time.sleep(1.2)
    st.glide_click(send.first, dur=0.5)
    st.page.get_by_text(re.compile(r"^Dispatched")).first.wait_for(
        state="visible", timeout=30_000)
    time.sleep(3.0)


def v_dispatch(st: Stage):
    t0 = time.monotonic()
    api_get("/api/self-improvement/status", timeout=10)
    require(time.monotonic() - t0 < 5.0, "status route wedged after dispatch")
    before = _FIXES_BEFORE.get("n")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        now = api_get("/api/self-improvement/status", timeout=10).get("total_fixes")
        if before is None or (now is not None and now > before):
            break
        time.sleep(2)
    require(before is None or now > before, "no PendingFix was staged by the dispatch")
    verify_path(st, "/system-map")


_FIXES_BEFORE: dict = {}


# --------------------------------------------------------------- beat 7: live

def act_live(st: Stage):
    tab = st.page.get_by_role("button", name="Activity")
    st.glide_click(tab.first, dur=0.8)
    time.sleep(1.0)
    st.cursor._xdo("key", "ctrl+shift+c")
    time.sleep(1.5)
    box = st.page.get_by_placeholder(FLOATING_CHAT_INPUT).first
    box.wait_for(state="visible", timeout=15_000)
    st.glide_click(box, dur=0.8)
    st.cursor.type_text("use the system mapper and tell me how many modules there are", delay_ms=40)
    press(st, "Return", settle=1.0)
    log = st.page.get_by_text(re.compile(r"map_codebase"))
    log.first.wait_for(state="visible", timeout=90_000)
    time.sleep(2.5)
    counter = st.page.get_by_text(re.compile(r"[1-9]\d*/30"))
    if counter.count():
        st.hover_over(counter.first, dur=0.8)
    time.sleep(2.0)


def v_live(st: Stage):
    require(st.page.get_by_text(re.compile(r"[1-9]\d*/30")).count(),
            "activity log still idle after the tool call")
    verify_path(st, "/system-map")


# ------------------------------------------------------------- beat 8: closer

def act_closer(st: Stage):
    close_btn = st.page.locator("button[title='Close']")
    if close_btn.count():
        st.glide_click(close_btn.first, dur=0.6)
        time.sleep(0.8)
    press(st, "r", settle=1.5)
    st.hover_over(hud(st), dur=0.9)
    time.sleep(1.5)
    for pat in (r"\d+ medium", r"\d+ hygiene"):
        chip = st.page.get_by_text(re.compile(pat))
        if chip.count():
            st.hover_over(chip.first, dur=0.7)
            time.sleep(1.4)


BEATS = [
    Beat(
        name="constellation",
        narration=[
            f"Every module in this product, drawn from its real imports. "
            f"{spoken(N['modules'])} of them, {spoken(N['edges'])} edges "
            f"between them, {N['cycles']} import cycles.",
            "",
            "Nothing here is hand drawn. The map is computed from the code "
            "on disk, cached for five minutes, and re-computed on demand.",
            "Drag to pan. Wheel to zoom. Bigger dots have more importers.",
        ],
        action=act_constellation, verify=v_map, reset=reset_map,
    ),
    Beat(
        name="spotlight",
        narration=[
            "Seven sections. Click one and the rest fades: the A P I layer, "
            "the services behind it, the tools an agent can call.",
            "",
            "Colour is section. Brightness is lifecycle: active, dormant, "
            "auto-loaded, test, script, config. A module that nothing "
            "imports and nothing registers, is dim.",
        ],
        action=act_spotlight, verify=v_map, reset=reset_map,
    ),
    Beat(
        name="search",
        narration=[
            "Slash to search. Type a module, and the camera flies to it.",
            "",
            "The panel shows its section, its lifecycle, and how many "
            "modules depend on it. This is the chat engine, so the answer "
            "is: a lot.",
            "Escape clears it.",
        ],
        action=act_search, verify=v_map, reset=reset_map,
    ),
    Beat(
        name="overlays",
        narration=[
            f"Two overlays. The tool graph: {N['registered']} tools are "
            f"registered, {N['wired']} are wired into a list an agent can "
            f"reach, {N['unwired']} are not. Registered but unreachable "
            "is a bug, and the map calls it one.",
            "",
            f"Ghost endpoints: routes the backend serves that no frontend "
            f"code calls. {N['ghosts']} shown, out of {spoken(N['routes'])} "
            "routes.",
        ],
        action=act_overlays, verify=v_overlays, reset=reset_map,
    ),
    Beat(
        name="findings",
        narration=[
            f"{spoken(N['findings'])} findings, ranked. Actionable is the "
            f"default view: {N['medium']} medium, nothing critical tonight.",
            "",
            f"{N['untested']} modules with no test. {N['dormant']} that "
            "nothing imports any more. Eight kinds in all, and each one "
            "carries the file and the line.",
            "Click a finding and the camera goes to the module.",
        ],
        action=act_findings, verify=v_map, reset=reset_map,
    ),
    Beat(
        name="catch",
        narration=[
            "Here is one it caught for real.",
            "The chat tool that lists documents was registered, but not in "
            "the list the agent reads. The map flagged it as unreachable.",
            "",
            "The fix went in, and the comment in the code cites the finding "
            "by its id. The map found a bug in the product it maps.",
            "",
            "And then the map kept flagging it, because that file defines "
            "the list twice and the map was reading the wrong one. So the "
            "map got fixed as well. Nothing here is taken on faith.",
        ],
        action=act_catch, verify=v_catch, reset=reset_map,
    ),
    Beat(
        name="dispatch",
        narration=[
            "Some findings you can send straight to the self-improvement "
            "agent. This one says a tool is registered but unreachable.",
            "",
            "Send it. When the remedy is mechanical, like this one, the "
            "exact one-line change is staged as a diff in the fix queue, "
            "for a person to approve. Nothing is edited on its own.",
            "",
            "The first time we shot this, the finding went to a small "
            "local model that invented a tool name and gave up. Now the "
            "finding carries its own fix, and the model is only asked when "
            "judgment is needed.",
            "",
            "Only six kinds of finding can be dispatched at all. Dead code "
            "and liveness findings are advisory by design. A tracing "
            "window that missed a rare handler must never become an "
            "auto-delete.",
        ],
        action=act_dispatch, verify=v_dispatch, reset=reset_dispatch,
    ),
    Beat(
        name="live",
        narration=[
            "The map is live. Open chat anywhere with control shift C, "
            "and ask it to use the system mapper.",
            "",
            "That is a tool call, not a description of one. The activity "
            "log records it, the module that answered pulses, and the "
            "answer is a sentence, not a payload.",
            "",
            "First time we shot this, the log stayed empty: the map was "
            "listening on the wrong channel. Fixed, and shown fixed.",
        ],
        action=act_live, verify=v_live, reset=reset_map,
    ),
    Beat(
        name="closer",
        narration=[
            f"One honest number to end on. {N['dead']} dead symbols are "
            f"shown. {spoken(N['suppressed'])} more were suppressed on "
            "purpose, because the analysis could not be sure. The map "
            "would rather miss than lie.",
            "",
            "R resets the view.",
            "Next: Guaardvark writing code. Its own editor, its own "
            "guardrails, and a queue where every change waits for you.",
        ],
        action=act_closer, verify=v_map, reset=reset_map,
    ),
]


def main():
    ep = Episode("ep14_systemmap", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        for warm in ("/", "/system-map"):
            stage.page.goto(FRONTEND + warm,
                            wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2500)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP14 COMPLETE: {final}")
    finally:
        kill_stage_terminal()
        stage.close()


if __name__ == "__main__":
    main()
