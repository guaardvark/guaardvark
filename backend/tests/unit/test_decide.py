"""decide(): today's answer unless a qualified, loaded model may answer.

The seams are module-level in nl_control_plane: ``_resident`` (is the chat
model loaded), ``gpu_busy`` (is GPU work running), ``_chat`` (the model call)
and ``_chat_model`` (which model the turn uses). Every test also counts calls
on ollama.chat, ollama.Client, requests and socket.connect: json_chat
swallows exceptions, so a stub that raised would prove nothing, while a count
of zero proves nothing was called.
"""
from __future__ import annotations

import socket

import ollama
import pytest
import requests

from backend.services import decision_settings
from backend.services import nl_control_plane as ncp
from backend.services.decision_data import DECISIONS

NETWORK = ("ollama.chat", "ollama.Client", "requests", "socket.connect")
REAL_CHAT = ncp._chat
REAL_GPU_BUSY = ncp.gpu_busy


@pytest.fixture
def network(monkeypatch):
    """Counts every way decide() could reach Ollama or the network."""
    counts = dict.fromkeys(NETWORK, 0)

    def counter(name):
        def call(*args, **kwargs):
            counts[name] += 1
            raise ConnectionError(f"{name} is not reachable in this test")
        return call

    monkeypatch.setattr(ollama, "chat", counter("ollama.chat"))
    monkeypatch.setattr(ollama, "Client", counter("ollama.Client"))
    for verb in ("get", "post", "put", "patch", "delete", "head", "request"):
        monkeypatch.setattr(requests, verb, counter("requests"))
    monkeypatch.setattr(requests.Session, "request", counter("requests"))
    monkeypatch.setattr(socket.socket, "connect", counter("socket.connect"))
    monkeypatch.setattr(ncp, "_clients", {})
    return counts


@pytest.fixture(autouse=True)
def shadow_jobs(monkeypatch):
    """Shadow questions started during the test, held instead of run on the
    shadow worker; a test runs them with run_jobs()."""
    jobs: list = []
    monkeypatch.setattr(ncp, "_submit_shadow", jobs.append)
    return jobs


def run_jobs(jobs):
    while jobs:
        jobs.pop(0)()


@pytest.fixture
def modes(monkeypatch):
    """Decision modes for the test; anything unset is off."""
    table: dict = {}
    monkeypatch.setattr(decision_settings, "get_decision_mode", lambda d: table.get(d, "off"))
    return table


def test_off_returns_the_fallback_and_calls_nothing(network, modes):
    result = ncp.decide("needs_live_data", {"message": "weather in Paris today?"}, fallback=True)
    assert result.answer is True
    assert (result.source, result.reason) == ("fallback", "off")
    assert result.model is None
    assert network == dict.fromkeys(NETWORK, 0)


@pytest.mark.parametrize("decision_id", sorted(DECISIONS))
def test_every_decision_is_off_by_default(network, monkeypatch, decision_id):
    """With no settings row and no override, the real mode store reads off."""
    monkeypatch.delenv("GUAARDVARK_DECISION_MODES", raising=False)
    monkeypatch.setattr(decision_settings, "_read_row", lambda key: None)
    decision_settings.clear_cache()
    result = ncp.decide(decision_id, {}, fallback="today")
    decision_settings.clear_cache()
    assert (result.answer, result.reason) == ("today", "off")
    assert network == dict.fromkeys(NETWORK, 0)


def test_an_undeclared_decision_returns_the_fallback(network, modes):
    result = ncp.decide("no_such_decision", {"message": "x"}, fallback=42)
    assert (result.answer, result.source, result.reason) == (42, "fallback", "unknown")
    assert network == dict.fromkeys(NETWORK, 0)


def test_decide_never_raises(network, monkeypatch):
    def broken(decision_id):
        raise RuntimeError("settings store exploded")

    monkeypatch.setattr(decision_settings, "get_decision_mode", broken)
    result = ncp.decide("needs_live_data", {"message": "x"}, fallback=False)
    assert (result.answer, result.reason) == (False, "error")


def test_a_recorder_failure_does_not_change_the_answer(network, modes, monkeypatch):
    def recorder(event, state):
        raise RuntimeError("disk full")

    modes["needs_live_data"] = "shadow"
    monkeypatch.setattr(ncp, "_recorder", recorder)
    result = ncp.decide("needs_live_data", {"message": "x"}, fallback=False)
    assert (result.answer, result.reason) == (False, "shadow")


def test_off_records_nothing(network, modes, monkeypatch):
    seen = []
    monkeypatch.setattr(ncp, "_recorder", lambda event, state: seen.append(event))
    ncp.decide("needs_live_data", {"message": "x"}, fallback=False)
    assert seen == []


def test_the_record_carries_a_hash_not_the_text(network, modes, monkeypatch, shadow_jobs):
    seen = []
    modes["needs_live_data"] = "shadow"
    monkeypatch.setattr(ncp, "_recorder", lambda event, state: seen.append(event))
    ncp.decide("needs_live_data", {"message": "my private sentence"}, fallback=False)
    run_jobs(shadow_jobs)
    (event,) = seen
    assert "my private sentence" not in repr(event)
    assert len(event["state_sha256"]) == 16


# ---- asking the model: the gate, the prompt, the reply -------------------------------------

from backend.services import decision_evidence  # noqa: E402
from backend.services.decision_data import MEASURED_ACCURACY  # noqa: E402
from backend.services.decision_prompt import wording_sha  # noqa: E402
from backend.services.nl_control_plane import JsonChatResult  # noqa: E402
from backend.tests.fixtures.decision_sets import load_set  # noqa: E402

BUILD = "4eb23ef187e2" + "c" * 52
OTHER_BUILD = "2cf98652fdc6" + "a" * 52
MODEL = "gemma4:12b"


def _row(decision_id, correct=200, n=200, digest=BUILD[:12], **extra):
    """A measured row that clears any floor, for the current wording and set."""
    entry = DECISIONS[decision_id]
    return {"backend": "chat", "model": MODEL, "digest": digest, "wording_sha": wording_sha(entry),
            "set_version": load_set(entry["set"])["set_version"], "correct": correct, "n": n,
            "date": "2026-10-16", **extra}


class Model:
    """The seams around the model, set so that a decision that is on gets asked."""

    def __init__(self, monkeypatch):
        self.replies: list = []
        self.asked: list = []
        self.resident = {"name": MODEL, "digest": BUILD, "size_vram": 1, "context_length": 32768}
        self.busy = None
        monkeypatch.setattr(ncp, "gpu_busy", lambda: self.busy)
        monkeypatch.setattr(ncp, "_chat_model", lambda ctx: (ctx or {}).get("model") or MODEL)
        monkeypatch.setattr(ncp, "_resident", lambda model: self.resident)
        monkeypatch.setattr(ncp, "_chat", self._chat)

    def _chat(self, system, user, *, model, timeout, num_ctx):
        self.asked.append({"system": system, "user": user, "model": model, "timeout": timeout, "num_ctx": num_ctx})
        reply = self.replies.pop(0) if self.replies else {"answer": True, "confidence": 0.9}
        return reply if isinstance(reply, JsonChatResult) else JsonChatResult(data=reply, raw=str(reply))


@pytest.fixture
def model(monkeypatch):
    return Model(monkeypatch)


@pytest.fixture
def qualified(monkeypatch):
    """needs_live_data and image_intent have a qualifying row for BUILD."""
    for decision_id in ("needs_live_data", "image_intent"):
        monkeypatch.setitem(MEASURED_ACCURACY, decision_id, [_row(decision_id)])


LIVE = {"message": "what's the weather in Lisbon right now?"}
IMAGE = {"message": "draw a fox in the snow", "has_recent_image": False}


def test_on_uses_the_models_answer(network, modes, model, qualified):
    modes["needs_live_data"] = "on"
    model.replies = [{"answer": True, "confidence": 0.8}]
    result = ncp.decide("needs_live_data", LIVE, fallback=False)
    assert (result.answer, result.source, result.reason) == (True, "llm", "ok")
    assert (result.model, result.digest, result.confidence) == (MODEL, BUILD[:12], 0.8)
    (asked,) = model.asked
    assert asked["num_ctx"] == 32768 and asked["timeout"] == 2.0
    assert asked["user"].startswith("STATE:\n")
    assert network == dict.fromkeys(NETWORK, 0)


def test_the_call_is_made_at_temperature_0_with_the_loaded_context(network, modes, model, qualified, monkeypatch):
    """Through the real _chat and json_chat_result, with a stub Ollama client."""
    from backend.utils import ollama_resource_manager as orm

    sent = []

    class Client:
        def __init__(self, host=None, timeout=None):
            self.timeout = timeout

        def chat(self, **kw):
            sent.append({**kw, "client_timeout": self.timeout})
            return {"message": {"content": '{"answer": "yes", "confidence": 1}'}}

    monkeypatch.setattr(ncp, "_chat", REAL_CHAT)
    monkeypatch.setattr(ollama, "Client", Client)
    monkeypatch.setattr(orm, "get_model_info", lambda *a, **k: None)
    modes["needs_live_data"] = "on"
    result = ncp.decide("needs_live_data", LIVE, fallback=False, timeout_s=1.5)
    assert (result.answer, result.reason) == (True, "ok")
    (call,) = sent
    assert call["options"]["temperature"] == 0.0
    assert call["options"]["num_ctx"] == 32768
    assert call["client_timeout"] == 1.5
    assert call["format"] == "json"
    assert call["model"] == MODEL
    assert network["ollama.chat"] == 0


def test_shadow_returns_the_fallback_and_asks_afterwards(network, modes, model, qualified, shadow_jobs,
                                                        monkeypatch):
    seen = []
    monkeypatch.setattr(ncp, "_recorder", lambda event, state: seen.append(event))
    modes["needs_live_data"] = "shadow"
    model.replies = [{"answer": True, "confidence": 0.7}]
    result = ncp.decide("needs_live_data", LIVE, fallback=False)
    assert (result.answer, result.reason) == (False, "shadow")
    assert model.asked == [] and seen == []
    run_jobs(shadow_jobs)
    assert len(model.asked) == 1
    (event,) = seen
    assert (event["llm_answer"], event["fallback_answer"], event["used_answer"]) == (True, False, False)
    assert (event["mode"], event["reason"]) == ("shadow", "shadow")


def test_a_turn_holds_its_shadow_questions_until_it_ends(network, modes, model, qualified, shadow_jobs):
    modes["needs_live_data"] = "shadow"
    ctx = {"request_id": "r1", ncp.SHADOW_KEY: []}
    ncp.decide("needs_live_data", LIVE, fallback=False, ctx=ctx)
    assert shadow_jobs == [] and len(ctx[ncp.SHADOW_KEY]) == 1
    assert ncp.run_shadow(ctx) == 1
    run_jobs(shadow_jobs)
    assert len(model.asked) == 1
    assert ncp.run_shadow(ctx) == 0


def test_shadow_asks_below_the_floor_but_not_on_a_busy_gpu(network, modes, model, shadow_jobs, monkeypatch):
    seen = []
    monkeypatch.setattr(ncp, "_recorder", lambda event, state: seen.append(event))
    monkeypatch.setitem(MEASURED_ACCURACY, "needs_live_data", [])
    modes["needs_live_data"] = "shadow"
    ncp.decide("needs_live_data", LIVE, fallback=False)
    run_jobs(shadow_jobs)
    assert len(model.asked) == 1
    model.busy = "video_render is running"
    ncp.decide("needs_live_data", {"message": "score of the game tonight?"}, fallback=False)
    run_jobs(shadow_jobs)
    assert len(model.asked) == 1
    assert seen[-1]["reason"] == "gpu_busy" and seen[-1]["llm_answer"] is None


@pytest.mark.parametrize("setup, reason", [
    (lambda m, mp: setattr(m, "busy", "video_render is running"), "gpu_busy"),
    (lambda m, mp: setattr(m, "resident", None), "not_resident"),
    (lambda m, mp: m.resident.update(digest=OTHER_BUILD), "below_floor"),          # digest mismatch
    (lambda m, mp: mp.setitem(MEASURED_ACCURACY, "needs_live_data", []), "below_floor"),   # no row: on acts as shadow
    (lambda m, mp: mp.setitem(MEASURED_ACCURACY, "needs_live_data",
                              [_row("needs_live_data", correct=46, n=47)]), "below_floor"),  # under the bound
    (lambda m, mp: mp.setitem(MEASURED_ACCURACY, "needs_live_data",
                              [{**_row("needs_live_data"), "wording_sha": "000000000000"}]), "below_floor"),
    (lambda m, mp: mp.setitem(MEASURED_ACCURACY, "needs_live_data",
                              [{**_row("needs_live_data"), "set_version": 0}]), "below_floor"),
])
def test_the_gate_refuses_without_asking(network, modes, model, qualified, monkeypatch, setup, reason):
    modes["needs_live_data"] = "on"
    setup(model, monkeypatch)
    result = ncp.decide("needs_live_data", LIVE, fallback="today")
    assert (result.answer, result.source, result.reason) == ("today", "fallback", reason)
    assert model.asked == []
    assert network == dict.fromkeys(NETWORK, 0)


def test_the_exclusive_gpu_lock_counts_as_busy(network, modes, model, qualified, monkeypatch):
    """Through the real gpu_busy: the job gate is idle, the coordinator lock is held."""
    from backend.services import gpu_resource_coordinator, job_operation_gate

    class Gate:
        def snapshot(self):
            return {"gpu_busy": False}

    class Coordinator:
        def lock_held(self):
            return True

    monkeypatch.setattr(ncp, "gpu_busy", REAL_GPU_BUSY)
    monkeypatch.setattr(job_operation_gate, "get_gate", lambda: Gate())
    monkeypatch.setattr(gpu_resource_coordinator, "get_gpu_coordinator", lambda: Coordinator())
    modes["needs_live_data"] = "on"
    assert ncp.decide("needs_live_data", LIVE, fallback=False).reason == "gpu_busy"
    assert model.asked == []


def test_a_gate_decision_needs_zero_injection_misses(network, modes, model, monkeypatch):
    state = {"assistant_offer": "Want me to save this as a note?", "user_reply": "yes please, call it ideas"}
    modes["accepted_offer"] = "on"
    monkeypatch.setitem(MEASURED_ACCURACY, "accepted_offer", [_row("accepted_offer")])
    assert ncp.decide("accepted_offer", state, fallback=False).reason == "below_floor"
    monkeypatch.setitem(MEASURED_ACCURACY, "accepted_offer", [_row("accepted_offer", injection_misses=0)])
    assert ncp.decide("accepted_offer", state, fallback=False).reason == "ok"


@pytest.mark.parametrize("reply, answer", [
    (JsonChatResult(data={"answer": "true"}), True),
    (JsonChatResult(data={"answer": "No", "confidence": "0.7"}), False),
    (JsonChatResult(data={"answer": False, "extra": "ignored"}), False),
])
def test_bool_replies_are_read(network, modes, model, qualified, reply, answer):
    modes["needs_live_data"] = "on"
    model.replies = [reply]
    result = ncp.decide("needs_live_data", LIVE, fallback=None)
    assert (result.answer, result.reason) == (answer, "ok")


def test_a_brace_wrapped_reply_is_read(network, modes, model, qualified, monkeypatch):
    import ollama as ollama_module

    class Client:
        def __init__(self, host=None, timeout=None):
            pass

        def chat(self, **kw):
            return {"message": {"content": 'Sure! {"choice": "new_image", "confidence": 0.9} Hope that helps.'}}

    from backend.utils import ollama_resource_manager as orm

    monkeypatch.setattr(ncp, "_chat", REAL_CHAT)
    monkeypatch.setattr(ollama_module, "Client", Client)
    monkeypatch.setattr(orm, "get_model_info", lambda *a, **k: None)
    modes["image_intent"] = "on"
    result = ncp.decide("image_intent", IMAGE, fallback="neither")
    assert (result.answer, result.reason) == ("new_image", "ok")


@pytest.mark.parametrize("reply, reason", [
    (JsonChatResult(failure="timeout", error="timed out"), "timeout"),
    (JsonChatResult(failure="error", error="connection refused"), "error"),
    (JsonChatResult(failure="parse_fail", raw="I think so"), "parse_fail"),
    (JsonChatResult(data={"choice": "draw_it"}), "parse_fail"),       # not one of the options
    (JsonChatResult(data={"choice": "NEW_IMAGE"}), "parse_fail"),     # options match exactly
    (JsonChatResult(data=["new_image"]), "parse_fail"),
])
def test_a_failed_or_unreadable_reply_returns_the_fallback(network, modes, model, qualified, reply, reason):
    modes["image_intent"] = "on"
    model.replies = [reply]
    result = ncp.decide("image_intent", IMAGE, fallback="neither")
    assert (result.answer, result.source, result.reason) == ("neither", "fallback", reason)


# ---- state limits ---------------------------------------------------------------------------

@pytest.mark.parametrize("decision_id, state", [
    ("needs_live_data", {"message": "x" * 601}),                                   # a bool decision
    ("image_intent", {"message": "draw " + "x" * 600, "has_recent_image": False}),  # a choice decision
])
def test_an_oversized_field_is_refused_not_cut(network, modes, model, qualified, decision_id, state):
    modes[decision_id] = "on"
    result = ncp.decide(decision_id, state, fallback="today")
    assert (result.answer, result.reason) == ("today", "too_long")
    assert model.asked == []


def test_a_field_at_its_limit_is_asked(network, modes, model, qualified):
    modes["needs_live_data"] = "on"
    assert ncp.decide("needs_live_data", {"message": "x" * 600}, fallback=False).reason == "ok"


@pytest.mark.parametrize("state", [
    {},
    {"message": "hi", "extra": "field"},
    {"msg": "hi"},
    {"message": None},
    {"message": ["a", "list"]},
    "just a string",
])
def test_a_state_of_the_wrong_shape_is_refused(network, modes, model, qualified, state):
    modes["needs_live_data"] = "on"
    result = ncp.decide("needs_live_data", state, fallback=False)
    assert (result.answer, result.reason) == (False, "bad_state")
    assert model.asked == []


def test_state_limits_apply_in_shadow_too(network, modes, model, qualified):
    modes["needs_live_data"] = "shadow"
    assert ncp.decide("needs_live_data", {"message": "x" * 601}, fallback=False).reason == "too_long"


# ---- one question per request ---------------------------------------------------------------

def test_the_same_question_in_one_request_is_asked_once(network, modes, model, qualified):
    modes["needs_live_data"] = "on"
    ctx = {"request_id": "r1"}
    first = ncp.decide("needs_live_data", LIVE, fallback=False, ctx=ctx)
    second = ncp.decide("needs_live_data", dict(LIVE), fallback=False, ctx=ctx)
    assert first == second
    assert len(model.asked) == 1


def test_another_state_or_request_is_asked_again(network, modes, model, qualified):
    modes["needs_live_data"] = "on"
    ctx = {"request_id": "r1"}
    ncp.decide("needs_live_data", LIVE, fallback=False, ctx=ctx)
    ncp.decide("needs_live_data", {"message": "latest Ollama release?"}, fallback=False, ctx=ctx)
    ncp.decide("needs_live_data", LIVE, fallback=False, ctx={"request_id": "r2"})
    ncp.decide("needs_live_data", LIVE, fallback=False)
    assert len(model.asked) == 4


# ---- per-layer backend policy ---------------------------------------------------------------

L5_STATE = {"stored_fact": "I live in Leeds", "new_fact": "I moved to York"}


@pytest.mark.parametrize("run", ["interactive", "unattended"])
def test_a_batch_only_decision_is_refused_outside_a_batch(network, modes, model, run):
    modes["memory_relation"] = "on"
    result = ncp.decide("memory_relation", L5_STATE, fallback="unrelated", run=run, exclude_models=())
    assert (result.answer, result.reason) == ("unrelated", "batch_only")
    assert model.asked == []


def test_a_batch_run_must_say_who_wrote_what_it_judges(network, modes, model):
    modes["memory_relation"] = "on"
    result = ncp.decide("memory_relation", L5_STATE, fallback="unrelated", run="batch")
    assert result.reason == "author_unknown"
    assert ncp.decide("memory_relation", L5_STATE, fallback="unrelated", run="batch",
                      exclude_models=()).reason == "below_floor"


def test_an_unattended_gate_must_say_who_wrote_what_it_judges(network, modes, model):
    state = {"topic": "local ai", "community": "selfhosted", "title": "t", "post_body": "b", "top_comments": "c"}
    modes["outreach_thread_fit"] = "on"
    assert ncp.decide("outreach_thread_fit", state, fallback="skip", run="unattended").reason == "author_unknown"
    assert ncp.decide("outreach_thread_fit", state, fallback="skip").reason == "below_floor"


def test_an_unknown_run_kind_is_an_error(network, modes, model, qualified):
    modes["needs_live_data"] = "on"
    assert ncp.decide("needs_live_data", LIVE, fallback=False, run="sometimes").reason == "error"


# ---- the judge is not the author ------------------------------------------------------------

def test_the_loaded_model_may_not_judge_its_own_output(network, modes, model, qualified):
    modes["needs_live_data"] = "on"
    result = ncp.decide("needs_live_data", LIVE, fallback=False, exclude_models=[MODEL])
    assert (result.answer, result.reason) == (False, "author_is_judge")
    assert model.asked == []
    assert network == dict.fromkeys(NETWORK, 0)


def test_nor_the_output_of_its_family(network, modes, model, qualified, monkeypatch):
    from backend.utils import ollama_resource_manager as orm

    tags = [
        {"name": MODEL, "digest": BUILD, "details": {"family": "gemma4"}},
        {"name": "gemma4:e2b", "digest": OTHER_BUILD, "details": {"family": "gemma4"}},
        {"name": "qwen3:14b", "digest": "bdbd181c33f2" + "1" * 52, "details": {"family": "qwen3"}},
    ]
    monkeypatch.setattr(orm, "_ollama_tags", lambda: tags)
    modes["needs_live_data"] = "on"
    assert ncp.decide("needs_live_data", LIVE, fallback=False, exclude_models=["gemma4:e2b"]).reason == "author_is_judge"
    assert ncp.decide("needs_live_data", LIVE, fallback=False, exclude_models=["qwen3:14b"]).reason == "ok"
    assert len(model.asked) == 1


def test_an_author_that_cannot_be_resolved_counts_as_the_judge(network, modes, model, qualified, monkeypatch):
    from backend.utils import ollama_resource_manager as orm

    monkeypatch.setattr(orm, "_ollama_tags", lambda: [])
    modes["needs_live_data"] = "on"
    result = ncp.decide("needs_live_data", LIVE, fallback=False, exclude_models=["somewhere-else:7b"])
    assert result.reason == "author_is_judge"


def test_one_author_named_as_a_string_is_one_author(network, modes, model, qualified):
    modes["needs_live_data"] = "on"
    assert ncp.decide("needs_live_data", LIVE, fallback=False, exclude_models=MODEL).reason == "author_is_judge"
