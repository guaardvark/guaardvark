"""grade_draft_externally says whether the grader's answers actually came back.

``checked`` is True only when the grader model answered its questions; a missing
model or a failed call is unchecked (and still ``skipped``, for older readers).
The Ollama HTTP call and the model lookup are stand-ins.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.services.social_outreach import external_grader


def _answer(content):
    def post(url, json=None, timeout=None):
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"message": {"content": content}})
    return post


@pytest.fixture(autouse=True)
def _no_model_info():
    with patch("backend.utils.ollama_resource_manager.get_model_info", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _drafter_unknown():
    """No drafter lookup reaches Ollama; tests that compare models set one."""
    with patch.object(external_grader, "_drafter_model", return_value=None):
        yield


def test_a_returned_answer_is_checked_and_graded_in_code():
    """The model's own grade (0.75) and concise (0) are not read: a one-word
    draft is concise, so all four items hold and the grade is 1.0."""
    reply = '{"grade": 0.75, "engages": 1, "on_topic": 1, "appropriate_tone": 1, "concise": 0, "reason": "ok"}'
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(reply)):
        result = external_grader.grade_draft_externally("draft", "thread")

    assert result["checked"] is True
    assert result["skipped"] is False
    assert result["passed"] is True
    assert result["concise"] == 1
    assert result["grade"] == pytest.approx(1.0)


def test_no_grader_model_is_unchecked():
    with patch.object(external_grader, "_resolve_grader_model", return_value=None):
        result = external_grader.grade_draft_externally("draft", "thread")

    assert result["checked"] is False
    assert result["skipped"] is True
    assert result["reason"] == "no_grader_model_loaded"


def _refused(url, json=None, timeout=None):
    raise ConnectionError("refused")


@pytest.mark.parametrize("post", [_answer("not json at all"), _refused])
def test_a_failed_call_is_unchecked(post):
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", post):
        result = external_grader.grade_draft_externally("draft", "thread")

    assert result["checked"] is False
    assert result["skipped"] is True
    assert result["reason"].startswith("grader_call_failed")


# ---- rubric items are read as booleans --------------------------------------------------

def _grade_with_engages(engages_json):
    reply = ('{"grade": 0.75, "engages": %s, "on_topic": 1, "appropriate_tone": 1, '
             '"concise": 1, "reason": "ok"}' % engages_json)
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(reply)):
        return external_grader.grade_draft_externally("draft", "thread")


@pytest.mark.parametrize("engages_json, bit", [
    ('"yes"', 1), ('"true"', 1), ("1", 1), ('"1"', 1), ("true", 1),
    ('"no"', 0), ('"false"', 0), ("0", 0), ('"0"', 0), ("false", 0),
])
def test_a_rubric_item_reads_true_yes_1_and_false_no_0(engages_json, bit):
    result = _grade_with_engages(engages_json)

    assert result["checked"] is True
    assert result["engages"] == bit


@pytest.mark.parametrize("engages_json", ['"maybe"', "2", "null", "[1]", '"sometimes"'])
def test_an_odd_rubric_answer_leaves_the_draft_unchecked(engages_json):
    result = _grade_with_engages(engages_json)

    assert result["checked"] is False
    assert result["skipped"] is True
    assert result["reason"] == "grader_reply_unparsed"


def test_a_missing_rubric_item_leaves_the_draft_unchecked():
    reply = '{"grade": 0.75, "on_topic": 1, "appropriate_tone": 1, "concise": 1, "reason": "ok"}'
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(reply)):
        result = external_grader.grade_draft_externally("draft", "thread")

    assert result["checked"] is False
    assert result["reason"] == "grader_reply_unparsed"


# ---- passing takes all three questions; the model's own total does not count -----------

def _grade(reply, draft="draft"):
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(reply)):
        return external_grader.grade_draft_externally(draft, "thread")


def test_a_high_model_grade_does_not_pass_a_draft_that_does_not_engage():
    result = _grade('{"grade": 0.75, "engages": 0, "on_topic": 0, "appropriate_tone": 1, '
                    '"concise": 1, "reason": "generic"}')

    assert result["checked"] is True
    assert result["passed"] is False
    assert result["grade"] == pytest.approx(0.5)


@pytest.mark.parametrize("failing", ["engages", "on_topic", "appropriate_tone"])
def test_any_one_question_failing_fails_the_draft(failing):
    answers = {"engages": 1, "on_topic": 1, "appropriate_tone": 1, failing: 0}
    reply = ('{"grade": 1.0, "engages": %(engages)d, "on_topic": %(on_topic)d, '
             '"appropriate_tone": %(appropriate_tone)d, "concise": 1, "reason": "r"}' % answers)

    result = _grade(reply)

    assert result["passed"] is False
    assert result["grade"] == pytest.approx(0.75)


@pytest.mark.parametrize("words, concise", [(119, 1), (120, 1), (121, 0)])
def test_concise_is_counted_from_the_draft(words, concise):
    """The model says concise for every length here; only the word count decides."""
    reply = '{"grade": 1.0, "engages": 1, "on_topic": 1, "appropriate_tone": 1, "concise": 1, "reason": "r"}'

    result = _grade(reply, draft=" ".join(["word"] * words))

    assert result["concise"] == concise
    assert result["grade"] == pytest.approx((3 + concise) / 4)
    assert result["passed"] is True


# ---- the prompt asks only for what is read ----------------------------------------------

def test_the_prompt_asks_only_for_the_three_answers():
    prompt = external_grader.GRADER_SYSTEM
    for question in external_grader.RUBRIC_QUESTIONS:
        assert f"{question}:" in prompt
    assert "concise" not in prompt
    # The example shows the shape with placeholders, not answers to copy.
    example = prompt[prompt.index("{"):prompt.rindex("}") + 1]
    assert set(re.findall(r'"(\w+)":', example)) == {*external_grader.RUBRIC_QUESTIONS, "reason"}


def test_a_reply_in_the_asked_shape_is_checked():
    result = _grade('{"engages": 1, "on_topic": 1, "appropriate_tone": 1, "reason": "fits"}')

    assert result["checked"] is True
    assert result["passed"] is True
    assert result["grade"] == pytest.approx(1.0)


# ---- the thread-fit judge's grade --------------------------------------------------------

def _fit(reply):
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(json.dumps(reply))):
        return external_grader.score_thread_relevance("title", "body", [], "rag")


@pytest.mark.parametrize("grade", ["high", [0.9], {"value": 0.9}, True, "nan", float("inf")])
def test_a_grade_that_is_not_a_number_is_no_verdict(grade):
    """The judge's reply came back but its grade is not a number: handled like
    a failed call (skipped, no verdict), never raised and never a pass."""
    from backend.services.social_outreach.recon import judged_unfit

    result = _fit({"grade": grade, "verdict": "good_fit", "reason": "r"})

    assert result["skipped"] is True
    assert result["grade"] == 0.0
    assert result["reason"] == "relevance_reply_unparsed"
    assert judged_unfit(result) is False


@pytest.mark.parametrize("grade, expected", [(0.8, 0.8), ("0.8", 0.8), (1.7, 1.0), (-1, 0.0), (None, 0.0)])
def test_a_numeric_grade_is_read_and_clamped(grade, expected):
    result = _fit({"grade": grade, "verdict": "good_fit", "reason": "r"})

    assert result["skipped"] is False
    assert result["grade"] == pytest.approx(expected)


def test_a_verdict_or_reason_that_is_not_text_does_not_raise():
    result = _fit({"grade": 0.2, "verdict": 0, "reason": {"why": "rant"}})

    assert result["skipped"] is False
    assert result["verdict"] == ""
    assert "rant" in result["reason"]


# ---- a reply on the account's own video --------------------------------------------------

def _grade_reply(reply, draft="Yes, 12GB is enough."):
    sent = []

    def post(url, json=None, timeout=None):
        sent.append(json)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"message": {"content": reply}})

    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", post):
        result = external_grader.grade_draft_externally(draft, "VIDEO TITLE: t", kind="reply")
    return result, sent


def test_a_reply_is_asked_only_the_reply_questions():
    result, sent = _grade_reply('{"on_topic": 1, "appropriate_tone": 1, "reason": "answers them"}')

    assert result["checked"] is True
    assert result["passed"] is True
    assert result["questions"] == ["on_topic", "appropriate_tone"]
    assert "engages" not in result
    assert result["grade"] == pytest.approx(1.0)
    assert sent[0]["messages"][0]["content"] == external_grader.REPLY_GRADER_SYSTEM


@pytest.mark.parametrize("failing", ["on_topic", "appropriate_tone"])
def test_a_reply_failing_either_question_fails(failing):
    answers = {"on_topic": 1, "appropriate_tone": 1, failing: 0}

    result, _ = _grade_reply(json.dumps({**answers, "reason": "r"}))

    assert result["passed"] is False
    assert result["grade"] == pytest.approx(2 / 3)


def test_engages_is_not_read_for_a_reply():
    result, _ = _grade_reply('{"engages": 0, "on_topic": 1, "appropriate_tone": 1, "reason": "r"}')

    assert result["passed"] is True


def test_the_reply_prompt_asks_only_for_the_two_answers():
    prompt = external_grader.REPLY_GRADER_SYSTEM
    for question in external_grader.REPLY_RUBRIC_QUESTIONS:
        assert f"{question}:" in prompt
    assert "engages" not in prompt
    assert "concise" not in prompt
    assert "grade" not in prompt.replace("grader", "")
    example = json.loads(prompt[prompt.index("{"):prompt.rindex("}") + 1])
    assert set(example) == {*external_grader.REPLY_RUBRIC_QUESTIONS, "reason"}


def test_a_comment_still_reports_its_three_questions():
    result = _grade('{"engages": 1, "on_topic": 1, "appropriate_tone": 1, "reason": "fits"}')

    assert result["questions"] == ["engages", "on_topic", "appropriate_tone"]


def test_an_unknown_rubric_kind_is_refused():
    with pytest.raises(ValueError):
        external_grader.grade_draft_externally("draft", "thread", kind="share")


# ---- whether the grade is independent of the drafter -------------------------------------

DRAFTER_ALIAS = "llamacpp:" + "2cf98652fdc6" + "a" * 52
GEMMA4 = {"family": "gemma4"}
TAGS = [
    {"name": "gemma4:e2b", "digest": "2cf98652fdc6" + "a" * 52, "details": GEMMA4},
    {"name": "gemma4:e2b", "digest": "da406dc37ca0" + "b" * 52, "details": GEMMA4},
    {"name": DRAFTER_ALIAS, "digest": "2cf98652fdc6" + "a" * 52, "details": GEMMA4},
    {"name": "gemma4:12b", "digest": "4eb23ef187e2" + "c" * 52, "details": GEMMA4},
    {"name": "qwen3:14b", "digest": "bdbd181c33f2" + "1" * 52, "details": {"family": "qwen3"}},
    {"name": "nofamily:1b", "digest": "0123456789ab" + "2" * 52},
]
PASSING = '{"engages": 1, "on_topic": 1, "appropriate_tone": 1, "reason": "fits"}'


@pytest.mark.parametrize("drafter, independent", [
    (DRAFTER_ALIAS, False),        # another name for the grader's own build
    ("gemma4:e2b", False),
    ("gemma4:12b", False),         # another build of the grader's family
    ("qwen3:14b", True),
    ("nofamily:1b", None),         # its family is not reported, so cannot be told
    ("not-installed:1b", None),    # cannot be told
    (None, None),
])
def test_a_grade_from_the_drafters_own_build_is_not_independent(drafter, independent):
    with patch.object(external_grader, "_drafter_model", return_value=drafter), \
            patch("backend.utils.ollama_resource_manager._ollama_tags", return_value=TAGS):
        result = _grade(PASSING)

    assert result["checked"] is True
    assert result["passed"] is True
    assert result["drafter_model"] == drafter
    assert result["independent"] is independent


def test_an_unchecked_grade_does_not_look_up_the_drafter():
    with patch.object(external_grader, "_drafter_model") as drafter:
        result = _grade("not json at all")

    assert result["checked"] is False
    drafter.assert_not_called()
