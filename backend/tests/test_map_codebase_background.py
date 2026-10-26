"""map_codebase never blocks past its wait budget: a map that still describes
the files is served, a stale one is served at once while a single background
build replaces it, and a first map that takes too long answers "computing"
until it is ready.

No network or GPU: the build is a gated stand-in that writes a snapshot, and
the tree's cache key is set by the test.
"""

from __future__ import annotations

import threading
import time

import pytest

from backend.services.system_mapper import store
from backend.tools import workstation_tools as wt


def _mcp(tool):
    tool.set_context({"transport": "mcp"})
    return tool


SNAPSHOT = {
    "file_count": 3,
    "languages": ["python"],
    "stats": {"dependency": {"python_modules": 3, "js_modules": 0}},
    "findings": [{"id": "f1", "kind": "ghost-api-caller", "severity": "medium",
                  "summary": "/api/x — frontend caller with no matching backend route",
                  "paths": ["frontend/src/api/x.js"]}],
}
KEY = {"head": "abc", "listing": "l1", "newest_mtime": 1.0, "files": 3, "mapper": store.MAPPER_VERSION}


def _computes_started(state, n=1, timeout=5.0):
    deadline = time.monotonic() + timeout
    while state["computes"] < n and time.monotonic() < deadline:
        time.sleep(0.01)
    return state["computes"]


@pytest.fixture
def mapper(monkeypatch, tmp_path):
    """A controllable stand-in for the build, over a real store in tmp_path."""
    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path / "storage"))
    root = tmp_path / "checkout"
    root.mkdir()
    state = {"computes": 0, "gate": threading.Event(), "fail": None, "root": root}
    state["gate"].set()

    def build(r):
        state["computes"] += 1
        state["gate"].wait(10)
        if state["fail"]:
            raise RuntimeError(state["fail"])
        payload = dict(SNAPSHOT, cache_key=dict(KEY))
        store.write_json_atomic(store.snapshot_path(r), payload)
        return dict(payload, _cache={"hit": False, "stale": False, "computed_in_seconds": 0.0})

    monkeypatch.setattr(store, "build", build)
    monkeypatch.setattr(store, "current_key", lambda r: dict(KEY))
    monkeypatch.setattr(store, "_jobs", {})
    monkeypatch.setattr(wt, "_safe_root", lambda _arg: root)
    monkeypatch.setattr("backend.services.system_mapper.actions.ranked_findings",
                        lambda snapshot, root: [dict(f, dispatchable=True) for f in snapshot.get("findings") or []])
    yield state
    state["gate"].set()
    for job in list(store._jobs.values()):
        job.done.wait(5)


def _store_map(state, **changes):
    store.write_json_atomic(store.snapshot_path(state["root"]), dict(SNAPSHOT, **changes))


def test_a_current_map_is_served_without_building(mapper):
    _store_map(mapper, cache_key=dict(KEY))
    result = wt.MapCodebaseTool().execute()
    assert result.success
    assert isinstance(result.output, str)
    assert result.output.startswith("System map of")
    assert "ghost-api-caller f1" in result.output
    assert result.metadata["note"] is None
    assert mapper["computes"] == 0


def test_findings_come_before_the_numbers(mapper):
    _store_map(mapper, cache_key=dict(KEY))
    text = wt.MapCodebaseTool().execute().output
    assert text.index("Findings:") < text.index("Stats:")


def test_a_stale_map_is_served_at_once_and_rebuilt_in_the_background(mapper, monkeypatch):
    _store_map(mapper, cache_key=dict(KEY, newest_mtime=0.5))
    mapper["gate"].clear()
    monkeypatch.setattr(wt, "_MAP_WAIT_SECONDS", 30.0)

    t0 = time.monotonic()
    result = wt.MapCodebaseTool().execute()
    assert time.monotonic() - t0 < 5
    assert result.success
    assert result.metadata["file_count"] == 3
    assert result.metadata["cache"]["stale"] is True
    assert result.metadata["cache"]["refreshing"] is True
    assert "source changed" in result.metadata["note"]

    # A second call while the rebuild runs joins it instead of starting another.
    assert _computes_started(mapper) == 1
    wt.MapCodebaseTool().execute()
    assert mapper["computes"] == 1
    assert len(store._jobs) == 1


def test_first_map_that_outlasts_the_wait_answers_computing(mapper, monkeypatch):
    mapper["gate"].clear()
    monkeypatch.setattr(wt, "_MAP_WAIT_SECONDS", 0.2)

    t0 = time.monotonic()
    result = _mcp(wt.MapCodebaseTool()).execute()
    assert time.monotonic() - t0 < 5
    assert result.success
    assert result.metadata["computing"] is True
    assert "Status: computing" in result.output

    assert _computes_started(mapper) == 1
    again = _mcp(wt.MapCodebaseTool()).execute()
    assert again.metadata.get("computing") is True
    assert mapper["computes"] == 1

    mapper["gate"].set()
    for job in list(store._jobs.values()):
        job.done.wait(5)
    done = _mcp(wt.MapCodebaseTool()).execute()
    assert done.metadata["file_count"] == 3
    assert done.metadata["findings"][0]["id"] == "f1"
    assert mapper["computes"] == 1


def test_first_map_that_finishes_within_the_wait_is_returned(mapper):
    result = wt.MapCodebaseTool().execute()
    assert result.success
    assert result.metadata["file_count"] == 3
    assert result.metadata["cache"]["hit"] is False


def test_refresh_waits_for_the_new_map(mapper):
    _store_map(mapper, file_count=1, cache_key=dict(KEY))
    result = wt.MapCodebaseTool().execute(refresh=True)
    assert result.metadata["file_count"] == 3
    assert mapper["computes"] == 1


def test_failed_refresh_keeps_the_last_map_and_says_why(mapper):
    _store_map(mapper, cache_key=dict(KEY))
    mapper["fail"] = "disk full"
    result = wt.MapCodebaseTool().execute(refresh=True)
    assert result.success
    assert result.metadata["cache"]["refresh_error"] == "disk full"
    assert "disk full" in result.metadata["note"]


def test_failed_first_map_is_an_error(mapper):
    mapper["fail"] = "boom"
    result = wt.MapCodebaseTool().execute()
    assert not result.success
    assert "boom" in result.error


def test_wait_budget_stays_under_the_mcp_timeout(monkeypatch):
    from backend.mcp import config as mcp_config

    monkeypatch.setattr(mcp_config, "load_config", lambda: mcp_config.MCPConfig(timeout_seconds=30))
    assert wt._map_wait_seconds(True) == 15
    monkeypatch.setattr(mcp_config, "load_config", lambda: mcp_config.MCPConfig())
    assert wt._map_wait_seconds(True) == wt._MAP_WAIT_SECONDS < mcp_config.MCPConfig().timeout_seconds
