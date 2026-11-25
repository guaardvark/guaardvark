"""Every poster asks ``before_submit`` immediately before it publishes.

The posting tick passes ``transitions.submit_gate(row)`` as ``before_submit``;
a draft rejected while a poster is working is stopped there. These tests run
each real poster with a recording screen and agent service (no browser, no
display, no network) and check where the question is asked and that a "no"
is obeyed.
"""

from __future__ import annotations

import sys
import types

import pytest

from backend.services.social_outreach import (
    facebook_outreach,
    general_poster,
    reddit_outreach,
    self_share,
    youtube_outreach,
)
from backend.services.social_outreach.transitions import WITHDRAWN_BEFORE_SUBMIT

YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"
FACEBOOK_URL = "https://www.facebook.com/reel/2412479772578341"


@pytest.fixture
def events(monkeypatch):
    """Replace the servo layer with recorders; return what they recorded."""
    log: list[str] = []

    class Screen:
        def click(self, x, y):
            log.append("click")

        def type_text(self, text, key_delays=None):
            log.append(f"type {text}")

        def hotkey(self, *keys):
            log.append("hotkey " + "+".join(keys))

    class Result:
        success = True
        reason = "ok"

    class Service:
        is_active = False

        def execute_task(self, task, screen):
            log.append("task " + task)
            return Result()

    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)

    def refuse_connection(*args, **kwargs):
        raise OSError("no browser in tests")

    module("backend.services.agent_control_service", get_agent_control_service=lambda: Service())
    module("backend.services.local_screen_backend", LocalScreenBackend=Screen)
    module("backend.utils.agent_display_utils", start_agent_display_if_needed=lambda: True)
    module("websocket", create_connection=refuse_connection)

    def recipe_step(service, screen, chat_message, failure_tag):
        log.append("recipe " + chat_message)
        return True, "ok"

    def fill_and_submit(text):
        log.append("fill and click Comment")
        return True, "submitted"

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr(reddit_outreach, "_bidi_navigate", lambda *a, **k: True)
    monkeypatch.setattr(reddit_outreach, "_bidi_scroll_to_composer", lambda: (True, "ok", (40, 50)))

    def focused_editable():
        typed = [e[len("type "):] for e in log if e.startswith("type ")]
        return {"editable": True, "tag": "div", "label": "", "text": typed[-1] if typed else ""}

    monkeypatch.setattr(reddit_outreach, "_focused_editable", focused_editable)
    monkeypatch.setattr(youtube_outreach, "_bidi_navigate", lambda *a, **k: True)
    monkeypatch.setattr(youtube_outreach, "_bidi_scroll_to_yt_composer", lambda: (True, "ok", (1, 1)))
    monkeypatch.setattr(youtube_outreach, "_bidi_fill_and_submit_comment", fill_and_submit)
    monkeypatch.setattr(youtube_outreach, "_verify_youtube_text_in_dom", lambda text: (True, "ok"))
    monkeypatch.setattr(youtube_outreach, "_run_recipe_step", recipe_step)
    monkeypatch.setattr(general_poster, "_preflight_logged_in", lambda platform: (True, "ok"))
    monkeypatch.setattr(general_poster, "_still_on_target", lambda url: (True, url))
    monkeypatch.setattr(reddit_outreach, "bidi_reachable", lambda *a, **k: (True, ""))
    monkeypatch.setattr(self_share, "bidi_reachable", lambda *a, **k: (True, ""))
    monkeypatch.setattr(self_share, "_bidi_navigate", lambda *a, **k: True)
    monkeypatch.setattr(self_share, "_read_form", lambda: ({
        "url": "https://www.reddit.com/r/SideProject/submit/?type=LINK", "kind": "LINK",
        "title": "a title", "link": "https://guaardvark.com",
        "button": {"disabled": False, "shown": True, "x": 600, "y": 900}}, ""))
    monkeypatch.setattr(self_share, "_post_landed", lambda *a, **k: (True, "posted"))

    def facebook_box(*args, **kwargs):
        box = focused_editable()
        box["label"] = "Comment as guaardvark"
        return box

    monkeypatch.delenv(facebook_outreach.POST_AS_ENV, raising=False)
    monkeypatch.setattr(facebook_outreach, "_location", lambda: FACEBOOK_URL)
    monkeypatch.setattr(facebook_outreach, "_find_composer", lambda *a, **k: (
        {"count": 1, "actor": "guaardvark", "x": 40, "y": 50, "text": ""}, ""))
    monkeypatch.setattr(facebook_outreach, "_composer_focused", facebook_box)
    monkeypatch.setattr(facebook_outreach, "_wait_for_focus", facebook_box)
    monkeypatch.setattr(facebook_outreach, "_verify", lambda text, actor: (True, {"mine": True}, ""))
    return log


# name -> (call taking the gate, prefix of the recorded publishing action)
POSTERS = {
    "reddit_comment": (
        lambda gate: reddit_outreach.post_comment_via_servo(
            "https://www.reddit.com/r/x/comments/a/t/", "hello", before_submit=gate),
        "hotkey ctrl+Return",
    ),
    "youtube_comment": (
        lambda gate: youtube_outreach.post_youtube_comment_via_servo(
            YOUTUBE_URL, "hello", 7, before_submit=gate),
        "fill and click Comment",
    ),
    "youtube_reply": (
        lambda gate: youtube_outreach.post_youtube_reply_via_servo(
            YOUTUBE_URL, "a parent comment long enough", "hello", 7, before_submit=gate),
        "recipe send the comment",
    ),
    "facebook_comment": (
        lambda gate: facebook_outreach.post_comment_via_bidi(FACEBOOK_URL, "hello", before_submit=gate),
        "hotkey Return",
    ),
    "agent_loop": (
        lambda gate: general_poster.post_via_agent_loop(
            "twitter", "https://x.com/a/status/1", "hello", action="comment", before_submit=gate),
        "task On this page, click the button that publishes/submits",
    ),
    "reddit_share": (
        lambda gate: self_share._submit_post_via_servo(
            "SideProject", "a title", "https://guaardvark.com", before_submit=gate),
        "click",
    ),
}


@pytest.mark.parametrize("name", sorted(POSTERS))
def test_poster_publishes_nothing_when_the_gate_says_no(events, name):
    call, publishing_action = POSTERS[name]

    def gate():
        events.append("gate")
        return False

    assert call(gate) == (False, WITHDRAWN_BEFORE_SUBMIT)
    assert events[-1] == "gate"
    assert not [e for e in events if e.startswith(publishing_action)]


@pytest.mark.parametrize("name", sorted(POSTERS))
def test_poster_asks_the_gate_right_before_publishing(events, name):
    call, publishing_action = POSTERS[name]

    def gate():
        events.append("gate")
        return True

    call(gate)

    assert events.count("gate") == 1
    asked = events.index("gate")
    assert events[asked + 1].startswith(publishing_action)
    # Nothing that publishes ran before the question.
    assert not [e for e in events[:asked] if e.startswith(publishing_action)]


@pytest.mark.parametrize("name", sorted(POSTERS))
def test_poster_without_a_gate_still_publishes(events, name):
    call, publishing_action = POSTERS[name]

    call(None)

    assert [e for e in events if e.startswith(publishing_action)]
