"""gpu_session_when_free: a busy GPU is waited for, not refused.

Chat image edits use it so a second edit queues behind the first. Only the
claim is retried; a capacity refusal, a stop, a timeout and any error from the
work itself surface at once.
"""

import contextlib

import pytest

import backend.services.gpu_resource_policy as grp
from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError
from backend.services.job_types import JobKind


@pytest.fixture
def fake_session(monkeypatch):
    """gpu_session that refuses `busy_times` claims, then holds; records every call."""
    state = {"busy_times": 0, "claims": 0, "entered": 0, "exited": 0, "error": GpuBusyError}

    @contextlib.contextmanager
    def _session(kind, op_id, **kw):
        state["claims"] += 1
        assert kw["on_busy"] == "raise"
        if state["claims"] <= state["busy_times"]:
            raise state["error"]("GPU is held by video_render:other_job")
        state["entered"] += 1
        try:
            yield True
        finally:
            state["exited"] += 1

    monkeypatch.setattr(grp, "gpu_session", _session)
    clock = {"t": 0.0}
    monkeypatch.setattr(grp.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(grp.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    state["clock"] = clock
    return state


def test_waits_through_busy_claims_then_runs(fake_session):
    fake_session["busy_times"] = 3
    reasons = []
    with grp.gpu_session_when_free(JobKind.VIDEO_RENDER, "edit", wait_s=600,
                                   on_wait=reasons.append):
        ran = True
    assert ran and fake_session["claims"] == 4
    assert fake_session["entered"] == fake_session["exited"] == 1
    assert len(reasons) == 3 and "held by" in reasons[0]


def test_capacity_refusal_is_not_waited_for(fake_session):
    fake_session["busy_times"] = 1
    fake_session["error"] = GpuCapacityError
    with pytest.raises(GpuCapacityError):
        with grp.gpu_session_when_free(JobKind.VIDEO_RENDER, "edit", wait_s=600):
            pass
    assert fake_session["claims"] == 1


def test_stop_ends_the_wait(fake_session):
    fake_session["busy_times"] = 99
    stopped = {"now": False}

    def on_wait(_reason):
        stopped["now"] = True

    with pytest.raises(grp.GpuWaitStopped):
        with grp.gpu_session_when_free(JobKind.VIDEO_RENDER, "edit", wait_s=600,
                                       on_wait=on_wait, should_stop=lambda: stopped["now"]):
            pass
    assert fake_session["entered"] == 0


def test_gives_up_with_the_busy_error_after_the_deadline(fake_session):
    fake_session["busy_times"] = 99
    clock = fake_session["clock"]

    with pytest.raises(GpuBusyError):
        with grp.gpu_session_when_free(JobKind.VIDEO_RENDER, "edit", wait_s=600):
            pass
    assert fake_session["entered"] == 0
    assert 600 <= clock["t"] < 620


def test_an_error_from_the_work_is_not_retried(fake_session):
    fake_session["busy_times"] = 0
    with pytest.raises(GpuBusyError):
        with grp.gpu_session_when_free(JobKind.VIDEO_RENDER, "edit", wait_s=600):
            raise GpuBusyError("raised by the work itself")
    assert fake_session["claims"] == 1 and fake_session["exited"] == 1


def test_a_waiting_claim_is_on_the_gates_waiting_list_until_it_holds(fake_session, monkeypatch):
    # Keep ready reads this list; off it, the chat model reloads into the gap
    # between the holder's release and this claim's next try.
    import backend.services.job_operation_gate as jog

    gate = jog.JobOperationGate()
    monkeypatch.setattr(jog, "get_gate", lambda: gate)
    fake_session["busy_times"] = 2
    seen = []
    with grp.gpu_session_when_free(JobKind.VIDEO_RENDER, "edit", wait_s=600,
                                   on_wait=lambda _r: seen.append(gate.snapshot()["gpu_waiting"])):
        inside = gate.snapshot()["gpu_waiting"]
    assert seen == [["video_render:edit"], ["video_render:edit"]]
    assert inside == [] and gate.snapshot()["gpu_waiting"] == []


def test_giving_up_leaves_the_waiting_list(fake_session, monkeypatch):
    import backend.services.job_operation_gate as jog

    gate = jog.JobOperationGate()
    monkeypatch.setattr(jog, "get_gate", lambda: gate)
    fake_session["busy_times"] = 99
    with pytest.raises(GpuBusyError):
        with grp.gpu_session_when_free(JobKind.VIDEO_RENDER, "edit", wait_s=60):
            pass
    assert gate.snapshot()["gpu_waiting"] == []


def test_the_gates_own_wait_is_listed_while_it_waits(monkeypatch):
    import backend.services.job_operation_gate as jog

    gate = jog.JobOperationGate()
    assert gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "first")[0]
    monkeypatch.setattr(jog.time, "sleep", lambda s: None)
    seen = []

    def on_wait(_reason):
        seen.append(gate.snapshot()["gpu_waiting"])
        gate.release_gpu_exclusive(JobKind.VIDEO_RENDER, "first")
        gate._gpu_last_released = float("-inf")  # skip the post-release cooldown

    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "second", on_busy="wait", on_wait=on_wait):
        inside = gate.snapshot()["gpu_waiting"]
    assert seen == [["video_render:second"]] and inside == []
