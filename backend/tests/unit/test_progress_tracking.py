"""progress_emitter is a compatibility shim over unified_progress_system.

Everything here goes through the real UnifiedProgressSystem and observes the
seams it actually uses: its SocketIO handle (_get_socketio) and its Redis
publish (stubbed, so a test run never pushes events into a live UI). The old
tests patched a progress_emitter._get_socketio that has been a no-op since
the unification, asserted ids for a type the system does not know, and passed
a str where a dict goes — producing the very error they should have caught.
"""
from unittest.mock import Mock

import pytest

from backend.utils import progress_emitter as pe
from backend.utils import unified_progress_system as ups
from backend.utils.progress_emitter import (
    ProgressTracker,
    complete_progress,
    create_progress_tracker,
    emit_progress_event,
    error_progress,
    update_progress,
)


@pytest.fixture
def socket(monkeypatch):
    """A Mock SocketIO wired where the unified system really looks, Redis stubbed."""
    system = ups.get_unified_progress()
    sock = Mock()
    monkeypatch.setattr(system, "_socketio_enabled", True)
    monkeypatch.setattr(type(system), "_get_socketio", lambda self: sock)
    import redis
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("stubbed")))
    return sock


def _progress_emits(sock):
    return [c for c in sock.emit.call_args_list if c.args and c.args[0] == "job_progress"]


def _last_payload(sock):
    return _progress_emits(sock)[-1].args[1]


class TestProgressEmitter:
    def test_create_progress_tracker_ids_carry_the_process_type(self, socket):
        pid = create_progress_tracker("indexing", "Doc 1")
        assert pid.startswith("indexing_")
        # An unmapped type is not an error; it lands in the unknown bucket.
        assert create_progress_tracker("test", "x").startswith("unknown_")
        assert socket.emit.called

    def test_update_progress_reaches_the_socket(self, socket):
        pid = create_progress_tracker("indexing", "Doc 1")
        socket.emit.reset_mock()
        update_progress(pid, 50, "Halfway done")
        payload = _last_payload(socket)
        assert payload["job_id"] == pid
        assert payload["progress"] == 50
        assert payload["message"] == "Halfway done"
        assert payload["status"] == "processing"

    def test_complete_and_error_progress(self, socket):
        pid = create_progress_tracker("indexing", "Doc 1")
        complete_progress(pid, "All done")
        assert _last_payload(socket)["status"] == "complete"

        pid2 = create_progress_tracker("indexing", "Doc 2")
        error_progress(pid2, "Something went wrong")
        assert _last_payload(socket)["status"] == "error"

    def test_emit_progress_event_goes_to_the_job_room_and_the_global_room(self, socket):
        emit_progress_event("training_abc", 75, "Almost done", "processing", "training")
        rooms = [c.kwargs.get("to") for c in _progress_emits(socket)]
        assert "training_abc" in rooms and "global_progress" in rooms
        payload = _last_payload(socket)
        assert payload["progress"] == 75 and payload["job_id"] == "training_abc"

    def test_legacy_positional_process_type_is_dropped_not_fatal(self, socket):
        # The pre-unification call shape: 4th positional was process_type.
        pid = create_progress_tracker("indexing", "Doc 1")
        socket.emit.reset_mock()
        update_progress(pid, 50, "Halfway", "indexing")
        assert _last_payload(socket)["progress"] == 50  # the update still landed
        complete_progress(pid, "done", "indexing")
        assert _last_payload(socket)["status"] == "complete"

    def test_progress_tracker_context_manager(self, socket):
        with ProgressTracker("indexing", "Test context") as progress:
            assert progress.process_id.startswith("indexing_")
            progress.update(50, "Halfway")
            assert _last_payload(socket)["progress"] == 50
        assert _last_payload(socket)["status"] == "complete"

    def test_progress_tracker_context_manager_error(self, socket):
        with pytest.raises(ValueError):
            with ProgressTracker("indexing", "Test error") as progress:
                progress.update(50, "Halfway")
                raise ValueError("Test error")
        payload = _last_payload(socket)
        assert payload["status"] == "error"
        assert "Test error" in payload["message"]

    def test_socketio_unavailable_is_not_an_error(self, monkeypatch):
        system = ups.get_unified_progress()
        monkeypatch.setattr(type(system), "_get_socketio", lambda self: None)
        import redis
        monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("stubbed")))
        pid = create_progress_tracker("indexing", "Test process")
        update_progress(pid, 50, "Halfway done")
        complete_progress(pid, "All done")
        assert system.get_process_history(pid)[-1].status.value == "complete"


class TestProgressIntegration:
    def test_multiple_concurrent_processes(self, socket):
        p1 = create_progress_tracker("indexing", "Doc 1")
        p2 = create_progress_tracker("file_generation", "File 1")
        p3 = create_progress_tracker("llm_processing", "Chat 1")
        update_progress(p1, 25, "Indexing 25%")
        update_progress(p2, 50, "Generating 50%")
        update_progress(p3, 75, "Processing 75%")
        complete_progress(p1, "Indexing done")
        complete_progress(p2, "File generated")
        complete_progress(p3, "LLM done")
        system = ups.get_unified_progress()
        for pid in (p1, p2, p3):
            history = system.get_process_history(pid)
            assert [e.status.value for e in history][-1] == "complete"
        by_room = {c.kwargs.get("to") for c in _progress_emits(socket)}
        assert {p1, p2, p3, "global_progress"} <= by_room
