"""Episode 6 — Hollywood on One GPU (≈4:30).

Model registry + downloads, preset tour, instant prompt styles, a real
launch with stage chips, Draft-vs-Cinema side by side, Advanced Editor nod.

GPU cast: ComfyUI (the on-camera launch renders during later beats — that's
the story). Ollama NOT needed. Kokoro narration via audio foundry (tiny).
Assets in: Draft + Cinema lighthouse renders (same prompt, same seed):
  VideoBatch_08-14-2026_054328_077 (draft) / _078 (cinema)

Run from scripts/demo_director/:  venv/bin/python episodes/ep06_video.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage, FRONTEND  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
DRAFT_BATCH = "VideoBatch_08-14-2026_054328_077"
CINEMA_BATCH = "VideoBatch_08-14-2026_054328_078"
PROMPT = ("The lighthouse keeper rows his wooden boat through heavy swell "
          "toward the tower, rain streaking the lens")


def reset_home(st: Stage):
    st.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
    st.page.wait_for_timeout(1500)
    st.cursor.jump(960, 700)
    st.cursor.click()
    st.page.wait_for_timeout(300)


def sweep_windows(st: Stage):
    st.page.goto(FRONTEND + "/documents", wait_until="load", timeout=30_000)
    st.page.wait_for_timeout(1500)
    for _ in range(6):
        closes = st.page.locator("svg[data-testid='CloseIcon']")
        if closes.count() == 0:
            break
        try:
            closes.first.locator("xpath=ancestor::button[1]").click(timeout=3000)
        except Exception:
            break
        st.page.wait_for_timeout(400)


def reset_swept(st: Stage):
    sweep_windows(st)
    reset_home(st)


def nav_video(st: Stage):
    st.nav_via_sidebar("Video Gen", "/video",
                       st.page.get_by_role("button",
                                           name=re.compile("add to queue", re.I)))
    time.sleep(1.0)


# ---------------------------------------------------------------- beats

def act_models(st: Stage):
    nav_video(st)
    btn = st.page.get_by_role("button", name=re.compile("manage video models", re.I))
    st.glide_click(btn.first, dur=0.9)
    time.sleep(4.0)                       # registry modal: models + sizes
    st.cursor._xdo("key", "Escape")
    time.sleep(0.8)


def act_presets(st: Stage):
    nav_video(st)
    # open quality, duration, aspect dropdowns in turn — show the menus
    for pattern in ("Standard", "Short", "16:9"):
        combo = st.page.get_by_role("combobox").filter(
            has_text=re.compile(pattern, re.I))
        if not combo.count():
            continue
        st.glide_click(combo.first, dur=0.8)
        time.sleep(2.2)                   # let the option list read on camera
        st.cursor._xdo("key", "Escape")
        time.sleep(0.6)


def act_launch(st: Stage):
    nav_video(st)
    box = st.page.get_by_role("textbox").first
    st.glide_click(box, dur=0.8)
    st.cursor._xdo("key", "ctrl+a")
    st.cursor.type_text(PROMPT, delay_ms=24)
    time.sleep(0.5)
    preview = st.page.get_by_role("button",
                                  name=re.compile("preview enhanced", re.I))
    st.glide_click(preview.first, dur=0.7)
    time.sleep(3.0)                       # instant style concat — no LLM
    add = st.page.get_by_role("button", name=re.compile("add to queue", re.I))
    st.glide_click(add.first, dur=0.8)
    time.sleep(6.0)                       # stage chip appears in the queue


def act_chips(st: Stage):
    # just watch the queue: stage chips on the running batch
    nav_video(st)
    time.sleep(2.0)
    chip = st.page.get_by_text(re.compile("queued|waiting|storyboard|generating|gpu", re.I))
    if chip.count():
        st.hover_over(chip.first, dur=1.0)
    time.sleep(2.0)


def _open_batch_preview(st: Stage, batch: str):
    icon = st.page.get_by_text("Videos", exact=True).first
    lx, ly = st.screen_xy(icon)
    st.cursor.glide(lx, ly - 38, dur=0.8)
    time.sleep(0.3)
    st.cursor.double_click()
    st.page.get_by_text("Home", exact=True).first.wait_for(
        state="visible", timeout=10_000)
    time.sleep(1.0)
    for _ in range(2):
        if st.page.get_by_text(batch).count():
            break
        st.glide_click(st.page.get_by_text("Date", exact=True).first, dur=0.6)
        time.sleep(1.0)
    def _dclick_row(locator):
        x, y = st.screen_xy(locator)
        st.cursor.glide(x, y, dur=0.7)
        time.sleep(0.3)
        st.cursor.double_click()
        time.sleep(1.6)

    _dclick_row(st.page.get_by_text(batch).first)
    # the Files UI FLATTENS the batch — the mp4 lists directly (the on-disk
    # uuid/videos nesting is hidden by the app's virtual hierarchy)
    clip = st.page.get_by_text(re.compile(r"\.mp4$", re.I)).first
    cx, cy = st.screen_xy(clip)
    st.cursor.glide(cx, cy, dur=0.6)
    time.sleep(0.3)
    st.cursor.double_click()
    time.sleep(6.0)                       # video plays in fullscreen preview
    st.cursor._xdo("key", "Escape")
    time.sleep(0.6)


def act_draft(st: Stage):
    st.nav_via_sidebar("Files", "/documents",
                       st.page.get_by_text("Videos", exact=True))
    _open_batch_preview(st, DRAFT_BATCH)


def act_cinema(st: Stage):
    st.nav_via_sidebar("Files", "/documents",
                       st.page.get_by_text("Videos", exact=True))
    _open_batch_preview(st, CINEMA_BATCH)


def act_advanced(st: Stage):
    nav_video(st)
    btn = st.page.get_by_role("button", name=re.compile("advanced editor", re.I))
    st.hover_over(btn.first, dur=1.2)     # hover only: opens a new tab, which
    time.sleep(2.5)                       # would hijack the kiosk stage


def v_video(st: Stage):
    assert "/video" in st.path(), st.path()


def v_files(st: Stage):
    assert "/documents" in st.path(), st.path()


BEATS = [
    Beat(
        name="hook_models",
        narration=[
            "Eleven video models. Five families.",
            "Wan. CogVideo X. LTX. Hunyuan. MiniMax H3.",
            "Text to video. Image to video. And one of them talks: "
            "picture and its own soundtrack in one pass.",
            "",
            "On the same card that ran your chat, your images, and the "
            "voice you're hearing.",
            "",
            "Every model downloads right here. Sizes, requirements, "
            "progress bars. All local.",
        ],
        action=act_models,
        verify=v_video,
        reset=reset_home,
    ),
    Beat(
        name="presets",
        narration=[
            "You don't tune tensors here. You pick presets.",
            "",
            "Quality, from a ten step draft to a fifty step master.",
            "Duration. Aspect ratio.",
            "And here's the trick: reshape the frame, and the VRAM bill "
            "stays the same. The pixel budget just redistributes.",
        ],
        action=act_presets,
        verify=v_video,
        reset=reset_home,
    ),
    Beat(
        name="launch",
        narration=[
            "Let's make a shot.",
            "The keeper, rowing home through the storm.",
            "",
            "Preview the enhanced prompt. That's instant. String craft, "
            "not an LLM call.",
            "",
            "And into the queue.",
            "Watch the stage chips: queued. Storyboard. Generating.",
            "If the card is busy, it says so, and waits its turn. Renders "
            "never fail just because the GPU had a customer.",
        ],
        action=act_launch,
        verify=v_video,
        reset=reset_home,
    ),
    Beat(
        name="draft",
        narration=[
            "While that cooks, here's the same shot, rendered two ways. "
            "Same prompt. Same seed.",
            "",
            "First: draft tier. Ten steps, raw output.",
            "Quick, rough, good enough to judge an idea.",
        ],
        action=act_draft,
        verify=v_files,
        reset=reset_swept,
    ),
    Beat(
        name="cinema",
        narration=[
            "And now cinema tier.",
            "Forty steps. Frame interpolation for motion. A super "
            "resolution pass on top.",
            "",
            "Same machine. Same card. Just more patience.",
        ],
        action=act_cinema,
        verify=v_files,
        reset=reset_swept,
    ),
    Beat(
        name="advanced_closer",
        narration=[
            "And when presets aren't enough, one click drops you into the "
            "full node editor underneath. ComfyUI, themed to match.",
            "",
            "Single clips are one thing.",
            "Next episode: drop in a song, and get a whole music video "
            "back, cut to the beat.",
            "",
            "One machine. No cloud.",
        ],
        action=act_advanced,
        verify=v_video,
        reset=reset_home,
    ),
]


def main():
    ep = Episode("ep06_video", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
        stage.page.wait_for_timeout(2000)
        for warm in ("/video", "/documents", "/"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP06 COMPLETE: {final}")
    finally:
        stage.close()


if __name__ == "__main__":
    main()
