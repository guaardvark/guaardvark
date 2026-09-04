"""Beat feature gates: a switched-off feature's entries are never sent."""
import json

import pytest
from celery import Celery

from backend import celery_beat_gates as gates
from backend.celery_beat_gates import (
    GATE_TTL_SECONDS,
    GateCache,
    GatedScheduler,
    gate_beat_entries,
    interconnector_client_gate,
)


class _Entry:
    def __init__(self, name):
        self.name = name

    def is_due(self):
        return ("entry-decided", 5.0)


def _scheduler(app, cache):
    """A GatedScheduler without PersistentScheduler's shelve/app wiring."""
    sched = GatedScheduler.__new__(GatedScheduler)
    sched.app = app
    sched.gate_cache = cache
    return sched


@pytest.fixture
def app():
    return Celery("gates-test")


@pytest.fixture(autouse=True)
def _base_is_due(monkeypatch):
    monkeypatch.setattr(gates.PersistentScheduler, "is_due", lambda self, entry: entry.is_due())


def test_gate_beat_entries_accumulates_and_rejects_unknown(app):
    gate_beat_entries(app, {"a": "autoresearch"})
    gate_beat_entries(app, {"b": "social_outreach"})
    assert dict(app.conf.get("beat_feature_gates")) == {"a": "autoresearch", "b": "social_outreach"}
    with pytest.raises(ValueError):
        gate_beat_entries(app, {"c": "no-such-gate"})


def test_closed_gate_holds_the_entry_and_open_gate_defers_to_it(app):
    gate_beat_entries(app, {"idle-check": "autoresearch"})
    state = {"open": False}
    sched = _scheduler(app, GateCache({"autoresearch": lambda: state["open"]}, ttl=0))

    held = sched.is_due(_Entry("idle-check"))
    assert held.is_due is False
    assert held.next == GATE_TTL_SECONDS

    state["open"] = True
    assert sched.is_due(_Entry("idle-check")) == ("entry-decided", 5.0)


def test_ungated_entry_is_untouched(app):
    sched = _scheduler(app, GateCache({}, ttl=0))
    assert sched.is_due(_Entry("check-scheduled-tasks")) == ("entry-decided", 5.0)


def test_decision_is_cached_for_the_ttl():
    clock = {"now": 0.0}
    reads = []

    def predicate():
        reads.append(clock["now"])
        return len(reads) > 1  # closed on the first read, open after

    cache = GateCache({"g": predicate}, ttl=60, clock=lambda: clock["now"])
    assert cache.is_open("g") is False
    clock["now"] = 59
    assert cache.is_open("g") is False  # served from cache
    assert reads == [0.0]
    clock["now"] = 61
    assert cache.is_open("g") is True  # re-read after the TTL
    assert reads == [0.0, 61]


def test_failing_or_unknown_predicate_fails_safe():
    def boom():
        raise RuntimeError("db down")

    cache = GateCache({"g": boom}, ttl=0)
    assert cache.is_open("g") is False  # a broken gate holds the feature
    assert cache.is_open("undeclared") is True  # a missing gate never hides a task


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"is_enabled": True, "node_mode": "client", "master_url": "http://m:5000"}, True),
        ({"is_enabled": True, "node_mode": "master", "master_url": ""}, False),
        ({"is_enabled": False, "node_mode": "client", "master_url": "http://m:5000"}, False),
        ({"is_enabled": True, "node_mode": "client", "master_url": "  "}, False),
        (None, False),
    ],
)
def test_interconnector_client_gate_reads_the_config_row(monkeypatch, config, expected):
    raw = json.dumps(config) if config is not None else None
    monkeypatch.setattr(gates, "_read_setting", lambda key, default=None: raw if key == "interconnector_config" else default)
    assert interconnector_client_gate() is expected


def test_flag_gates_read_their_settings_keys(monkeypatch):
    seen = {}

    def fake_read(key, default=None):
        seen[key] = True
        return "true"

    monkeypatch.setattr(gates, "_read_setting", fake_read)
    assert gates.autoresearch_gate() is True
    assert gates.social_outreach_gate() is True
    assert set(seen) == {"rag_autoresearch_auto_enabled", "social_outreach_enabled"}


def test_every_toggle_governed_loop_declares_its_gate():
    """The registrations in celery_app / rag_autoresearch_tasks stay declared."""
    from backend.tasks.rag_autoresearch_tasks import schedule_autoresearch_tasks

    app = Celery("gates-registration")
    schedule_autoresearch_tasks(app)
    assert app.conf.beat_feature_gates["autoresearch-idle-check"] == "autoresearch"
