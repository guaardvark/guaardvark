"""The Reddit comment poster submits only the approved text.

It waits for the composer to take focus before typing (keystrokes sent while
Reddit expands the editor are lost), reads the composer back before Ctrl+Enter
and empties it on any difference, and reports a submit whose comment is not yet
on the page as possibly live rather than failed.
"""

from __future__ import annotations

import sys
import types

import pytest

from backend.services.social_outreach import reddit_outreach

PERMALINK = "https://www.reddit.com/r/x/comments/abc/t/"
TEXT = "The Matryoshka part is a huge win for local RAG."


@pytest.fixture
def rig(monkeypatch):
    """A recording screen and a page whose focus and thread the test sets."""
    state = {"log": [], "focus": {"editable": True, "tag": "div", "label": ""},
             "drop": 0, "clears": True, "thread": {"foundInThread": True, "composerEmpty": True, "errorVisible": False}}

    class Screen:
        def click(self, x, y):
            state["log"].append("click")

        def type_text(self, text, key_delays=None):
            state["log"].append("type")
            state["focus"] = {**state["focus"], "text": text[state["drop"]:]}

        def hotkey(self, *keys):
            state["log"].append("hotkey " + "+".join(keys))
            if keys == ("BackSpace",) and state["clears"]:
                state["focus"] = {**state["focus"], "text": ""}

    class Service:
        is_active = False

    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)

    module("backend.services.agent_control_service", get_agent_control_service=lambda: Service())
    module("backend.services.local_screen_backend", LocalScreenBackend=Screen)
    module("backend.utils.agent_display_utils", start_agent_display_if_needed=lambda: True)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr(reddit_outreach, "_bidi_navigate", lambda *a, **k: True)
    monkeypatch.setattr(reddit_outreach, "_bidi_scroll_to_composer", lambda: (True, "ok", (40, 50)))
    monkeypatch.setattr(reddit_outreach, "_focused_editable", lambda: state["focus"])
    monkeypatch.setattr(reddit_outreach, "bidi_evaluate_json", lambda js: (state["thread"], ""))
    return state


def test_matching_text_is_submitted(rig):
    assert reddit_outreach.post_comment_via_servo(PERMALINK, TEXT) == (True, "ok")
    assert "hotkey ctrl+Return" in rig["log"]


def test_lost_opening_words_are_never_submitted(rig):
    rig["drop"] = 19
    ok, reason = reddit_outreach.post_comment_via_servo(PERMALINK, TEXT)
    assert ok is False
    assert reason.startswith("typed_text_mismatch: nothing was posted")
    assert "hotkey ctrl+Return" not in rig["log"]
    assert rig["log"][-2:] == ["hotkey ctrl+a", "hotkey BackSpace"]


def test_unreadable_composer_is_never_submitted(rig, monkeypatch):
    # Readable while the poster focuses the box, unreadable once it has typed.
    monkeypatch.setattr(reddit_outreach, "_focused_editable",
                        lambda: None if "type" in rig["log"] else rig["focus"])
    ok, reason = reddit_outreach.post_comment_via_servo(PERMALINK, TEXT)
    assert ok is False
    assert reason.startswith("typed_text_unreadable: nothing was posted")
    assert "hotkey ctrl+Return" not in rig["log"]


def test_focus_in_the_search_bar_types_nothing(rig):
    rig["focus"] = {"editable": True, "tag": "input", "label": "Search Reddit"}
    rig["focus"]["editable"] = True
    ok, reason = reddit_outreach.post_comment_via_servo(PERMALINK, TEXT)
    assert ok is False
    assert reason.startswith("composer_not_focused")
    assert "type" not in rig["log"]


def test_composer_that_never_takes_focus_types_nothing(rig):
    rig["focus"] = {"editable": False, "tag": "body", "label": ""}
    ok, reason = reddit_outreach.post_comment_via_servo(PERMALINK, TEXT)
    assert ok is False
    assert reason.startswith("composer_not_focused")
    assert "type" not in rig["log"]


def test_emptied_composer_without_the_comment_is_possibly_live(rig):
    rig["thread"] = {"foundInThread": False, "composerEmpty": True, "errorVisible": False, "url": PERMALINK}
    ok, reason = reddit_outreach.post_comment_via_servo(PERMALINK, TEXT)
    assert ok is False
    assert reason.startswith("submitted_unconfirmed: may be live")


def test_visible_error_is_a_plain_failure(rig):
    rig["thread"] = {"foundInThread": False, "composerEmpty": False, "errorVisible": True, "url": PERMALINK}
    ok, reason = reddit_outreach.post_comment_via_servo(PERMALINK, TEXT)
    assert ok is False
    assert reason.startswith("submit_failed")


def test_a_restored_draft_is_emptied_before_typing(rig):
    rig["focus"] = {"editable": True, "tag": "div", "label": "", "text": "an unsent comment from before"}
    assert reddit_outreach.post_comment_via_servo(PERMALINK, TEXT) == (True, "ok")
    assert rig["log"][1:5] == ["click", "hotkey ctrl+a", "hotkey BackSpace", "type"]


def test_a_draft_that_will_not_clear_types_nothing(rig):
    rig["focus"] = {"editable": True, "tag": "div", "label": "", "text": "an unsent comment from before"}
    rig["clears"] = False
    ok, reason = reddit_outreach.post_comment_via_servo(PERMALINK, TEXT)
    assert ok is False
    assert reason.startswith("composer_not_empty: nothing was posted")
    assert "type" not in rig["log"]


def test_same_text_ignores_paragraph_breaks():
    assert reddit_outreach._same_text("one two\n\nthree", "one two three")
    assert not reddit_outreach._same_text("esentation Learning", "Representation Learning")
