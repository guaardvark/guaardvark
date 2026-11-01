"""Approved YouTube drafts go through the API only from an account switched on for it."""
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.social_outreach import api_post


def row(**fields):
    base = dict(id=7, status="approved", platform="youtube", action="comment",
                target_url="https://www.youtube.com/watch?v=abc123", draft_text="hello", posted_text=None)
    return SimpleNamespace(**{**base, **fields})


def test_video_id_reads_both_link_shapes():
    assert api_post.video_id("https://www.youtube.com/watch?v=abc123&t=4") == "abc123"
    assert api_post.video_id("https://youtu.be/abc123") == "abc123"
    assert api_post.video_id("https://example.com/") is None


def test_comment_link_names_the_parent():
    assert api_post._linked_comment("https://www.youtube.com/watch?v=a&lc=Ugx1") == "Ugx1"
    assert api_post._linked_comment("https://www.youtube.com/watch?v=a") is None


def test_no_account_leaves_the_draft_to_the_browser():
    with patch.object(api_post, "account", return_value=None):
        assert not api_post.handles(row())
        assert api_post.ready_platforms() == []


def test_other_platforms_never_use_the_api():
    with patch.object(api_post, "account", return_value=object()):
        assert not api_post.handles(row(platform="reddit"))
        assert not api_post.handles(row(action="share"))
        assert api_post.handles(row())


def test_a_refused_draft_is_not_claimed():
    with patch.object(api_post, "_refusal", return_value="stopped"), patch.object(api_post.transitions, "claim") as claim:
        assert api_post.post(row()) == (False, "stopped")
        claim.assert_not_called()
