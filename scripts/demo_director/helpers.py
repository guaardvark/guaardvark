"""Shared stage helpers for the series-two episodes (13 onward).

The first-series beat files each carried private copies of goto /
close_dialogs / stage_terminal; new episodes import them from here so a
selector fix lands once. Also home to the two things every series-two beat
does: seed the navigation chrome the episode is shot on, and refuse a take
whose visible page names something this clone keeps private.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests as rq

from director import API, FRONTEND, Stage

REPO = Path(__file__).resolve().parents[2]

# Untracked, per-clone: one "pattern<TAB>why" per line. Read at verify time,
# never quoted into narration, on-screen text or SERIES.md.
PRIVATE_PATTERNS_FILE = REPO / "scripts" / ".portable-local-patterns"

# Every command string handed to the stage terminal, and the typescript
# each terminal session wrote, so the privacy check covers what was typed
# and what scrolled past on camera as well as what the page rendered.
_TERMINAL_COMMANDS: list[str] = []
_TERMINAL_LOGS: list[Path] = []
TERMINAL_LOG_DIR = REPO / "docs" / "local-workspace-only" / "demo_terminal_logs"


# ------------------------------------------------------------- navigation

def goto(st: Stage, path: str, settle: float = 2.5):
    st.page.goto(FRONTEND + path, wait_until="load", timeout=60_000)
    st.page.wait_for_timeout(int(settle * 1000))


def close_dialogs(st: Stage):
    for _ in range(2):
        if st.page.locator("div[role='dialog']").count():
            st.cursor._xdo("key", "Escape")
            time.sleep(0.8)


def set_nav_chrome(st: Stage, mode: str = "software", path: str | None = None):
    """Seed the persisted app store so the next load renders the given
    chrome ("software" = Workspaces top bar, "sidebar" = classic). A fresh
    Playwright profile always starts on the sidebar; every beat that should
    open on the bar seeds it here, in its reset, off camera."""
    assert mode in ("software", "sidebar"), mode
    st.page.evaluate(
        """(mode) => {
            const key = "guaardvark-app-storage";
            let blob = {state: {}, version: 0};
            try { blob = JSON.parse(localStorage.getItem(key)) || blob; }
            catch (e) {}
            blob.state = Object.assign({}, blob.state, {navChrome: mode});
            localStorage.setItem(key, JSON.stringify(blob));
        }""", mode)
    if path is not None:
        goto(st, path)
    else:
        st.page.reload(wait_until="load", timeout=60_000)
        st.page.wait_for_timeout(2000)


def workspace_button(st: Stage, label: str):
    return st.page.locator("nav[aria-label='Workspace']").get_by_role(
        "button", name=label, exact=True)


def tool_tab(st: Stage, label: str):
    return st.page.get_by_role("tablist", name="Workspace tools").get_by_role(
        "tab", name=label, exact=True)


def open_workspace(st: Stage, label: str, expect_path: str, dur: float = 0.8):
    """Click a workspace on the top bar and wait for its first tool's route."""
    st.glide_click(workspace_button(st, label), dur=dur)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if expect_path in st.path():
            return
        time.sleep(0.2)
    raise RuntimeError(f"workspace {label!r} did not land on {expect_path}; "
                       f"path={st.path()}")


# --------------------------------------------------------------- terminal

def stage_terminal(cmd: str, cwd: Path | None = None):
    """Spawn a terminal on the stage display running cmd. GTK needs the
    Wayland handle scrubbed or it opens on the host desktop. The session
    runs under script(1) so everything shown on camera is captured to a
    typescript the privacy check can grep."""
    _TERMINAL_COMMANDS.append(cmd)
    TERMINAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = TERMINAL_LOG_DIR / f"terminal_{int(time.time())}.typescript"
    _TERMINAL_LOGS.append(log)
    env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
    env["DISPLAY"] = os.environ.get("DEMO_DISPLAY", ":98")
    env["GDK_BACKEND"] = "x11"
    # The product's own binaries on PATH, so a command never has to spell
    # out the checkout's absolute path (which the typescript would record).
    env["PATH"] = f"{REPO / 'backend' / 'venv' / 'bin'}:{env.get('PATH', '')}"
    subprocess.Popen(
        ["ptyxis", "--standalone", "--", "script", "-qfc", cmd, str(log)],
        cwd=str(cwd or REPO), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def kill_stage_terminal():
    # --standalone windows only; the operator's own ptyxis runs as a
    # gapplication service and never matches this pattern.
    subprocess.run(["pkill", "-f", "ptyxis --standalon[e]"], check=False)


def type_line(st: Stage, text: str, delay_ms: int = 80, settle: float = 2.0):
    st.cursor.type_text(text, delay_ms=delay_ms)
    st.cursor._xdo("key", "Return")
    time.sleep(settle)


# ---------------------------------------------------------------- guards

def require(cond, msg: str):
    """Precondition for a reset: an empty or wrong surface fails the take
    before the recorder starts, instead of shipping narration over nothing."""
    if not cond:
        raise RuntimeError(f"precondition failed: {msg}")


def load_private_patterns() -> list[tuple[re.Pattern, str]]:
    out = []
    if not PRIVATE_PATTERNS_FILE.exists():
        return out
    for raw in PRIVATE_PATTERNS_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pat, _, why = line.partition("\t")
        try:
            out.append((re.compile(pat), why.strip()))
        except re.error:
            continue
    return out


def verify_no_private_names(st: Stage):
    """Fail the take if the visible page, the tab title or anything typed
    into the stage terminal matches a per-clone private pattern."""
    pats = load_private_patterns()
    require(pats, f"no private patterns loaded from {PRIVATE_PATTERNS_FILE}")
    visible = st.page.evaluate(
        "() => (document.title || '') + '\\n' + (document.body ? document.body.innerText : '')")
    corpus = [("page", visible)] + [("terminal", c) for c in _TERMINAL_COMMANDS]
    for log in _TERMINAL_LOGS:
        if log.exists():
            # script(1) writes "Script started/done" header lines carrying the
            # full command into the file only; they are never on screen.
            body = "\n".join(
                line for line in log.read_bytes().decode("utf-8", "replace").splitlines()
                if not line.startswith(("Script started", "Script done")))
            corpus.append((f"terminal output {log.name}", body))
    for where, text in corpus:
        for pat, why in pats:
            m = pat.search(text)
            if m:
                raise RuntimeError(
                    f"private name on camera ({where}): pattern #{pats.index((pat, why)) + 1} "
                    f"matched {m.group(0)!r} — {why}")
    print(f"  privacy: {len(pats)} patterns, 0 hits")


def verify_path(st: Stage, prefix: str):
    p = st.path()
    assert prefix in p, f"expected {prefix} in path, got {p}"
    verify_no_private_names(st)


def verify_visible(st: Stage, locator, what: str):
    require(locator.count() and locator.first.is_visible(), f"{what} not visible")
    verify_no_private_names(st)


# ------------------------------------------------------------------ api

def api_get(path: str, timeout: float = 20, **params):
    r = rq.get(f"{API}{path}", params=params or None, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    return body.get("data", body) if isinstance(body, dict) else body


def snapshot(st: Stage, out: Path):
    """Full-display PNG for dry-run review."""
    subprocess.run(["import", "-window", "root", "-display", st.display, str(out)],
                   check=False)
    return out
