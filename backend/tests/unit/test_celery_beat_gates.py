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
    assert held.next == 1.0  # "ask again right away" — tick() does the real holding

    state["open"] = True
    assert sched.is_due(_Entry("idle-check")) == ("entry-decided", 5.0)


def test_tick_rotates_a_closed_entry_off_the_heap_top(app, monkeypatch):
    """Regression: a closed entry left on top starved every other entry."""
    import heapq
    from celery.beat import event_t

    gate_beat_entries(app, {"heartbeat": "interconnector_client"})
    sched = _scheduler(app, GateCache({"interconnector_client": lambda: False}, ttl=0))
    entries = {"heartbeat": _Entry("heartbeat"), "core": _Entry("core")}
    sched._store = {"entries": entries}  # PersistentScheduler.schedule reads its shelve here
    sched.old_schedulers = entries
    monkeypatch.setattr(GatedScheduler, "schedules_equal", lambda self, a, b: True)
    monkeypatch.setattr(GatedScheduler, "_when", lambda self, entry, secs: 1000.0 + secs)
    sched._heap = [event_t(10.0, 5, sched.schedule["heartbeat"]), event_t(20.0, 5, sched.schedule["core"])]
    heapq.heapify(sched._heap)
    monkeypatch.setattr(gates.PersistentScheduler, "tick", lambda self, *a, **k: "base-tick")

    assert sched.tick() == 0  # closed entry moved out of the way, come straight back
    assert sched._heap[0].entry.name == "core"
    assert sched._heap[1].time == 1000.0 + GATE_TTL_SECONDS
    assert sched.tick() == "base-tick"  # the core entry gets its turn


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
    monkeypatch.setattr(
        gates, "_read_setting",
        lambda key, default=None, table="settings": raw if key == "interconnector_config" else default,
    )
    assert interconnector_client_gate() is expected


def test_flag_gates_read_their_settings_keys(monkeypatch):
    seen = {}

    def fake_read(key, default=None, table="settings"):
        seen[key] = True
        return "true"

    monkeypatch.setattr(gates, "_read_setting", fake_read)
    assert gates.autoresearch_gate() is True
    assert gates.social_outreach_gate() is True
    assert set(seen) == {"rag_autoresearch_auto_enabled", "social_outreach_enabled"}


@pytest.mark.parametrize("raw, expected", [(None, True), ("true", True), ("false", False), ("FALSE ", False)])
def test_self_improvement_gate_defaults_on_without_a_row(monkeypatch, raw, expected):
    def fake_read(key, default=None, table="settings"):
        assert (key, table) == ("self_improvement_enabled", "system_settings")
        return raw if raw is not None else default

    monkeypatch.setattr(gates, "_read_setting", fake_read)
    assert gates.self_improvement_gate() is expected


def test_read_setting_refuses_an_unknown_table():
    with pytest.raises(ValueError):
        gates._read_setting("k", table="users")


def test_every_toggle_governed_loop_declares_its_gate():
    """The registrations in celery_app / rag_autoresearch_tasks stay declared."""
    from backend.tasks.rag_autoresearch_tasks import schedule_autoresearch_tasks

    from backend.tasks.self_improvement_tasks import schedule_self_improvement_tasks

    app = Celery("gates-registration")
    schedule_autoresearch_tasks(app)
    schedule_self_improvement_tasks(app)
    declared = app.conf.beat_feature_gates
    assert declared["autoresearch-idle-check"] == "autoresearch"
    assert {declared[n] for n in ("self-improvement-check", "uncle-claude-advice", "servo-optimization")} == {"self_improvement"}
