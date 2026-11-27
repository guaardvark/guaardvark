"""The job arbiter: one place that decides which waiting job may start next.

Guaardvark has many queues (Celery, the batch FIFOs, the render requeues, the
GPU gate's own wait) and each keeps its own order. The arbiter does not
replace them. A job still waits in its own loop; before it claims the GPU,
and at the safe points of background work, it asks the arbiter whether it is
its turn. The arbiter answers from one ledger shared by every process on the
box (backend, Celery workers, sidecars), so the answer is the same wherever
the job runs.

What it decides, with the job order setting on:

    * Admission to the GPU. Waiting jobs are ranked by class
      (job_priority_data: interactive, yours, background), by how far the
      person moved them on the Activity page, and, for background work, by
      how long they have waited. Only the head of the line may try to claim
      the card; the rest keep waiting in their own loops.
    * Interactive work first. Interactive tickets head the GPU line. While a
      chat or voice turn runs, and for INTERACTIVE_GRACE_S after it,
      background work does not claim the GPU, and background jobs already
      running pause at their safe points (yield_point). Yours work does not
      wait for a turn: a turn can itself wait on a render it asked for (a
      single-image upscale, a storyboard regenerate), and making that render
      wait for the turn would deadlock both.
    * Nothing running is stopped. A job that holds the GPU keeps it until it
      ends; a higher-class job waits for the release like everyone else.
    * A swarm agent's GPU hold. A job that would unload Ollama's models waits
      while an agent holds one, unless the person presses Run now for it.
    * Idle box for nightly work (box_idle).

With the setting off every entry point is a pass-through: no ledger is read
or written, nothing waits, and the order is exactly what each queue does by
itself.

The ledger is a JSON file under pids/, guarded by an advisory flock on a
sidecar file, the same pattern as the cross-process GPU lease
(gpu_resource_coordinator). Every ticket records the process that owns it;
tickets of processes that are gone are dropped on the next read.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from backend.services import job_priority_data as data
from backend.services.job_priority_data import PriorityClass

logger = logging.getLogger(__name__)

INTERACTIVE = PriorityClass.INTERACTIVE.value
YOURS = PriorityClass.YOURS.value
BACKGROUND = PriorityClass.BACKGROUND.value

# Ticket states.
WAITING = "waiting"    # asking for its turn
RUNNING = "running"    # admitted, or an interactive turn in flight
PAUSED = "paused"      # background work holding at a safe point

# Why a waiting ticket is not admitted (the snapshot's ``blocked`` field).
BLOCKED_INTERACTIVE = "interactive"
BLOCKED_HOLD = "hold"
BLOCKED_QUEUED = "queued"


def _default_ledger_path() -> Path:
    return Path(__file__).resolve().parents[2] / "pids" / "job_queue.json"


def enabled() -> bool:
    """Whether the job order setting is on. Never raises; unreadable is off."""
    try:
        from backend.utils.settings_utils import get_job_priority_enabled

        return bool(get_job_priority_enabled())
    except Exception as e:  # noqa: BLE001 - an unreadable setting keeps today's order
        logger.debug("job order setting unreadable (%s); treating it as off", e)
        return False


_clock: Callable[[], float] = time.time


def _now() -> float:
    return _clock()


# --- Process liveness ---------------------------------------------------------

def _process_started_at(pid: int) -> Optional[float]:
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001 - unknown start time: the PID alone decides
        return None


_SELF = {"pid": None, "started_at": None}


def _self_identity() -> tuple[int, Optional[float]]:
    pid = os.getpid()
    if _SELF["pid"] != pid:  # first call, or a forked child
        _SELF.update(pid=pid, started_at=_process_started_at(pid))
    return pid, _SELF["started_at"]


def _owner_alive(pid: Any, started_at: Any) -> bool:
    """True while the process that wrote a ticket is still the one running.

    A live PID is not enough after a restart, when the PID can belong to an
    unrelated process; a recorded start time that does not match means the
    owner is gone (as gpu_resource_coordinator._holder_alive reads a lease).
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    from backend.utils.proc_alive import pid_alive

    if not pid_alive(pid):
        return False
    if started_at is None:
        return True
    actual = _process_started_at(pid)
    if actual is None:
        return True
    return abs(actual - float(started_at)) < 1.0


# --- The ledger -------------------------------------------------------------

def _empty_state() -> dict:
    return {"tickets": {}, "holds": {}, "last": {}}


class Ledger:
    """The shared state: tickets, swarm holds and when work of each class last ran."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._held = threading.local()
        self._peeked: tuple = (None, None)  # (stat signature, state)

    def _flock_path(self) -> Path:
        return self.path.with_suffix(".flock")

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return _empty_state()
        except Exception as e:  # noqa: BLE001 - a corrupt ledger starts over
            logger.warning("job queue ledger %s unreadable (%s); starting empty", self.path, e)
            return _empty_state()
        state = _empty_state()
        if isinstance(raw, dict):
            for key in state:
                if isinstance(raw.get(key), dict):
                    state[key] = raw[key]
        return state

    def _write(self, state: dict) -> None:
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, sort_keys=True))
        os.replace(tmp, self.path)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[dict]:
        """Read, change and write the ledger under one cross-process lock.

        Dead owners are dropped before the caller sees the state. The file is
        written only when the state changed. Not reentrant: a second flock from
        the same thread would wait on itself, so a nested call raises instead.
        """
        if getattr(self._held, "on", False):
            raise RuntimeError("job queue ledger transaction opened inside another")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                import fcntl
            except ImportError:  # no flock on this platform: in-process lock only
                fcntl = None
            with open(self._flock_path(), "a+") as fh:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                # Set only once the finally below is sure to clear it: a failed
                # mkdir or open used to leave it set for the thread for good.
                self._held.on = True
                try:
                    state = self._read()
                    before = json.dumps(state, sort_keys=True)
                    _reap(state, _now())
                    yield state
                    if json.dumps(state, sort_keys=True) != before:
                        self._write(state)
                finally:
                    self._held.on = False
                    if fcntl is not None:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def peek(self) -> dict:
        """The state without taking the lock, for cheap checks at safe points.

        The file is replaced atomically, so a read never sees half a write.
        The parsed state is reused while the file is unchanged. Dead owners are
        filtered out of the returned copy; the next transaction removes them.
        """
        try:
            st = self.path.stat()
            sig = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            return _empty_state()
        cached_sig, cached = self._peeked
        if cached_sig != sig or cached is None:
            cached = self._read()
            self._peeked = (sig, cached)
        state = json.loads(json.dumps(cached))
        _reap(state, _now())
        return state

    def exists(self) -> bool:
        return self.path.exists()


def _reap(state: dict, now: float) -> None:
    """Drop tickets and holds whose owner is gone, and waiting tickets nobody asks for."""
    tickets = state["tickets"]
    for key, t in list(tickets.items()):
        if not _owner_alive(t.get("pid"), t.get("pid_started_at")):
            del tickets[key]
            continue
        age = now - float(t.get("seen_at") or 0)
        if t.get("state") == WAITING and age > data.WAITING_STALE_S:
            del tickets[key]
    holds = state["holds"]
    for slot, h in list(holds.items()):
        if not _owner_alive(h.get("pid"), h.get("pid_started_at")):
            del holds[slot]


_LEDGER = Ledger(_default_ledger_path())


def _ledger() -> Ledger:
    return _LEDGER


# --- Ranking ------------------------------------------------------------------

def _score(t: dict, now: float) -> float:
    """Lower is admitted first: class, minus background age credit, minus moves."""
    if t.get("run_now"):
        return -1e9
    cls = t.get("cls") or YOURS
    score = data.class_rank(cls) * data.CLASS_STEP
    if cls == BACKGROUND:
        waited = max(0.0, now - float(t.get("enqueued_at") or now))
        score -= min(data.CLASS_STEP, data.CLASS_STEP * waited / data.BACKGROUND_AGING_S)
    return score - float(t.get("boost") or 0.0)


def _seq(t: dict) -> float:
    """Tie-break among equal scores: arrival time, unless a move placed it."""
    return float(t.get("seq", t.get("enqueued_at")) or 0)


def _sort_key(now: float):
    # Rounded so a score a move set equal to another's compares equal.
    return lambda t: (round(_score(t, now), 6), _seq(t), str(t.get("id")))


def _gpu_line(state: dict, now: float) -> list[dict]:
    """Every ticket waiting for the GPU, best first."""
    waiting = [t for t in state["tickets"].values() if t.get("state") == WAITING and t.get("resource") == "gpu"]
    return sorted(waiting, key=_sort_key(now))


def _interactive_busy(state: dict, now: float, grace_s: float) -> Optional[str]:
    """The label of interactive work running now, or ended within ``grace_s``; else None."""
    for t in state["tickets"].values():
        if t.get("cls") != INTERACTIVE or t.get("state") != RUNNING or t.get("resource") != "none":
            continue
        if now - float(t.get("started_at") or now) > data.INTERACTIVE_MAX_S:
            continue
        return t.get("label") or "a chat reply"
    if grace_s > 0:
        ended = float((state.get("last") or {}).get(INTERACTIVE) or 0)
        if ended and now - ended < grace_s:
            return "your conversation"
    return None


def _active_hold(state: dict) -> Optional[dict]:
    holds = list((state.get("holds") or {}).values())
    return min(holds, key=lambda h: float(h.get("since") or 0)) if holds else None


def _hold_text(hold: dict) -> str:
    slot = str(hold.get("slot_id") or "")
    model = slot.split(":", 1)[1] if ":" in slot else slot
    who = hold.get("holder") or "a swarm agent"
    return f"{who} is using {model}" if model else f"{who} holds the GPU"


def _blocked(state: dict, t: dict, now: float) -> tuple[Optional[str], str]:
    """Why ``t`` may not start regardless of the line: (code, reason) or (None, "")."""
    cls = t.get("cls") or YOURS
    if cls == BACKGROUND:
        if _interactive_busy(state, now, data.INTERACTIVE_GRACE_S):
            return BLOCKED_INTERACTIVE, "Background work: waiting until you finish chatting"
    # Interactive work outranks a hold the way it outranks Yours work: someone
    # asking for a picture in chat is the Run now.
    if t.get("evicts_ollama") and not t.get("run_now") and cls != INTERACTIVE:
        hold = _active_hold(state)
        if hold:
            return BLOCKED_HOLD, f"Waiting: {_hold_text(hold)}. Run now to go ahead anyway."
    return None, ""


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _decide(state: dict, t: dict, now: float) -> tuple[bool, Optional[str], str]:
    """(admitted, blocked code, reason) for ticket ``t`` against ``state``."""
    code, reason = _blocked(state, t, now)
    if code:
        return False, code, reason
    if t.get("resource") == "gpu":
        line = _gpu_line(state, now)
        # The head is the best ticket that is asking right now and is not
        # blocked by itself; a ticket asleep in a backoff, or one waiting on a
        # hold, does not hold up the ones behind it.
        head = next(
            (c for c in line
             if now - float(c.get("seen_at") or 0) <= float(c.get("fresh_s") or data.HEAD_FRESH_S)
             and _blocked(state, c, now)[0] is None),
            None,
        )
        if head is not None and head.get("key") != t.get("key"):
            keys = [c.get("key") for c in line]
            pos = keys.index(t.get("key")) + 1 if t.get("key") in keys else len(line) + 1
            return False, BLOCKED_QUEUED, f"Queued {_ordinal(pos)}, behind {head.get('label') or 'another job'}"
    return True, None, ""


# --- Tickets ------------------------------------------------------------------

_tls = threading.local()


def _ticket_key(job_type: str, op_id: str) -> str:
    pid, _ = _self_identity()
    return f"{pid}:{job_type}:{op_id}"


def _new_ticket(key: str, *, job_type: str, op_id: str, state: str, resource: str,
                label: Optional[str], job_id: Optional[str], evicts_ollama: bool,
                cls: Optional[str], now: float) -> dict:
    pid, started = _self_identity()
    info = data.job_type_info(job_type)
    return {
        "key": key,
        "id": uuid.uuid4().hex[:12],
        "job_type": job_type,
        "cls": PriorityClass(cls or info["class"]).value,
        "label": label or info.get("label") or job_type,
        "op_id": str(op_id),
        "job_id": job_id,
        "state": state,
        "resource": resource,
        "evicts_ollama": bool(evicts_ollama),
        "pid": pid,
        "pid_started_at": started,
        "enqueued_at": now,
        "seen_at": now,
        "started_at": now if state != WAITING else None,
        "boost": 0.0,
        "seq": now,
        "moved": False,
        "run_now": False,
        "blocked": None,
        "reason": "",
        "fresh_s": data.HEAD_FRESH_S,
    }


def _stamp_last(state: dict, cls: str, now: float) -> None:
    state.setdefault("last", {})[cls] = now


class Admission:
    """One job's place in the GPU line, from its first ask until it ends.

    A refused claim leaves the ticket waiting, so a caller that retries with
    the same id (the batch loops, gpu_session_when_free) keeps its place and
    its waiting time; one that gives up is dropped after WAITING_STALE_S.
    """

    def __init__(self, key: str, job_type: str, op_id: str, *, label: Optional[str],
                 job_id: Optional[str], evicts_ollama: bool, priority_class: Optional[str],
                 fresh_s: float = data.HEAD_FRESH_S):
        self.key = key
        self.job_type = job_type
        self.op_id = op_id
        self.label = label
        self.job_id = job_id
        self.evicts_ollama = bool(evicts_ollama)
        self.priority_class = priority_class
        self.fresh_s = float(fresh_s)
        self.id: Optional[str] = None
        self.reason = ""
        self.blocked: Optional[str] = None

    @classmethod
    def open(cls, job_type: str, op_id: str, *, label: Optional[str] = None,
             job_id: Optional[str] = None, evicts_ollama: bool = False,
             priority_class: Optional[str] = None, fresh_s: float = data.HEAD_FRESH_S) -> "Admission":
        adm = cls(_ticket_key(job_type, op_id), job_type, str(op_id), label=label, job_id=job_id,
                  evicts_ollama=evicts_ollama, priority_class=priority_class, fresh_s=fresh_s)
        with _ledger().transaction() as state:
            adm._join(state, _now())
        return adm

    def _join(self, state: dict, now: float) -> dict:
        """This job's waiting ticket in ``state``: the one it left on a refused
        try (same process and id), else a new one at the back of its class."""
        t = state["tickets"].get(self.key)
        if t is None or t.get("state") != WAITING:
            t = _new_ticket(self.key, job_type=self.job_type, op_id=self.op_id, state=WAITING,
                            resource="gpu", label=self.label, job_id=self.job_id,
                            evicts_ollama=self.evicts_ollama, cls=self.priority_class, now=now)
            t["fresh_s"] = self.fresh_s
            state["tickets"][self.key] = t
        else:
            t["seen_at"] = now
            t["fresh_s"] = self.fresh_s
            t["evicts_ollama"] = self.evicts_ollama
            if self.label:
                t["label"] = self.label
            if self.job_id:
                t["job_id"] = self.job_id
        self.id = t["id"]
        return t

    def touch(self) -> None:
        """Keep the ticket at the head while its job sleeps between tries."""
        def _do(state):
            t = state["tickets"].get(self.key)
            if t is not None:
                t["seen_at"] = _now()
        self._quietly("touch", _do)

    def _quietly(self, what: str, change: Callable[[dict], None]) -> None:
        """Apply a bookkeeping change; a ledger failure is logged, never raised,
        so it cannot fail the job it describes."""
        try:
            with _ledger().transaction() as state:
                change(state)
        except Exception as e:  # noqa: BLE001
            logger.warning("job arbiter: %s for %s failed (%s)", what, self.op_id, e)

    def turn(self) -> tuple[bool, str]:
        """Whether this job may try to claim the GPU now, and if not, why."""
        now = _now()
        with _ledger().transaction() as state:
            t = state["tickets"].get(self.key)
            if t is None or t.get("state") != WAITING:
                # Dropped while its job slept past WAITING_STALE_S: rejoin.
                t = self._join(state, now)
            t["seen_at"] = now
            ok, code, reason = _decide(state, t, now)
            t["blocked"] = code
            if code:
                t["reason"] = reason
            self.blocked, self.reason = code, reason
            return ok, reason

    def note(self, reason: str) -> None:
        """Record what an admitted job is still waiting for (the GPU itself, say)."""
        if not reason or reason == self.reason:
            return
        self.reason = reason

        def _do(state):
            t = state["tickets"].get(self.key)
            if t is not None:
                t["reason"] = reason
                t["seen_at"] = _now()
        self._quietly("note", _do)

    def started(self) -> None:
        """The job holds the GPU and its work begins."""
        def _do(state):
            now = _now()
            t = state["tickets"].get(self.key)
            if t is not None:
                t.update(state=RUNNING, started_at=now, seen_at=now, blocked=None, reason="")
                _stamp_last(state, t["cls"], now)
        self._quietly("start", _do)

    def finished(self) -> None:
        """The job ran and ended (or failed after it started): leave the line."""
        def _do(state):
            t = state["tickets"].pop(self.key, None)
            if t is not None:
                _stamp_last(state, t["cls"], _now())
        self._quietly("finish", _do)

    def abandon(self) -> None:
        """The job will not ask again (it failed for good or was stopped): leave the line."""
        def _do(state):
            t = state["tickets"].get(self.key)
            if t is not None and t.get("state") == WAITING:
                del state["tickets"][self.key]
        self._quietly("abandon", _do)


def _current_admission() -> Optional[Admission]:
    return getattr(_tls, "admission", None)


@contextlib.contextmanager
def gpu_admission(kind, op_id: str, *, evicts_ollama: bool = False, label: Optional[str] = None,
                  job_type: Optional[str] = None, job_id: Optional[str] = None,
                  slot_id: Optional[str] = None, polls: bool = True,
                  keep_on_refusal: Optional[bool] = None) -> Iterator[Optional[Admission]]:
    """The caller's place in the GPU line, or None while the setting is off.

    An admission already open on this thread for the same id (an outer
    gpu_session_when_free) is reused, so one job keeps one ticket across its
    retries. ``polls`` is False for a caller that asks once.

    After a refusal (GPU busy or not its turn) the ticket stays waiting when
    the caller will ask again with the same id: it polls (the gate's wait, a
    batch loop around it), or its job type requeues itself, in which case the
    ticket keeps the head for RETRY_FRESH_S. Otherwise, or when
    ``keep_on_refusal`` is False, the ticket leaves the line with the refusal.
    """
    if not enabled():
        yield None
        return
    outer = _current_admission()
    if outer is not None and outer.op_id == str(op_id):
        yield outer
        return
    jt = job_type or data.job_type_for_session(kind, op_id, slot_id)
    keep = (polls or data.requeues(jt)) if keep_on_refusal is None else bool(keep_on_refusal)
    try:
        adm = Admission.open(jt, str(op_id), label=label, job_id=job_id, evicts_ollama=evicts_ollama,
                             fresh_s=data.HEAD_FRESH_S if polls else data.RETRY_FRESH_S)
    except Exception as e:  # noqa: BLE001 - an unusable ledger must not stop a render
        logger.warning("job arbiter unavailable (%s); %s runs in today's order", e, op_id)
        yield None
        return
    prev = outer
    _tls.admission = adm
    try:
        yield adm
    except BaseException as e:
        if not (keep and _retryable_refusal(e)):
            adm.abandon()
        raise
    finally:
        _tls.admission = prev


def _retryable_refusal(exc: BaseException) -> bool:
    """A refusal the job retries with the same id, keeping its ticket: GPU busy
    or queued, but not a card too small for the job."""
    from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError

    return isinstance(exc, GpuBusyError) and not isinstance(exc, GpuCapacityError)


def admit_callable(adm: Optional[Admission]) -> Optional[Callable[[], tuple[bool, str]]]:
    """The ``admit`` hook JobOperationGate.gpu_exclusive asks before each claim."""
    if adm is None:
        return None

    def _admit() -> tuple[bool, str]:
        try:
            return adm.turn()
        except Exception as e:  # noqa: BLE001 - a ledger failure admits (today's order)
            logger.warning("job arbiter turn check failed (%s); admitting", e)
            return True, ""

    return _admit


# --- Interactive work -------------------------------------------------------

@contextlib.contextmanager
def interactive(job_type: str = "chat", op_id: Optional[str] = None, *, label: Optional[str] = None) -> Iterator[None]:
    """Mark an interactive turn in flight for the length of the ``with`` block.

    Background work does not claim the GPU during it or for
    INTERACTIVE_GRACE_S after it, and pauses at its safe points. Nothing
    that is already running is stopped.
    """
    if not enabled():
        yield
        return
    op = str(op_id or uuid.uuid4().hex[:8])
    key = f"{_ticket_key(job_type, op)}:{threading.get_ident()}"
    try:
        now = _now()
        with _ledger().transaction() as state:
            state["tickets"][key] = _new_ticket(
                key, job_type=job_type, op_id=op, state=RUNNING, resource="none", label=label,
                job_id=None, evicts_ollama=False, cls=INTERACTIVE, now=now,
            )
            _stamp_last(state, INTERACTIVE, now)
    except Exception as e:  # noqa: BLE001 - chat must never fail over the ledger
        logger.warning("job arbiter: could not mark %s in flight (%s)", job_type, e)
        yield
        return
    try:
        yield
    finally:
        try:
            with _ledger().transaction() as state:
                state["tickets"].pop(key, None)
                _stamp_last(state, INTERACTIVE, _now())
        except Exception as e:  # noqa: BLE001
            logger.warning("job arbiter: could not clear %s (%s)", job_type, e)


# --- Background safe points ---------------------------------------------------

# When background work of each type in this process first had to hold, while
# it keeps having to. Cleared at the first safe point that needs no hold. A
# background job that has been held back for BACKGROUND_AGING_S stops waiting
# out the grace after each turn and waits for the turn itself only, as Yours
# work does, so a long conversation cannot hold it off for good.
_held_since: dict[str, float] = {}


def _yield_grace(job_type: str, cls: str, now: float) -> float:
    since = _held_since.get(job_type)
    if since is not None and now - since >= data.BACKGROUND_AGING_S:
        return 0.0
    return data.INTERACTIVE_GRACE_S


def should_yield(job_type: str = "indexing") -> bool:
    """True when background work of ``job_type`` should hold at its next safe point.

    Cheap: one cached setting read, then a stat of the ledger file. Always
    False while the setting is off.
    """
    if not enabled():
        return False
    try:
        state = _ledger().peek()
    except Exception:  # noqa: BLE001
        return False
    cls = data.default_class(job_type).value
    if cls != BACKGROUND:
        return False
    now = _now()
    return _interactive_busy(state, now, _yield_grace(job_type, cls, now)) is not None


def yield_point(job_type: str, op_id: Any = None, *, label: Optional[str] = None,
                should_stop: Optional[Callable[[], bool]] = None,
                max_wait_s: Optional[float] = None,
                sleep: Callable[[float], None] = time.sleep) -> float:
    """Hold here while interactive work runs; return the seconds held.

    Call it between units of background work (a document, a trial, a step),
    never in the middle of one. While holding, the job is listed on the
    Activity page as paused. ``should_stop()`` ends the hold early (a cancel);
    ``max_wait_s`` bounds it. Returns 0 at once when the setting is off or
    nothing interactive is running.
    """
    if not should_yield(job_type):
        _held_since.pop(job_type, None)
        return 0.0
    _held_since.setdefault(job_type, _now())
    op = str(op_id if op_id is not None else uuid.uuid4().hex[:8])
    key = f"{_ticket_key(job_type, op)}:paused"
    started = _now()
    try:
        with _ledger().transaction() as state:
            t = _new_ticket(key, job_type=job_type, op_id=op, state=PAUSED, resource="none",
                            label=label, job_id=None, evicts_ollama=False, cls=None, now=started)
            t["reason"] = "Paused while you chat; resumes on its own"
            state["tickets"][key] = t
    except Exception as e:  # noqa: BLE001 - not being listed must not stop the pause
        logger.debug("job arbiter: could not list paused %s (%s)", job_type, e)
    logger.info("%s %s paused at a safe point for interactive work", job_type, op)
    try:
        while should_yield(job_type):
            if should_stop is not None and should_stop():
                break
            if max_wait_s is not None and _now() - started >= max_wait_s:
                break
            sleep(data.POLL_S)
    finally:
        try:
            with _ledger().transaction() as state:
                state["tickets"].pop(key, None)
        except Exception:  # noqa: BLE001
            pass
    held = max(0.0, _now() - started)
    logger.info("%s %s resumed after %.0fs", job_type, op, held)
    return held


# --- Nightly work -------------------------------------------------------------

def box_idle(min_idle_s: float = data.IDLE_FOR_NIGHTLY_S) -> bool:
    """True when nightly work may start: always while the setting is off.

    With it on: no interactive or Yours work running or waiting in the
    ledger, none ended within ``min_idle_s``, and no GPU work running (the
    check Keep ready makes before loading the chat model: the cross-process
    lease, this process's gate, ComfyUI's queue).
    """
    if not enabled():
        return True
    try:
        state = _ledger().peek()
    except Exception:  # noqa: BLE001
        return True
    now = _now()
    for t in state["tickets"].values():
        if t.get("cls") in (INTERACTIVE, YOURS):
            return False
    last = state.get("last") or {}
    recent = max(float(last.get(INTERACTIVE) or 0), float(last.get(YOURS) or 0))
    if recent and now - recent < min_idle_s:
        return False
    try:
        from backend.services.chat_keep_ready import _gpu_busy_reason

        busy = _gpu_busy_reason()
        if busy:
            logger.info("box not idle for nightly work: %s", busy)
            return False
    except Exception:  # noqa: BLE001 - no GPU view: the ledger alone decides
        pass
    return True


# --- Swarm GPU holds ----------------------------------------------------------

def record_hold(slot_id: str, holder: Optional[str] = None) -> None:
    """A model another program keeps on the GPU (a swarm agent's Ollama model).

    Recorded only while the setting is on; a hold taken before it was switched
    on is seen from the next agent that starts.
    """
    if not enabled() or not slot_id:
        return
    pid, started = _self_identity()
    try:
        with _ledger().transaction() as state:
            h = state["holds"].get(slot_id) or {"slot_id": slot_id, "since": _now()}
            h.update(pid=pid, pid_started_at=started, holder=holder or h.get("holder") or "a swarm agent")
            state["holds"][slot_id] = h
    except Exception as e:  # noqa: BLE001
        logger.warning("job arbiter: could not record GPU hold %s (%s)", slot_id, e)


def drop_hold(slot_id: str) -> None:
    """The hold on ``slot_id`` ended. Runs whatever the setting, without creating the ledger."""
    if not slot_id or not _ledger().exists():
        return
    try:
        with _ledger().transaction() as state:
            state["holds"].pop(slot_id, None)
    except Exception as e:  # noqa: BLE001
        logger.warning("job arbiter: could not drop GPU hold %s (%s)", slot_id, e)


# --- The Activity page --------------------------------------------------------

class QueueError(ValueError):
    """A move or Run now the arbiter cannot do; the message says why."""


def _public(t: dict, now: float) -> dict:
    info = data.CLASS_INFO[PriorityClass(t.get("cls") or YOURS)]
    return {
        "id": t.get("id"),
        "job_type": t.get("job_type"),
        "label": t.get("label"),
        "class": t.get("cls"),
        "class_label": info["label"],
        "state": t.get("state"),
        "reason": t.get("reason") or "",
        "blocked": t.get("blocked"),
        "job_id": t.get("job_id"),
        "op_id": t.get("op_id"),
        "waited_s": round(max(0.0, now - float(t.get("enqueued_at") or now)), 1),
        "moved": bool(t.get("moved")),
        "run_now": bool(t.get("run_now")),
    }


def snapshot() -> dict:
    """What the Activity page shows: the GPU line in order, then paused and running work."""
    on = enabled()
    out = {
        "enabled": on,
        "classes": [
            {"id": c.value, "label": info["label"], "help": info["help"]}
            for c, info in sorted(data.CLASS_INFO.items(), key=lambda kv: kv[1]["rank"])
        ],
        "queued": [], "paused": [], "running": [], "holds": [], "interactive_busy": None,
    }
    if not on or not _ledger().exists():
        return out
    state = _ledger().peek()
    now = _now()
    for i, t in enumerate(_gpu_line(state, now)):
        item = _public(t, now)
        item["position"] = i + 1
        item["can_run_now"] = t.get("blocked") == BLOCKED_HOLD and not t.get("run_now")
        out["queued"].append(item)
    others = sorted(state["tickets"].values(), key=lambda t: float(t.get("started_at") or 0))
    out["paused"] = [_public(t, now) for t in others if t.get("state") == PAUSED]
    out["running"] = [_public(t, now) for t in others if t.get("state") == RUNNING]
    out["holds"] = [
        {"slot_id": h.get("slot_id"), "holder": h.get("holder"), "since": h.get("since")}
        for h in state["holds"].values()
    ]
    out["interactive_busy"] = _interactive_busy(state, now, 0.0)
    return out


def _place(t: dict, now: float, score: float, seq: float) -> None:
    """Give ``t`` the score and tie-break that sort it where a move put it.

    Its boost absorbs the difference, so a background item keeps gaining
    age credit from there: a move is relative to now, and aging carries on.
    """
    t["boost"] = float(t.get("boost") or 0.0) + (_score(t, now) - score)
    t["seq"] = seq
    t["moved"] = True


def _same(a: float, b: float) -> bool:
    return round(a, 6) == round(b, 6)


def move(ticket_id: str, direction: str) -> dict:
    """Move a waiting item one place up or down the GPU line. Returns the new snapshot."""
    if direction not in ("up", "down"):
        raise QueueError("direction must be up or down")
    if not enabled():
        raise QueueError("Job order is off in Settings; the queues keep their own order.")
    with _ledger().transaction() as state:
        now = _now()
        line = _gpu_line(state, now)
        idx = next((i for i, t in enumerate(line) if t.get("id") == ticket_id), None)
        if idx is None:
            raise QueueError("That item is no longer waiting.")
        t = line[idx]
        if t.get("run_now"):
            raise QueueError("That item is already set to run now.")
        if direction == "up" and idx > 0:
            above = line[idx - 1]
            if above.get("run_now"):
                raise QueueError("The item above is set to run now.")
            score = _score(above, now)
            two_up = line[idx - 2] if idx >= 2 else None
            if two_up is not None and _same(_score(two_up, now), score):
                seq = (_seq(two_up) + _seq(above)) / 2.0
            else:
                seq = _seq(above) - 1.0
            _place(t, now, score, seq)
        elif direction == "down" and idx < len(line) - 1:
            below = line[idx + 1]
            score = _score(below, now)
            two_down = line[idx + 2] if idx + 2 < len(line) else None
            if two_down is not None and _same(_score(two_down, now), score):
                seq = (_seq(below) + _seq(two_down)) / 2.0
            else:
                seq = _seq(below) + 1.0
            _place(t, now, score, seq)
    return snapshot()


def run_now(ticket_id: str) -> dict:
    """Let a job waiting on a swarm agent's GPU hold go ahead, and to the front."""
    if not enabled():
        raise QueueError("Job order is off in Settings.")
    with _ledger().transaction() as state:
        t = next((t for t in state["tickets"].values() if t.get("id") == ticket_id), None)
        if t is None or t.get("state") != WAITING:
            raise QueueError("That item is no longer waiting.")
        if t.get("blocked") != BLOCKED_HOLD:
            raise QueueError("Run now is for an item waiting on a swarm agent's GPU hold.")
        t["run_now"] = True
    return snapshot()
