"""Episode 2 — A Brain With Three Speeds (≈3:30).

Reflex / instinct / deliberation tier tour in the unified chat, the slash
command deck, inline /imagine, the floating chat card, the narrate button,
lesson pearls, and a file-drop closer.

GPU cast: Ollama live (gemma4 chat + tier-3 deliberation); ComfyUI serves
the single /imagine still. Kokoro narration via audio foundry (tiny).
Assets: none — everything happens live in the chat.

Run from scripts/demo_director/:  venv/bin/python episodes/ep02_brain.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage, FRONTEND  # noqa: E402

REPO = Path(__file__).resolve().parents[3]

CHAT_PLACEHOLDER = "Type your message, paste an image, or use voice..."
INSTINCT_Q = ("In two sentences, why did lighthouses use rotating lenses "
              "instead of a fixed lamp?")
DELIB_Q = ("Research the guaardvark architecture and then write a two line "
           "summary of how the system is put together. There is an image "
           "on the website, by the way.")
IMAGINE_ARGS = "a stone lighthouse at dawn, golden light, sea mist, cinematic"
NARRATE_Q = "In one short sentence, what is a Fresnel lens?"
LESSON_Q = "Our project ships every Friday. Remember that. What day do we ship?"
DROP_FILE = REPO / "data/demo_assets/ep03_tree/AcmeCorp/Research/market-notes.md"


def chat_input(st: Stage):
    return st.page.get_by_placeholder(CHAT_PLACEHOLDER)


def nav_chat(st: Stage):
    if "/chat" not in st.path():
        st.nav_via_sidebar("Chat", "/chat", chat_input(st))
    chat_input(st).first.wait_for(state="visible", timeout=15_000)
    time.sleep(0.8)


def new_chat(st: Stage):
    """Off-camera: clean session so a tier beat opens on an empty chat."""
    nav_chat(st)
    try:
        st.page.locator("button:has(svg[data-testid='AddIcon'])").first.click(
            timeout=5_000)
        st.page.wait_for_timeout(1200)
    except Exception:
        pass


def close_dialogs(st: Stage):
    for _ in range(2):
        if st.page.locator("div[role='dialog']").count():
            st.cursor._xdo("key", "Escape")
            time.sleep(0.8)


def type_and_send(st: Stage, text: str, delay_ms: int = 28):
    box = chat_input(st).first
    st.glide_click(box, dur=0.7)
    st.cursor.type_text(text, delay_ms=delay_ms)
    time.sleep(0.5)
    # a live slash popup would swallow Return as "select command"
    if st.page.locator("[data-command-row]").count():
        st.cursor._xdo("key", "Escape")
        time.sleep(0.3)
    st.cursor._xdo("key", "Return")


def wait_reply(st: Stage, n0: int, timeout: float = 120.0) -> None:
    """Wait until a new finished assistant message lands (narrate button)."""
    sel = "button:has(svg[data-testid='RecordVoiceOverIcon'])"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if st.page.locator(sel).count() > n0:
            return
        time.sleep(0.8)
    raise RuntimeError("assistant reply never finished")


def narrate_btn_count(st: Stage) -> int:
    return st.page.locator(
        "button:has(svg[data-testid='RecordVoiceOverIcon'])").count()


# ---------------------------------------------------------------- resets

def reset_hard_chat(st: Stage):
    subprocess.run(["pkill", "-9", "-f", "vlc"], check=False)
    st.page.goto(FRONTEND + "/chat", wait_until="load", timeout=30_000)
    st.page.wait_for_timeout(2500)
    new_chat(st)


def reset_new_chat(st: Stage):
    subprocess.run(["pkill", "-9", "-f", "vlc"], check=False)
    close_dialogs(st)
    new_chat(st)


def reset_same_chat(st: Stage):
    close_dialogs(st)
    nav_chat(st)


def reset_imagine(st: Stage):
    """Free the card for the inline still: narration is already synthesized,
    so the foundry's ~4GB of idle TTS CUDA contexts can go, and gemma4
    reloads lazily. Then pre-warm the SD pipeline off camera so the
    on-camera /imagine answers in seconds, not a cold model load."""
    import requests as rq
    for intent in ("voice", "music", "fx"):
        try:
            rq.post(f"http://127.0.0.1:8206/evict/{intent}", timeout=30)
        except Exception:
            pass
    try:
        rq.post("http://localhost:5000/api/model/unload", timeout=30)
    except Exception:
        pass
    try:
        rq.post(
            "http://localhost:5000/api/chat/unified/direct-tool",
            json={"session_id": "ep02-warm",
                  "slash_command": "imagine",
                  "slash_args": "warmup thumbnail, plain gray card"},
            timeout=280,
        )
    except Exception:
        pass
    # the gate holds an 8s post-release cooldown after the warmup render —
    # wait it out off camera or the on-camera /imagine lands inside it
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            g = rq.get("http://localhost:5000/api/jobs/gate", timeout=5).json()
            if not g.get("gpu_busy") and g.get("gpu_cooldown_remaining_s", 0) == 0:
                break
        except Exception:
            break
        time.sleep(1.0)
    time.sleep(2.0)
    reset_same_chat(st)


def reset_narrate(st: Stage):
    """The imagine reset evicted gemma4 — pull it back into VRAM off camera
    so the on-camera question answers promptly instead of on a cold load."""
    import requests as rq
    try:
        rq.post("http://localhost:11434/api/generate",
                json={"model": "gemma4:latest", "prompt": "ok",
                      "stream": False},
                timeout=180)
    except Exception:
        pass
    reset_same_chat(st)


def reset_floating(st: Stage):
    # a retake may leave the card open on /video — close it, return to chat
    try:
        if st.page.locator("button[title='Close']").count():
            st.page.locator("button[title='Close']").first.click(timeout=3000)
            st.page.wait_for_timeout(600)
    except Exception:
        pass
    nav_chat(st)


def reset_lessons(st: Stage):
    close_dialogs(st)
    # end any stray active lesson so Begin is clickable again
    try:
        if st.page.locator("svg[data-testid='StopCircleIcon']").count():
            st.page.locator(
                "button:has(svg[data-testid='StopCircleIcon'])").first.click(
                timeout=3000)
            st.page.get_by_text("Lesson Summary").first.wait_for(
                state="visible", timeout=90_000)
            close_dialogs(st)
    except Exception:
        pass
    nav_chat(st)


# ---------------------------------------------------------------- beats

def act_reflex(st: Stage):
    nav_chat(st)
    type_and_send(st, "play the grey hour")
    st.page.get_by_text(
        re.compile(r"tracks|playing|matching", re.I)).first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.5)


def act_instinct(st: Stage):
    nav_chat(st)
    try:
        st.hover_over(st.page.get_by_text(re.compile("^Model: ")).first,
                      dur=0.8)
        time.sleep(1.2)
    except Exception:
        pass
    n0 = narrate_btn_count(st)
    type_and_send(st, INSTINCT_Q)
    wait_reply(st, n0, timeout=120)
    time.sleep(1.0)


def act_deliberate(st: Stage):
    nav_chat(st)
    type_and_send(st, DELIB_Q)
    trail = st.page.get_by_text(re.compile(r"Agent thinking — \d+ steps?"))
    trail.first.wait_for(state="visible", timeout=120_000)
    time.sleep(0.5)
    st.glide_click(trail.first, dur=0.8)   # expand the live trail
    time.sleep(2.0)
    # let the answer finish if it's quick; otherwise cut while it thinks
    try:
        wait_reply(st, 0, timeout=90)
        # re-expand the persisted trail (it re-mounts collapsed on completion)
        if trail.first.is_visible():
            st.glide_click(trail.first, dur=0.7)
        time.sleep(1.5)
    except Exception:
        pass


def act_slash(st: Stage):
    nav_chat(st)
    box = chat_input(st).first
    st.glide_click(box, dur=0.7)
    st.cursor.type_text("/", delay_ms=60)
    st.page.locator("[data-command-row]").first.wait_for(
        state="visible", timeout=10_000)
    time.sleep(1.6)
    for _ in range(4):
        st.cursor._xdo("key", "Down")
        time.sleep(0.7)
    time.sleep(1.0)
    st.cursor._xdo("key", "Escape")
    time.sleep(0.4)
    st.cursor._xdo("key", "BackSpace")
    time.sleep(0.4)


def act_imagine(st: Stage):
    nav_chat(st)
    box = chat_input(st).first
    st.glide_click(box, dur=0.7)
    n_img = st.page.locator("img").count()
    st.cursor.type_text("/imagine " + IMAGINE_ARGS, delay_ms=30)
    time.sleep(0.6)
    if st.page.locator("[data-command-row]").count():
        st.cursor._xdo("key", "Escape")
        time.sleep(0.3)
    st.cursor._xdo("key", "Return")
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        if st.page.locator("img").count() > n_img:
            break
        time.sleep(1.0)
    else:
        raise RuntimeError("inline /imagine image never arrived")
    time.sleep(3.0)


def act_floating(st: Stage):
    st.nav_via_sidebar(
        "Video Gen", "/video",
        st.page.get_by_role("button", name=re.compile("add to queue", re.I)))
    time.sleep(1.2)
    fab = st.page.get_by_role("button", name="Open chat (Ctrl+Shift+C)")
    st.glide_click(fab.first, dur=0.9)
    st.page.locator("button[title='New chat']").first.wait_for(
        state="visible", timeout=10_000)
    time.sleep(1.0)
    # drag the card by its header title (the card's "Chat" text is the last)
    title = st.page.get_by_text("Chat", exact=True).last
    hx, hy = st.screen_xy(title)
    st.cursor.glide(hx, hy, dur=0.7)
    time.sleep(0.3)
    st.cursor.drag(650, 260, dur=1.3)
    time.sleep(1.2)
    try:
        chip = st.page.get_by_text(re.compile("Video Generator")).last
        st.hover_over(chip, dur=0.8)
        time.sleep(1.0)
    except Exception:
        pass


def act_narrate(st: Stage):
    nav_chat(st)
    n0 = narrate_btn_count(st)
    type_and_send(st, NARRATE_Q)
    wait_reply(st, n0, timeout=120)
    time.sleep(0.8)
    n_before = narrate_btn_count(st)
    btn = st.page.locator(
        "button:has(svg[data-testid='RecordVoiceOverIcon'])").last
    st.glide_click(btn, dur=0.9)
    # while it synthesizes, the icon is a spinner (count drops); wait for the
    # drop first, then for the icon to come back
    dropped = False
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if narrate_btn_count(st) < n_before:
            dropped = True
            break
        time.sleep(0.4)
    if dropped:
        # a finished narration swaps the icon row for an inline audio player,
        # so the icon count may never recover — hold briefly, don't stake the
        # take on it
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if narrate_btn_count(st) >= n_before:
                break
            time.sleep(0.8)
    time.sleep(2.0)


def act_lessons(st: Stage):
    nav_chat(st)
    st.glide_click(
        st.page.locator("button:has(svg[data-testid='SchoolIcon'])").first,
        dur=0.9)
    st.page.get_by_text(
        re.compile(r"Lesson — \d+ pearls?")).first.wait_for(
        state="visible", timeout=10_000)
    time.sleep(1.0)
    n0 = narrate_btn_count(st)
    type_and_send(st, LESSON_Q)
    wait_reply(st, n0, timeout=120)
    time.sleep(0.6)
    st.glide_click(
        st.page.locator(
            "button:has(svg[data-testid='ThumbUpOutlinedIcon'])").last,
        dur=0.8)
    st.page.get_by_text(
        re.compile(r"Lesson — 1 pearl")).first.wait_for(
        state="visible", timeout=20_000)
    time.sleep(1.4)
    st.glide_click(
        st.page.locator(
            "button:has(svg[data-testid='StopCircleIcon'])").first,
        dur=0.8)
    st.page.get_by_text("Lesson Summary").first.wait_for(
        state="visible", timeout=90_000)
    time.sleep(2.5)


def act_closer(st: Stage):
    nav_chat(st)
    attach = st.page.get_by_role("button", name="Attach file or image")
    st.hover_over(attach.first, dur=0.9)
    time.sleep(0.6)
    st.page.locator("input[type='file']").first.set_input_files(str(DROP_FILE))
    st.page.get_by_text(
        re.compile("Uploaded Successfully|indexing", re.I)).first.wait_for(
        state="visible", timeout=90_000)
    try:
        st.page.get_by_text(
            re.compile("Uploaded Successfully")).first.wait_for(
            state="visible", timeout=60_000)
    except Exception:
        pass
    time.sleep(1.5)


def v_chat(st: Stage):
    assert "/chat" in st.path(), st.path()


def v_floating(st: Stage):
    assert "/video" in st.path(), st.path()
    assert st.page.locator("button[title='New chat']").count() >= 1


BEATS = [
    Beat(
        name="hook_reflex",
        narration=[
            "Ask it to play a song.",
            "",
            "Done. That answer came back in under a hundred milliseconds, "
            "with zero AI calls.",
            "Tier one. The reflex layer: a pattern table, wired straight "
            "to the tools.",
            "",
            "Same chat box. Three different brains.",
        ],
        action=act_reflex,
        verify=v_chat,
        reset=reset_hard_chat,
    ),
    Beat(
        name="instinct",
        narration=[
            "Tier two: instinct.",
            "One question. One model call.",
            "",
            "This is gemma four, streaming every token from the card as "
            "it's made.",
            "No cloud round trip. The model lives here.",
        ],
        action=act_instinct,
        verify=v_chat,
        reset=reset_new_chat,
    ),
    Beat(
        name="deliberation",
        narration=[
            "Tier three: deliberation.",
            "A request with steps in it gets a plan, tools, and a thinking "
            "trail you can read.",
            "",
            "Real deliberation takes real seconds. We're not speeding the "
            "tape up.",
            "",
            "And notice what it did not do. I mentioned an image just now.",
            "It didn't reach for the image generator.",
            "Knowing when not to use a tool is the hard part.",
        ],
        action=act_deliberate,
        verify=v_chat,
        reset=reset_new_chat,
    ),
    Beat(
        name="slash",
        narration=[
            "Every power feature is one keystroke away.",
            "Slash.",
            "",
            "Imagine. Agent. Voice. Model.",
            "And custom commands you define yourself, in plain English, "
            "over in Rules.",
        ],
        action=act_slash,
        verify=v_chat,
        reset=reset_same_chat,
    ),
    Beat(
        name="imagine",
        narration=[
            "Slash imagine: a stone lighthouse at dawn.",
            "",
            "The image studio answers right in the thread. Same card. "
            "Same machine.",
            "",
            "Episode five goes deep on this.",
        ],
        action=act_imagine,
        verify=v_chat,
        reset=reset_imagine,
    ),
    Beat(
        name="floating",
        narration=[
            "Chat isn't a page you visit. It's a card you carry.",
            "",
            "Here's the video studio. And the same chat, floating over it.",
            "It knows which page you're on, too.",
        ],
        action=act_floating,
        verify=v_floating,
        reset=reset_floating,
    ),
    Beat(
        name="narrate",
        narration=[
            "Any reply can be read aloud.",
            "One click, and a local voice reads it back.",
            "",
            "That voice never leaves this machine either. Episode seven "
            "builds one from scratch.",
        ],
        action=act_narrate,
        verify=v_chat,
        reset=reset_narrate,
    ),
    Beat(
        name="lessons",
        narration=[
            "You can teach it.",
            "Begin a lesson. Ask. And when an answer gets it right, "
            "thumbs up. That's a pearl.",
            "",
            "End the lesson, and it distills the pearls into a summary "
            "you can edit.",
            "Future conversations read it back. You taught it something, "
            "once, in plain English.",
        ],
        action=act_lessons,
        verify=v_chat,
        reset=reset_lessons,
    ),
    Beat(
        name="closer",
        narration=[
            "One more thing.",
            "Hand the chat a file, and it's indexed before you've finished "
            "typing your question.",
            "",
            "So. Where do files actually live in Guaardvark?",
            "Next episode: your files get a desktop.",
            "",
            "One machine. No cloud.",
        ],
        action=act_closer,
        verify=v_chat,
        reset=reset_lessons,
    ),
]


def main():
    ep = Episode("ep02_brain", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=60_000)
        stage.page.wait_for_timeout(2000)
        for warm in ("/chat", "/video", "/chat"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP02 COMPLETE: {final}")
    finally:
        subprocess.run(["pkill", "-9", "-f", "vlc"], check=False)
        stage.close()


if __name__ == "__main__":
    main()
