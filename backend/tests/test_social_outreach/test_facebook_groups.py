"""Addresses, identity and the voice sheet for Facebook group outreach."""

import pytest

from backend.services.social_outreach import facebook_outreach as fb
from backend.services.social_outreach import voice


@pytest.mark.parametrize("url, expected", [
    ("https://www.facebook.com/groups/123/posts/456/", ("123", "456")),
    ("https://www.facebook.com/groups/localai/permalink/456/?comment_id=9", ("localai", "456")),
    ("https://www.facebook.com/groups/123/", None),
    ("https://www.facebook.com/somepage/posts/456", None),
])
def test_group_post_ref(url, expected):
    assert fb.group_post_ref(url) == expected


@pytest.mark.parametrize("text, expected", [
    ("https://www.facebook.com/groups/localai/", "localai"),
    ("https://www.facebook.com/groups/123/posts/456/", "123"),
    ("1280677296824475", "1280677296824475"),
    ("https://www.facebook.com/groups/joins/", None),
    ("https://www.facebook.com/somepage", None),
])
def test_group_ref(text, expected):
    assert fb.group_ref(text) == expected


def test_a_group_post_asks_for_the_group_identity_first(monkeypatch):
    group_post = "https://www.facebook.com/groups/123/posts/456/"
    page_post = "https://www.facebook.com/somepage/posts/456"
    monkeypatch.setenv(fb.POST_AS_ENV, "A Page")
    monkeypatch.delenv(fb.GROUP_POST_AS_ENV, raising=False)
    assert fb.wanted_identity(group_post) == ("A Page", fb.POST_AS_ENV)
    monkeypatch.setenv(fb.GROUP_POST_AS_ENV, "Someone")
    assert fb.wanted_identity(group_post) == ("Someone", fb.GROUP_POST_AS_ENV)
    assert fb.wanted_identity(page_post) == ("A Page", fb.POST_AS_ENV)


def test_answer_boxes_count_as_top_level():
    assert fb._TOP_LEVEL_LABEL.search("Answer as Someone")
    assert fb._TOP_LEVEL_LABEL.search("Comment as A Page")
    assert not fb._TOP_LEVEL_LABEL.search("Reply to Someone")


def test_a_platform_sheet_replaces_the_general_one(tmp_path, monkeypatch):
    general = tmp_path / "VOICE.md"
    general.write_text("Write short.\n\n## Never\nrobust\n")
    monkeypatch.setattr(voice, "VOICE_PATH", general)
    voice._cache.clear()
    assert voice.hits("a robust thing", "facebook") == ["robust"]
    (tmp_path / "VOICE.facebook.md").write_text("Write shorter.\n\n## Never\nseamless\n")
    assert voice.sheet_path("facebook").name == "VOICE.facebook.md"
    assert voice.sheet_path("reddit").name == "VOICE.md"
    assert voice.hits("a robust and seamless thing", "facebook") == ["seamless"]
    assert voice.hits("a robust and seamless thing", "reddit") == ["robust"]
    assert "Write shorter." in voice.block(platform="facebook")
