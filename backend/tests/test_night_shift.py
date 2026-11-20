"""Night shift rules: the window, the morning rule and the holds.

The store and the probes are replaced, so these run without a database, a
desktop session or a backend.
"""

import datetime

import pytest

from backend.services import night_shift as ns


def at(hhmm: str, day: int = 20) -> datetime.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.datetime(2026, 11, day, h, m)


@pytest.fixture
def shift(monkeypatch):
    """A night shift over an in-memory store with nobody at the machine."""
    box = {
        "settings": {},
        "queue": [{"id": 1}, {"id": 2}],
        "present": None,
        "other": None,
        "disk": None,
        "released": [],
    }
    monkeypatch.setattr(ns, "_get", lambda key, default=None: box["settings"].get(key, default))
    monkeypatch.setattr(ns, "_set", lambda key, value: box["settings"].__setitem__(key, value))
    monkeypatch.setattr(ns, "waiting_rows", lambda kind=None: list(box["queue"]) if kind == ns.NIGHT_SHIFT else [])
    monkeypatch.setattr(ns, "_release_due", lambda now: [])
    monkeypatch.setattr(ns, "presence", lambda now=None, away_s=ns.AWAY_S: {
        "present": box["present"] is not None, "reason": box["present"],
        "desktop_idle_s": 0.0, "desktop_source": "gnome", "chat_idle_s": None,
    })
    monkeypatch.setattr(ns, "_other_work", lambda: box["other"])
    monkeypatch.setattr(ns, "_disk_hold", lambda: box["disk"])

    def release(task_id):
        box["released"].append(task_id)
        box["queue"] = [r for r in box["queue"] if r["id"] != task_id]
        return True

    monkeypatch.setattr(ns, "release", release)
    monkeypatch.setattr("backend.services.job_handoff.sync", lambda factory: {})
    return box


@pytest.mark.parametrize("window, now, opened", [
    ("00:00-07:00", at("03:00"), at("00:00")),
    ("00:00-07:00", at("07:00"), None),
    ("22:30-06:00", at("23:00"), at("22:30")),
    ("22:30-06:00", at("05:59", day=21), at("22:30")),
    ("22:30-06:00", at("12:00"), None),
])
def test_window_opening(window, now, opened):
    assert ns.window_opening(now, window) == opened


@pytest.mark.parametrize("bad", ["7-8", "25:00-01:00", "01:00-01:00", "", None])
def test_bad_window_is_refused(bad):
    with pytest.raises(ValueError):
        ns.parse_window(bad)


def test_one_job_starts_in_the_window_in_order(shift):
    assert ns.tick(at("01:00"))["night_started"] == 1
    assert shift["released"] == [1]


def test_nothing_starts_before_the_window(shift):
    result = ns.tick(at("22:00"))
    assert result["night_started"] is None
    assert result["night"]["hold"] == "the window is closed"


@pytest.mark.parametrize("field, reason", [
    ("present", "the mouse or keyboard was used in the last 10 minutes"),
    ("other", "another job is in progress: a render"),
    ("disk", "only 1.0 GB free"),
])
def test_holds(shift, field, reason):
    shift[field] = reason
    result = ns.tick(at("01:00"))
    assert result["night_started"] is None
    assert result["night"]["hold"] == reason


def test_pause_holds(shift):
    shift["settings"][ns.PAUSED_KEY] = "true"
    assert ns.tick(at("01:00"))["night"]["hold"] == "the night shift is paused"


def test_keeps_going_past_the_end_while_nobody_is_back(shift):
    ns.tick(at("06:59"))
    assert ns.tick(at("08:30"))["night_started"] == 2


def test_stops_for_the_day_once_the_person_is_back(shift):
    ns.tick(at("06:59"))
    shift["present"] = "the mouse or keyboard was used in the last 10 minutes"
    assert ns.tick(at("08:30"))["night_started"] is None
    shift["present"] = None
    assert ns.tick(at("09:30"))["night_started"] is None
    assert shift["released"] == [1]


def test_a_person_back_inside_the_window_only_pauses_it(shift):
    shift["present"] = "the mouse or keyboard was used in the last 10 minutes"
    assert ns.tick(at("01:00"))["night_started"] is None
    shift["present"] = None
    assert ns.tick(at("02:00"))["night_started"] == 1


def test_a_job_added_in_the_morning_waits_for_tonight(shift):
    shift["queue"] = []
    ns.tick(at("06:59"))
    ns.tick(at("07:30"))
    shift["queue"] = [{"id": 9}]
    assert ns.tick(at("10:00"))["night_started"] is None
    assert ns.tick(at("00:30", day=21))["night_started"] == 9
