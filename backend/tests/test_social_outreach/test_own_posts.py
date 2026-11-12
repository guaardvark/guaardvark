"""Posts of the account's own with an infographic: who may post, which image
is chosen, what the chat hears, and what a new infographic may say."""

import pytest

from backend.services.social_outreach import facebook_outreach as fb
from backend.services.social_outreach import infographic_maker, intent, own_posts, persona, self_share


@pytest.mark.parametrize("actor, wanted, allowed", [
    ("Someone", "", True),
    ("Someone", "someone", True),
    ("A Page", "Someone, A Page", True),
    ("a page", "Someone,A Page", True),
    ("Stranger", "Someone, A Page", False),
    ("", "Someone", False),
])
def test_either_named_account_may_post(actor, wanted, allowed):
    assert fb.posts_as_allowed(actor, wanted) is allowed


def test_the_least_used_infographic_not_yet_posted_there_is_chosen():
    usable = [{"document_id": 1}, {"document_id": 2}, {"document_id": 3}]
    assert own_posts.choose(usable, used_here={1}, uses={2: 4, 3: 1})["document_id"] == 3
    assert own_posts.choose(usable, used_here=set(), uses={})["document_id"] == 1
    assert own_posts.choose(usable, used_here={1, 2, 3}, uses={}) is None


@pytest.mark.parametrize("text, wanted", [
    ("post an infographic in my Facebook groups", {"intent": "own_posts", "platform": "facebook", "communities": []}),
    ("share infographics on r/SideProject", {"intent": "own_posts", "platform": "reddit", "communities": ["SideProject"]}),
    ("post an infographic in https://www.facebook.com/groups/localai/",
     {"intent": "own_posts", "platform": "facebook", "communities": ["localai"]}),
    ("make an infographic about the Docker install", {"intent": "make_infographic", "topic": "the Docker install"}),
    ("create an infographic", {"intent": "make_infographic", "topic": ""}),
])
def test_infographic_requests_are_settled_by_their_words(text, wanted):
    heard = intent._typed_request(text)
    assert heard is not None
    for key, value in wanted.items():
        assert heard[key] == value


def test_a_plain_link_is_still_a_request_to_reply_to_it():
    heard = intent._typed_request("https://www.facebook.com/groups/1/posts/2/")
    assert heard["intent"] == "draft_url"


def test_the_image_form_is_refused_until_it_holds_the_title_and_one_image():
    ready = {
        "url": "https://www.reddit.com/r/test/submit/?type=IMAGE&title=A+title",
        "title": "A title", "has_input": True, "attached": 1,
        "button": {"disabled": False, "shown": True, "x": 1, "y": 1},
    }
    assert self_share._image_form_problem(ready, "test", "A title", 1) == ""
    assert "images" in self_share._image_form_problem({**ready, "attached": 2}, "test", "A title", 1)
    assert "title" in self_share._image_form_problem({**ready, "title": "Other"}, "test", "A title", 1)
    assert "takes no image" in self_share._image_form_problem({**ready, "has_input": False}, "test", "A title", 0)
    assert "not r/other" in self_share._image_form_problem(ready, "other", "A title", 1)
    assert "Post button" in self_share._image_form_problem(
        {**ready, "button": {"disabled": True, "shown": True}}, "test", "A title", 1,
    )


SHEET = (
    "The product is free and open source under the MIT licence.\n"
    "It runs on your own PC with no cloud API keys.\n"
    "Models install from inside the app with the Install button.\n"
)


def test_a_line_is_drawn_only_when_the_pitch_sheet_backs_it(monkeypatch):
    monkeypatch.setattr(persona, "_pitch_sheet", lambda: SHEET)

    def llm(_system, _user):
        return {
            "headline": {"text": "Free and open source", "backing": "The product is free and open source under the MIT licence."},
            "labels": [
                {"text": "Runs on your PC", "backing": "It runs on your own PC with no cloud API keys."},
                {"text": "One-click model installs", "backing": "Models install from inside the app with the Install button."},
                {"text": "Ten times faster", "backing": "It is ten times faster than anything else."},
                {"text": "Free", "backing": "free"},
            ],
            "scene": "a desk at night",
        }

    spec = infographic_maker.write_spec("what it is", llm)
    assert spec["ok"]
    assert spec["title"] == "Free and open source"
    assert spec["callouts"] == ["Runs on your PC", "One-click model installs"]
    assert spec["dropped"] == ["Ten times faster", "Free"]
    assert set(spec["backing"]) == {"Free and open source", "Runs on your PC", "One-click model installs"}


def test_no_infographic_is_written_without_enough_backed_lines(monkeypatch):
    monkeypatch.setattr(persona, "_pitch_sheet", lambda: SHEET)

    def llm(_system, _user):
        return {
            "headline": {"text": "The best studio", "backing": "It is the best studio there is."},
            "labels": [{"text": "Runs on your PC", "backing": "It runs on your own PC with no cloud API keys."}],
        }

    assert infographic_maker.write_spec("anything", llm)["ok"] is False


def test_the_words_for_a_post_end_with_the_link_except_in_a_reddit_title(monkeypatch):
    monkeypatch.setattr(persona, "_pitch_sheet", lambda: SHEET)

    def llm(_system, _user):
        return {"draft": "Made this to show the\nDocker install.", "grade": 0.9, "reason": "fits"}

    context = {"community": "A group", "infographic": {"headline": "ONE COMMAND", "lines": ["NO SETUP"]}}
    on_facebook = persona.draft_outreach_text(
        "facebook", context, mode="post", llm=llm, link_url="https://example.org",
    )["draft"]
    assert on_facebook.startswith("Made this to show the")
    assert "example.org" in on_facebook
    title = persona.draft_outreach_text("reddit", context, mode="post", llm=llm)["draft"]
    assert title == "Made this to show the Docker install."
