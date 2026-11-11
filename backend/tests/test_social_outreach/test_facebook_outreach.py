"""Facebook comment poster: addresses, refusals and what it types (no browser).

The BiDi reads are replaced by a scripted page and the screen by a recorder, so
these check the poster's decisions: which post it holds the browser to, when it
refuses, and that nothing but the approved text is ever submitted.
"""

from __future__ import annotations

import sys
import types

import pytest

from backend.services.social_outreach import facebook_outreach as fb
from backend.services.social_outreach import reddit_outreach

REEL = "https://www.facebook.com/reel/2412479772578341"


class TestAddresses:
    def test_mobile_and_short_hosts_map_to_www(self):
        assert fb.canonical_post_url("http://m.facebook.com/reel/1?s=2") == "https://www.facebook.com/reel/1?s=2"
        assert fb.canonical_post_url("https://fb.com/reel/1") == "https://www.facebook.com/reel/1"

    def test_other_hosts_are_left_alone(self):
        assert fb.canonical_post_url("https://x.com/a/status/1") == "https://x.com/a/status/1"

    def test_same_post_ignores_tracking_and_trailing_slash(self):
        assert fb.same_post(REEL + "/?__cft__=abc", REEL)
        assert fb.same_post("https://m.facebook.com/reel/2412479772578341", REEL)

    def test_another_reel_is_another_post(self):
        assert not fb.same_post("https://www.facebook.com/reel/881737375012801", REEL)

    def test_post_id_in_the_query_string_counts(self):
        photo = "https://www.facebook.com/photo/?fbid=122121798272192419&set=pb.1.-2"
        assert fb.same_post(photo, "https://www.facebook.com/photo/?fbid=122121798272192419&set=a.9")
        assert not fb.same_post(photo, "https://www.facebook.com/photo/?fbid=122105855024192419&set=pb.1.-2")


@pytest.fixture
def page(monkeypatch):
    """A scripted Facebook page and a recording screen; returns the shared state."""
    state = {
        "log": [],
        "url": REEL,
        "box": {"count": 1, "actor": "guaardvark", "x": 40, "y": 50, "text": ""},
        "box_text": "",
        "focus": True,
        "verify": (True, {"mine": True, "held": False}, ""),
        "typed_override": None,
    }
    log = state["log"]

    class Screen:
        def click(self, x, y):
            log.append(f"click {x},{y}")

        def type_text(self, text, key_delays=None):
            log.append(f"type {text}")
            state["box_text"] += text

        def hotkey(self, *keys):
            log.append("hotkey " + "+".join(keys))
            if keys == ("shift", "Return"):
                state["box_text"] += "\n"
            elif keys == ("BackSpace",):
                state["box_text"] = ""

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
    monkeypatch.setattr(reddit_outreach, "bidi_reachable", lambda *a, **k: (True, ""))
    monkeypatch.delenv(fb.POST_AS_ENV, raising=False)

    def focused(*args, **kwargs):
        if not state["focus"]:
            return None
        text = state["box_text"]
        if state["typed_override"] is not None and any(e.startswith("type ") for e in log):
            text = state["typed_override"]
        return {"editable": True, "tag": "div", "label": "Comment as guaardvark", "text": text}

    monkeypatch.setattr(fb, "_location", lambda: state["url"])
    monkeypatch.setattr(fb, "_find_composer", lambda *a, **k: (state["box"], "") if state["box"] else (None, "composer_not_found: x"))
    monkeypatch.setattr(fb, "_composer_focused", focused)
    monkeypatch.setattr(fb, "_wait_for_focus", focused)
    monkeypatch.setattr(fb, "_verify", lambda text, actor: state["verify"])
    return state


def _published(log):
    return [e for e in log if e == "hotkey Return"]


def test_posts_and_types_only_the_approved_text(page):
    assert fb.post_comment_via_bidi(REEL, "hello there") == (True, "ok")
    assert page["log"] == ["click 40,50", "type hello there", "hotkey Return"]


def test_lines_are_joined_with_shift_enter_never_a_bare_enter(page):
    assert fb.post_comment_via_bidi(REEL, "line one\nline two") == (True, "ok")
    assert page["log"][1:4] == ["type line one", "hotkey shift+Return", "type line two"]
    assert page["log"].index("hotkey Return") == len(page["log"]) - 1


def test_a_box_posting_as_someone_else_is_refused_before_anything_is_typed(page, monkeypatch):
    monkeypatch.setenv(fb.POST_AS_ENV, "Guaardvark Page")
    ok, reason = fb.post_comment_via_bidi(REEL, "hello")
    assert not ok and reason.startswith("wrong_identity:")
    assert page["log"] == []


def test_the_configured_account_matches_case_insensitively(page, monkeypatch):
    monkeypatch.setenv(fb.POST_AS_ENV, "GUAARDVARK")
    assert fb.post_comment_via_bidi(REEL, "hello") == (True, "ok")


def test_text_that_did_not_arrive_intact_is_cleared_and_not_posted(page):
    page["typed_override"] = "llo"
    ok, reason = fb.post_comment_via_bidi(REEL, "hello")
    assert not ok and reason.startswith("typed_text_mismatch:")
    assert not _published(page["log"])
    assert page["log"][-2:] == ["hotkey ctrl+a", "hotkey BackSpace"]


def test_leaving_the_post_before_submit_posts_nothing(page, monkeypatch):
    # The reel viewer moved on to the next reel after the text was typed.
    urls = iter([REEL, REEL])
    monkeypatch.setattr(fb, "_location", lambda: next(urls, "https://www.facebook.com/reel/881737375012801"))
    ok, reason = fb.post_comment_via_bidi(REEL, "hello")
    assert not ok and reason.startswith("wrong_page:")
    assert not _published(page["log"])


def test_an_address_that_leads_off_facebook_is_refused(page):
    page["url"] = "https://example.com/landing"
    ok, reason = fb.post_comment_via_bidi(REEL, "hello")
    assert not ok and reason.startswith("wrong_page:")
    assert page["log"] == []


def test_a_box_holding_old_text_is_emptied_before_typing(page):
    page["box_text"] = "an old draft"
    assert fb.post_comment_via_bidi(REEL, "hello") == (True, "ok")
    assert page["log"][1:3] == ["hotkey ctrl+a", "hotkey BackSpace"]


def test_no_comment_box_posts_nothing(page):
    page["box"] = None
    ok, reason = fb.post_comment_via_bidi(REEL, "hello")
    assert not ok and reason.startswith("composer_not_found")
    assert page["log"] == []


def test_an_emptied_box_without_the_comment_is_unconfirmed(page):
    page["verify"] = (False, {"mine": False, "anyone": False, "held": False, "alerts": "", "url": REEL}, "")
    ok, reason = fb.post_comment_via_bidi(REEL, "hello")
    assert not ok and reason.startswith("submitted_unconfirmed: may be live")


def test_a_box_still_holding_the_text_is_a_failed_submit(page):
    page["verify"] = (False, {"mine": False, "anyone": False, "held": True, "alerts": "", "url": REEL}, "")
    ok, reason = fb.post_comment_via_bidi(REEL, "hello")
    assert not ok and reason.startswith("submit_failed:")


class TestFindComposer:
    def _script(self, monkeypatch, readings):
        seq = iter(readings)
        monkeypatch.setattr("time.sleep", lambda seconds: None)
        monkeypatch.setattr(fb, "_read", lambda expression: next(seq))

    def test_two_boxes_are_ambiguous(self, monkeypatch):
        self._script(monkeypatch, [({"count": 2, "url": REEL}, "")])
        box, reason = fb._find_composer(wait_s=0)
        assert box is None and reason.startswith("composer_ambiguous:")

    def test_a_closed_panel_is_opened_once(self, monkeypatch):
        seen = []
        readings = iter([({"count": 0}, ""), ({"clicked": True}, ""), ({"count": 1, "actor": "a"}, "")])

        def read(expression):
            seen.append(expression)
            return next(readings)

        monkeypatch.setattr("time.sleep", lambda seconds: None)
        monkeypatch.setattr(fb, "_read", read)
        box, reason = fb._find_composer(wait_s=5)
        assert box == {"count": 1, "actor": "a"} and reason == ""
        assert seen.count(fb._OPEN_COMMENTS_JS) == 1

    def test_a_login_wall_reads_as_logged_out(self, monkeypatch):
        self._script(monkeypatch, [({"count": 0, "login_wall": True}, ""), ({"clicked": False}, "")])
        box, reason = fb._find_composer(wait_s=0)
        assert box is None and reason.startswith("logged_out:facebook")
