"""chat_decisions: the chat turn's retrieval questions, with decide() stubbed.

No model, database or network: ``ask`` and ``mode`` are replaced, and the
search is a function that records its calls.
"""
from __future__ import annotations

import threading

import pytest

from backend.services import chat_decisions as cd
from backend.services.nl_control_plane import DecisionResult


def _result(decision, answer, source="llm"):
    return DecisionResult(decision, answer, source, "ok" if source == "llm" else "off")


@pytest.fixture
def modes(monkeypatch):
    table: dict = {}
    monkeypatch.setattr(cd, "mode", lambda d: table.get(d, "off"))
    return table


@pytest.fixture
def answers(monkeypatch):
    """Answers the stubbed decide() gives, by decision; records each question."""
    given: dict = {}
    asked: list = []

    def ask(decision, state, *, fallback, ctx):
        asked.append((decision, state))
        if decision in given:
            value = given[decision]
            return _result(decision, value(state) if callable(value) else value)
        return _result(decision, fallback, source="fallback")

    monkeypatch.setattr(cd, "ask", ask)
    return given, asked


@pytest.fixture
def listing(monkeypatch):
    monkeypatch.setattr(cd, "knowledge_listing", lambda: "budget.md, itinerary.md")


class Search:
    def __init__(self, passages=None):
        self.calls = []
        self.passages = passages or [{"label": "a.md", "source": "a.md", "text": "x", "score": 0.5}]
        self.release = threading.Event()
        self.release.set()

    def __call__(self, message):
        self.calls.append(message)
        self.release.wait(5)
        return {"passages": list(self.passages), "notes": {}, "error": None, "failed": False}


def test_format_listing_cuts_with_a_count():
    names = [f"document_{i:03d}_with_a_long_name.pdf" for i in range(60)]
    text = cd.format_listing(names)
    assert len(text) <= cd.LISTING_MAX_CHARS
    assert text.endswith(" more")
    shown = text.rsplit(" and ", 1)[0].count(", ") + 1
    assert text.endswith(f" and {60 - shown} more")
    assert cd.format_listing(["a.md", "b.md"]) == "a.md, b.md"


def test_with_skip_rag_off_the_rule_decides(modes, answers):
    search = Search()
    found, note = cd.search(search, "what is in my budget?", previous_message="", by_rule=True, ctx={})
    assert search.calls == ["what is in my budget?"] and note is None
    found, note = cd.search(search, "open firefox", previous_message="", by_rule=False, ctx={})
    assert found is None and len(search.calls) == 1


def test_a_no_ends_the_wait_for_the_search(modes, answers, listing):
    modes["skip_rag"] = "on"
    given, asked = answers
    given["skip_rag"] = False
    search = Search()
    search.release.clear()  # the search would hang; a no must not wait for it
    found, note = cd.search(search, "write a haiku", previous_message="", by_rule=True, ctx={})
    search.release.set()
    assert found is None
    assert note["answer"] is False
    assert asked[0][1] == {"message": "write a haiku", "previous_message": "", "documents": "budget.md, itinerary.md"}


def test_a_yes_waits_for_the_search(modes, answers, listing):
    modes["skip_rag"] = "on"
    answers[0]["skip_rag"] = True
    found, _ = cd.search(Search(), "how much did we budget for flights?", previous_message="", by_rule=True, ctx={})
    assert found and found["passages"]


def test_a_yes_unlocks_a_search_the_rule_skipped(modes, answers, listing):
    modes["skip_rag"] = "on"
    answers[0]["skip_rag"] = True
    search = Search()
    found, _ = cd.search(search, "run me through my notes on the trip", previous_message="", by_rule=False, ctx={})
    assert search.calls and found


def test_no_listing_means_no_question(modes, answers, monkeypatch):
    modes["skip_rag"] = "on"
    monkeypatch.setattr(cd, "knowledge_listing", lambda: "")
    search = Search()
    cd.search(search, "anything", previous_message="", by_rule=True, ctx={})
    assert answers[1] == [] and search.calls == ["anything"]


def _passages(*scores):
    return {"passages": [{"label": f"p{i}", "source": f"p{i}", "text": f"text {i}", "score": s}
                         for i, s in enumerate(scores)]}


def test_check_passages_off_keeps_everything(modes, answers):
    found, left = cd.check_passages(_passages(0.4, 0.5), "q", ctx={})
    assert len(found["passages"]) == 2 and left == [] and answers[1] == []


def test_a_no_leaves_the_passage_out(modes, answers):
    modes["context_answers"] = "on"
    answers[0]["context_answers"] = lambda state: state["passage"] != "text 1"
    found, left = cd.check_passages(_passages(0.4, 0.5, 0.6), "q", ctx={})
    assert [p["source"] for p in found["passages"]] == ["p0", "p2"]
    assert left == [{"source": "p1", "score": 0.5}]


def test_a_confident_passage_is_kept_without_asking(modes, answers):
    modes["context_answers"] = "on"
    answers[0]["context_answers"] = False
    found, left = cd.check_passages(_passages(cd.CONFIDENT_RERANK_SCORE, 0.3, None), "q", ctx={})
    assert [p["source"] for p in found["passages"]] == ["p0"]
    assert len(answers[1]) == 2  # the 0.3 passage and the unscored one were asked about


def test_checks_stop_at_the_cap(modes, answers):
    modes["context_answers"] = "on"
    answers[0]["context_answers"] = False
    found, left = cd.check_passages(_passages(*([0.4] * (cd.MAX_PASSAGE_CHECKS + 2))), "q", ctx={})
    assert len(left) == cd.MAX_PASSAGE_CHECKS
    assert len(found["passages"]) == 2


def test_turns_running_counts_turns():
    assert not cd.turns_running()
    cd.turn_started()
    try:
        assert cd.turns_running()
    finally:
        cd.turn_ended()
    assert not cd.turns_running()
