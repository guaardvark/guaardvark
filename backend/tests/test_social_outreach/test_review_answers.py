"""How a chat message is read while an outreach review is open.

Only "post" leads to a post, so the cases that must not read as "post" matter
as much as the ones that do.
"""

import pytest

from backend.services.social_outreach.review import read_answer


@pytest.mark.parametrize("text", [
    "yes", "Yes.", "yes, post it", "Ok post it", "post it please", "approve",
    "go ahead", "looks good", "send it",
])
def test_plain_approval_reads_as_post(text):
    assert read_answer(text) == ("post", "")


@pytest.mark.parametrize("text", [
    "ok", "Okay.", "sure", "yes but change the ending", "post it tomorrow",
    "what's the weather", "I don't know", "", "yes?? or no",
])
def test_anything_less_than_a_plain_approval_is_not_post(text):
    assert read_answer(text)[0] != "post"


@pytest.mark.parametrize("text, kind", [
    ("skip", "skip"), ("no", "skip"), ("next one", "skip"),
    ("reject it", "reject"), ("delete this one", "reject"),
    ("Stop.", "stop"), ("that's all", "stop"), ("stop the review", "stop"),
])
def test_skip_reject_and_stop(text, kind):
    assert read_answer(text) == (kind, "")


def test_change_it_to_carries_the_new_text_as_typed():
    assert read_answer("change it to: Thanks, glad it helped!") == ("edit", "Thanks, glad it helped!")
    assert read_answer('Make it say "hi there"') == ("edit", "hi there")


@pytest.mark.parametrize("text, note", [
    ("make it shorter", "shorter"),
    ("rewrite it, less salesy", "less salesy"),
    ("more casual", "more casual"),
    ("try again", "write a different version"),
])
def test_a_rewrite_request_carries_what_to_change(text, note):
    assert read_answer(text) == ("redraft", note)
