"""Episode 3 — Your Files Have a Desktop (≈3:30).

The in-browser file desktop: folder windows, a real bulk folder-tree
import, in-app viewers (PDF / DOCX / CSV / audio), the opt-in media
gallery, folder-to-entity linking, RAG indexing with a live retrieval
test, and the live code-repository window.

GPU cast: Ollama (embeddings + retrieval synthesis). Kokoro narration
via audio foundry (tiny). Assets: staged tree in
data/demo_assets/ep03_tree/AcmeCorp (regenerated from the scratchpad
backup if an earlier take consumed it).

Run from scripts/demo_director/:  venv/bin/python episodes/ep03_files.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import requests as rq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import API, Beat, Episode, Stage, FRONTEND  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
TREE = REPO / "data" / "demo_assets" / "ep03_tree" / "AcmeCorp"
RETRIEVAL_Q = "What does the Acme service agreement say about renewal?"

# The team-memo playback must be audible: x11grab records video only, so the
# real track is mixed in at mux time. Predicting the click time across takes
# is unreliable, so act_viewers measures the actual click moment and rewrites
# the beat's overlay schedule before the mux reads it. MEMO_CLICK_AT is only
# a floor that keeps fast takes from outrunning the narration.
MEMO_WAV = TREE / "Media" / "team-memo-audio.wav"
MEMO_PLAY_S = 3.7
MEMO_CLICK_AT = 43.0
MEMO_AUTOPLAY_LAG = 1.2
LEAD_IN = 0.8


# ---------------------------------------------------------------- helpers

def sweep_windows(st: Stage):
    """Fold any open folder windows back into desktop icons (off camera)."""
    for _ in range(8):
        closes = st.page.locator(".folder-window svg[data-testid='CloseIcon']")
        if closes.count() == 0:
            break
        try:
            closes.first.locator("xpath=ancestor::button[1]").click(timeout=3000)
        except Exception:
            break
        st.page.wait_for_timeout(400)


def goto_documents(st: Stage):
    st.page.goto(FRONTEND + "/documents", wait_until="load", timeout=60_000)
    st.page.wait_for_timeout(2500)
    st.page.locator("[data-desktop-container]").first.wait_for(
        state="visible", timeout=15_000)


def reset_desktop(st: Stage):
    goto_documents(st)
    sweep_windows(st)
    st.cursor.jump(1500, 850)
    st.cursor.click()
    st.page.wait_for_timeout(400)


def delete_acme_import(st: Stage):
    """Remove any previous AcmeCorp import so the on-camera one is fresh."""
    try:
        r = rq.get(f"{API}/api/files/browse",
                   params={"path": "/", "fields": "light", "limit": 100},
                   timeout=15).json()
        data = r.get("data", r)
        for f in data.get("folders", []):
            if f.get("name") == "AcmeCorp":
                rq.delete(f"{API}/api/files/folder/{f['id']}", timeout=60)
    except Exception:
        pass


def desktop_card(st: Stage, name: str):
    return st.page.locator(".desktop-item-card").filter(
        has=st.page.get_by_text(name, exact=True)).first


def open_folder_window(st: Stage, name: str):
    glyph = desktop_card(st, name).locator(
        "[data-testid='FolderOutlinedIcon']")
    x, y = st.screen_xy(glyph.first)
    st.cursor.glide(x, y, dur=0.8)
    time.sleep(0.3)
    st.cursor.double_click()
    st.page.locator(".folder-window").last.wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.2)


def dclick_row(st: Stage, locator, settle: float = 1.4):
    x, y = st.screen_xy(locator)
    st.cursor.glide(x, y, dur=0.7)
    time.sleep(0.3)
    st.cursor.double_click()
    time.sleep(settle)


def close_dialog(st: Stage):
    """Close the top dialog via its title-bar CloseIcon button."""
    btn = st.page.locator(
        "div[role='dialog'] button:has(svg[data-testid='CloseIcon'])")
    if btn.count():
        st.glide_click(btn.last, dur=0.6)
        time.sleep(0.8)


def window_row(st: Stage, text_pattern):
    return st.page.locator(".folder-window tr").filter(
        has_text=text_pattern).first


# ---------------------------------------------------------------- beats

def act_desktop(st: Stage):
    open_folder_window(st, "Images")
    handle = st.page.locator(".folder-window-drag-handle").last
    hx, hy = st.screen_xy(handle)
    st.cursor.glide(hx, hy, dur=0.6)
    time.sleep(0.3)
    st.cursor.drag(700, 220, dur=1.1)
    time.sleep(0.8)
    # the react-resizable corner handle fails Playwright's visibility check
    # (styled hit-area) — drag from the window's own bottom-right corner
    win = st.page.locator(".folder-window").last
    box = win.bounding_box()
    ox, oy = st._offsets()
    gx = int(box["x"] + box["width"] - 8 + ox)
    gy = int(box["y"] + box["height"] - 8 + oy)
    st.cursor.glide(gx, gy, dur=0.6)
    time.sleep(0.3)
    st.cursor.drag(min(gx + 240, 1870), min(gy + 170, 1010), dur=1.0)
    time.sleep(0.8)
    open_folder_window(st, "Audio")
    arrange = st.page.locator(
        "button:has(svg[data-testid='AppsIcon'])").first
    st.glide_click(arrange, dur=0.8)
    time.sleep(2.0)


def v_desktop(st: Stage):
    assert "/documents" in st.path(), st.path()
    assert st.page.locator(".folder-window").count() >= 2


def reset_bulk(st: Stage):
    delete_acme_import(st)
    st.page.goto(FRONTEND + "/documents/bulk-import", wait_until="load",
                 timeout=60_000)
    st.page.wait_for_timeout(2000)
    st.page.get_by_label("Source Directory").first.wait_for(
        state="visible", timeout=15_000)


def act_bulk(st: Stage):
    src = st.page.get_by_label("Source Directory").first
    st.glide_click(src, dur=0.8)
    st.cursor.type_text(str(TREE), delay_ms=14)
    time.sleep(0.5)
    tgt = st.page.get_by_label("Target Folder (optional)").first
    st.glide_click(tgt, dur=0.7)
    st.cursor.type_text("AcmeCorp", delay_ms=40)
    time.sleep(0.4)
    force = st.page.get_by_label(re.compile("Force copy")).first
    st.glide_click(force, dur=0.7)
    time.sleep(0.4)
    start = st.page.get_by_role("button", name=re.compile("Start Import"))
    st.glide_click(start.first, dur=0.8)
    st.page.get_by_text("Confirm Bulk Import").first.wait_for(
        state="visible", timeout=10_000)
    time.sleep(1.2)
    confirm = st.page.get_by_role("button", name="Confirm Import")
    st.glide_click(confirm.first, dur=0.7)
    st.page.get_by_text(re.compile("Import complete")).first.wait_for(
        state="visible", timeout=180_000)
    time.sleep(1.5)


def v_bulk(st: Stage):
    assert st.page.get_by_text(re.compile("Import complete")).count() >= 1


def act_viewers(st: Stage):
    t0 = time.monotonic()

    def until(mark: float):
        remaining = mark - (time.monotonic() - t0)
        if remaining > 0:
            time.sleep(remaining)

    open_folder_window(st, "AcmeCorp")
    dclick_row(st, window_row(st, re.compile("^Contracts$|Contracts")))
    dclick_row(st, window_row(st, re.compile(r"service-agreement-2026\.pdf")),
               settle=2.8)
    close_dialog(st)
    home = st.page.locator(".folder-window").last.get_by_text(
        "Home", exact=True).first
    st.glide_click(home, dur=0.6)
    time.sleep(0.8)
    dclick_row(st, window_row(st, re.compile("Research")))
    dclick_row(st, window_row(st, re.compile(r"q3-briefing\.docx")),
               settle=2.8)
    close_dialog(st)
    st.glide_click(st.page.locator(".folder-window").last.get_by_text(
        "Home", exact=True).first, dur=0.6)
    time.sleep(0.8)
    dclick_row(st, window_row(st, re.compile("Invoices")))
    dclick_row(st, window_row(st, re.compile(r"fy2026-summary\.csv")),
               settle=2.2)
    close_btn = st.page.get_by_role("button", name="Close")
    if close_btn.count():
        st.glide_click(close_btn.last, dur=0.6)
        time.sleep(0.6)
    else:
        close_dialog(st)
    st.glide_click(st.page.locator(".folder-window").last.get_by_text(
        "Home", exact=True).first, dur=0.6)
    time.sleep(0.8)
    dclick_row(st, window_row(st, re.compile("Media")))
    until(MEMO_CLICK_AT)
    dclick_row(st, window_row(st, re.compile(r"team-memo-audio\.wav")),
               settle=0.05)
    clicked_at = time.monotonic() - t0
    _VIEWERS_BEAT.audio_overlays[:] = [
        (str(MEMO_WAV), LEAD_IN + clicked_at + MEMO_AUTOPLAY_LAG)]
    time.sleep(MEMO_PLAY_S + 1.2)
    ac = st.page.get_by_role("button", name="close")
    if ac.count():
        st.glide_click(ac.last, dur=0.6)
    else:
        close_dialog(st)
    time.sleep(0.8)


def v_documents(st: Stage):
    assert "/documents" in st.path(), st.path()


def reset_gallery(st: Stage):
    reset_desktop(st)


def act_gallery(st: Stage):
    open_folder_window(st, "AcmeCorp")
    dclick_row(st, window_row(st, re.compile("Media")))
    # Straight into the gallery: the toggle click lands as the narration
    # makes the claim, so the list view is a blink, not a beat.
    media_toggle = st.page.locator(
        ".folder-window button[value='media']").last
    st.glide_click(media_toggle, dur=0.5)
    time.sleep(2.6)
    nxt = st.page.get_by_role("button", name="Next")
    if nxt.count():
        st.glide_click(nxt.first, dur=0.7)
        time.sleep(1.6)
    lst = st.page.locator(".folder-window button[value='list']").last
    st.glide_click(lst, dur=0.7)
    time.sleep(1.0)
    dclick_row(st, window_row(st, re.compile(r"\.png")), settle=2.5)
    # fullscreen preview overlay; page to the sibling, then close
    arrows = st.page.locator("[data-testid='PlayArrowIcon']")
    if arrows.count() >= 2:
        st.glide_click(arrows.last.locator("xpath=ancestor::button[1]"),
                       dur=0.7)
        time.sleep(2.0)
    st.cursor._xdo("key", "Escape")
    time.sleep(0.8)


def act_properties(st: Stage):
    icon = desktop_card(st, "AcmeCorp").locator(
        "[data-testid='FolderOutlinedIcon']")
    x, y = st.screen_xy(icon.first)
    st.cursor.glide(x, y, dur=0.8)
    time.sleep(0.3)
    st.cursor.click(button=3)
    time.sleep(1.0)
    props = st.page.get_by_role("menuitem", name="Properties")
    st.glide_click(props.first, dur=0.7)
    st.page.get_by_text("Folder Properties").first.wait_for(
        state="visible", timeout=10_000)
    time.sleep(1.2)
    client = st.page.get_by_label(re.compile("^Link to Client")).first
    st.glide_click(client, dur=0.8)
    st.cursor.type_text("Acme", delay_ms=60)
    time.sleep(1.2)
    opt = st.page.get_by_role("option", name=re.compile("Acme"))
    if opt.count():
        st.glide_click(opt.first, dur=0.6)
        time.sleep(0.6)
    tags = st.page.get_by_label(re.compile("Tags")).first
    st.glide_click(tags, dur=0.7)
    st.cursor.type_text("client, demo", delay_ms=40)
    time.sleep(0.5)
    save = st.page.get_by_role("button", name=re.compile("Save"))
    st.glide_click(save.first, dur=0.8)
    st.page.get_by_text(re.compile("successfully")).first.wait_for(
        state="visible", timeout=30_000)
    time.sleep(1.5)


def reset_retrieval(st: Stage):
    """Wait off camera until every AcmeCorp text doc is INDEXED, so the
    on-camera retrieval test hits real chunks instead of a race."""
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            r = rq.get(f"{API}/api/files/browse",
                       params={"path": "/AcmeCorp/Contracts",
                               "fields": "light", "limit": 20},
                       timeout=10).json()
            docs = r.get("data", r).get("documents", [])
            pdfs = [d for d in docs if d.get("filename", "").endswith(".md")]
            if pdfs and all(d.get("index_status") == "INDEXED" for d in pdfs):
                break
        except Exception:
            pass
        time.sleep(5)
    reset_desktop(st)


def act_retrieval(st: Stage):
    icon = desktop_card(st, "AcmeCorp").locator(
        "[data-testid='FolderOutlinedIcon']")
    x, y = st.screen_xy(icon.first)
    st.cursor.glide(x, y, dur=0.8)
    time.sleep(0.3)
    st.cursor.click(button=3)
    time.sleep(1.0)
    idx = st.page.get_by_role("menuitem", name="Index Contents")
    st.glide_click(idx.first, dur=0.7)
    time.sleep(2.5)
    st.nav_via_sidebar("Settings", "/settings",
                       st.page.get_by_text("Settings").first)
    time.sleep(1.5)
    btn = st.page.get_by_role("button", name="Test Retrieval")
    st.glide_click(btn.first, dur=0.9)
    st.page.get_by_text("Test RAG Retrieval").first.wait_for(
        state="visible", timeout=10_000)
    time.sleep(0.8)
    q = st.page.get_by_label("Query").first
    st.glide_click(q, dur=0.7)
    st.cursor.type_text(RETRIEVAL_Q, delay_ms=22)
    time.sleep(0.5)
    run = st.page.locator(
        "div[role='dialog']").get_by_role(
        "button", name=re.compile("^Test Retrieval$|^Testing"))
    st.glide_click(run.last, dur=0.7)
    st.page.get_by_text(re.compile(r"Retrieval Results")).first.wait_for(
        state="visible", timeout=120_000)
    time.sleep(1.0)
    rank = st.page.get_by_text(re.compile("Rank 1"))
    if rank.count():
        st.glide_click(rank.first, dur=0.8)
    time.sleep(2.5)
    st.cursor._xdo("key", "Escape")
    time.sleep(0.6)


def v_settings(st: Stage):
    assert "/settings" in st.path(), st.path()


def act_repo(st: Stage):
    open_folder_window(st, "Guaardvark Code")
    chip = st.page.get_by_text("CODE REPO", exact=True)
    if chip.count():
        st.hover_over(chip.first, dur=0.8)
        time.sleep(1.0)
    acc = st.page.get_by_text("Repository Analysis")
    if acc.count():
        st.glide_click(acc.first, dur=0.8)
        time.sleep(3.0)
    time.sleep(1.5)


def v_repo(st: Stage):
    assert not st.page.get_by_text("Failed to load folder contents").count(), \
        "repo window shows a load failure"
    assert st.page.get_by_text("backend", exact=True).count(), \
        "repo listing never rendered"


def act_closer(st: Stage):
    arrange = st.page.locator(
        "button:has(svg[data-testid='GridViewIcon'])").first
    st.glide_click(arrange, dur=0.8)
    time.sleep(1.5)
    icon = desktop_card(st, "AcmeCorp").locator(
        "[data-testid='FolderOutlinedIcon']")
    if icon.count():
        st.hover_over(icon.first, dur=0.9)
    time.sleep(1.5)


BEATS = [
    Beat(
        name="hook_desktop",
        narration=[
            "This is a browser tab.",
            "",
            "Those are folder windows. Drag them. Resize them. Snap them "
            "to a grid.",
            "",
            "Your files didn't move to the cloud. The desktop moved into "
            "Guaardvark.",
        ],
        action=act_desktop,
        verify=v_desktop,
        reset=reset_desktop,
    ),
    Beat(
        name="bulk_import",
        narration=[
            "Got years of files sitting in a folder? Point the importer "
            "at it.",
            "",
            "The whole tree comes in with its nesting intact. Contracts, "
            "research, invoices, media.",
            "",
            "Ten files just became a client workspace.",
        ],
        action=act_bulk,
        verify=v_bulk,
        reset=reset_bulk,
    ),
    Beat(
        name="viewers",
        # Blank runs are constructed silence (0.55s each): the lines are paced
        # to land on the viewer they describe at this beat's measured tempo.
        narration=[
            "And you never leave to look at anything.",
            "", "", "", "", "", "", "", "",
            "A contract opens right in place. The viewer is built into the "
            "window — scroll it, read it, close it.",
            "", "", "", "", "", "",
            "The quarterly briefing is a Word document. It just opens. "
            "No download. No second application.",
            "", "", "", "", "", "", "", "", "",
            "Spreadsheets render straight from the folder — live rows, "
            "not a preview.",
            "", "", "",
            "None of this ever left the machine. It's already home.",
            "",
            "And audio plays right where it lives.",
            "Listen.",
        ],
        audio_overlays=[(str(MEMO_WAV), 45.0)],
        action=act_viewers,
        verify=v_documents,
        reset=reset_desktop,
    ),
    Beat(
        name="gallery",
        narration=[
            "Folders with pictures get a gallery view. Opt in, per window.",
            "",
            "And full screen, with paging.",
            "Your renders and your photos, one keystroke apart.",
        ],
        action=act_gallery,
        verify=v_documents,
        reset=reset_gallery,
    ),
    Beat(
        name="properties",
        narration=[
            "Folders can belong to someone.",
            "Link this one to a client, tag it, and everything inside "
            "inherits the link.",
            "",
            "That's how the rest of the system knows whose work this is.",
        ],
        action=act_properties,
        verify=v_documents,
        reset=reset_desktop,
    ),
    Beat(
        name="index_retrieval",
        narration=[
            "Now the important part. Index the folder. Then ask.",
            "",
            "But don't take the answer on faith.",
            "This is the retrieval test: the actual chunks it found, "
            "with the actual scores.",
            "If the system is ever wrong, you can see exactly where.",
        ],
        action=act_retrieval,
        verify=v_settings,
        reset=reset_retrieval,
    ),
    Beat(
        name="repo",
        narration=[
            "Code gets the same treatment.",
            "This folder is the system's own repository. Live, browsable, "
            "language detected.",
            "",
            "Same windows. Same rules. Your code is just another folder "
            "here — one the system can read.",
        ],
        action=act_repo,
        verify=v_repo,
        reset=reset_desktop,
    ),
    Beat(
        name="closer",
        narration=[
            "So your files have a desktop. Viewers. Owners. And a search "
            "that shows its work.",
            "",
            "Who tunes those retrieval parameters? The system does. "
            "Overnight. That's episode eleven.",
            "",
            "One machine. No cloud.",
        ],
        action=act_closer,
        verify=v_documents,
        reset=reset_desktop,
    ),
]


_VIEWERS_BEAT = next(b for b in BEATS if b.name == "viewers")


def main():
    ep = Episode("ep03_files", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=60_000)
        stage.page.wait_for_timeout(2000)
        for warm in ("/documents", "/documents/bulk-import", "/settings",
                     "/documents"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP03 COMPLETE: {final}")
    finally:
        stage.close()


if __name__ == "__main__":
    main()
