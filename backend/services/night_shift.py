"""Night shift: jobs a person scheduled for later, released on an unattended machine.

A scheduled job is a ``tasks`` row held in status ``waiting``, which neither
release loop selects. Two kinds:

* ``schedule_type = 'night_shift'``: runs once the window has opened, nobody
  is at the machine and no other job is running; one at a time, in the order
  the person set (``queue_rank``).
* ``schedule_type = 'at'``: joins the normal line when ``due_date`` passes.

``tick()`` is called by the every-minute scheduler beat. It works without a
Flask app context and without the job order setting: the idle test here reads
the desktop, the chat history and the jobs list itself.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

WAITING = "waiting"
NIGHT_SHIFT = "night_shift"
AT = "at"
SCHEDULED_TYPES = (NIGHT_SHIFT, AT)

WINDOW_KEY = "night_shift_window"
PAUSED_KEY = "night_shift_paused"
STATE_KEY = "night_shift_state"
MIN_FREE_GB_KEY = "night_shift_min_free_gb"

DEFAULT_WINDOW = "00:00-07:00"
# How long the machine must have been left alone before a night job starts.
AWAY_S = 600.0
# Free space the render folders' volume must keep before a night job starts. Measured
# 2026-11-19 over 179 rendered clips: median 1.3 MB, largest 90 MB, so 5 GB is
# room for a full night of renders and their intermediate frames.
DEFAULT_MIN_FREE_GB = 5.0
# An `at` job released more than this long after its time is shown as late.
LATE_AFTER_S = 300.0

_GDBUS_TIMEOUT_S = 3


# --- storage -----------------------------------------------------------------

_session_factory = None


def _session():
    """A session without a Flask app context: the beat calls this module too."""
    global _session_factory
    if _session_factory is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from backend import config

        _session_factory = sessionmaker(bind=create_engine(config.DATABASE_URL, pool_pre_ping=True))
    return _session_factory()


def _get(key: str, default: Optional[str] = None) -> Optional[str]:
    s = _session()
    try:
        row = s.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": key}).fetchone()
        return str(row[0]) if row and row[0] is not None else default
    except Exception as e:  # noqa: BLE001 - an unreadable setting reads as its default
        logger.warning("night shift: could not read %s: %s", key, e)
        return default
    finally:
        s.close()


def _set(key: str, value: str) -> None:
    s = _session()
    try:
        s.execute(
            text(
                "INSERT INTO settings (key, value, updated_at) VALUES (:k, :v, :now) "
                "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = :now"
            ),
            {"k": key, "v": value, "now": datetime.datetime.now()},
        )
        s.commit()
    finally:
        s.close()


# --- window ------------------------------------------------------------------

def parse_window(value: Optional[str]) -> tuple[int, int]:
    """'HH:MM-HH:MM' as (start, end) in minutes of the day. Raises ValueError."""
    start_s, end_s = str(value or "").split("-")

    def minutes(part: str) -> int:
        h, m = (int(x) for x in part.strip().split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(part)
        return h * 60 + m

    start, end = minutes(start_s), minutes(end_s)
    if start == end:
        raise ValueError("the window's start and end are the same time")
    return start, end


def get_window() -> str:
    value = _get(WINDOW_KEY, DEFAULT_WINDOW) or DEFAULT_WINDOW
    try:
        parse_window(value)
        return value
    except ValueError:
        logger.warning("night shift: bad window %r; using %s", value, DEFAULT_WINDOW)
        return DEFAULT_WINDOW


def set_window(start: str, end: str) -> str:
    value = f"{start.strip()}-{end.strip()}"
    s, e = parse_window(value)
    value = f"{s // 60:02d}:{s % 60:02d}-{e // 60:02d}:{e % 60:02d}"
    _set(WINDOW_KEY, value)
    return value


def window_opening(now: datetime.datetime, window: str) -> Optional[datetime.datetime]:
    """When the window ``now`` falls in opened; None outside the window."""
    start, end = parse_window(window)
    minute = now.hour * 60 + now.minute
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if start < end:
        if start <= minute < end:
            return midnight + datetime.timedelta(minutes=start)
        return None
    if minute >= start:
        return midnight + datetime.timedelta(minutes=start)
    if minute < end:
        return midnight - datetime.timedelta(days=1) + datetime.timedelta(minutes=start)
    return None


def is_paused() -> bool:
    return (_get(PAUSED_KEY, "false") or "").strip().lower() in ("true", "1", "yes", "on")


def set_paused(paused: bool) -> None:
    _set(PAUSED_KEY, "true" if paused else "false")


def _load_state() -> dict:
    try:
        state = json.loads(_get(STATE_KEY, "") or "{}")
        return state if isinstance(state, dict) else {}
    except ValueError:
        return {}


def _save_state(state: dict) -> None:
    _set(STATE_KEY, json.dumps(state))


# --- is anyone there ---------------------------------------------------------

def _gdbus(dest: str, path: str, method: str, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["gdbus", "call", "--session", "--dest", dest, "--object-path", path, "--method", method, *args],
            capture_output=True, text=True, timeout=_GDBUS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def desktop_idle() -> dict:
    """Mouse and keyboard idle time on this desktop.

    ``seconds`` is None where it cannot be read (no GNOME session bus: another
    desktop, a container, a service start); the caller then goes by
    Guaardvark's own activity and says so. ``inhibited`` is True while an
    application holds the screen awake, which is how video players and
    browsers mark playback.
    """
    raw = _gdbus(
        "org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core",
        "org.gnome.Mutter.IdleMonitor.GetIdletime",
    )
    seconds = None
    # The reply reads "(uint64 4842,)": milliseconds after the type name.
    match = re.search(r"uint64\s+(\d+)", raw or "")
    if match:
        seconds = int(match.group(1)) / 1000.0
    if seconds is None:
        return {"seconds": None, "inhibited": False, "source": None}
    # 8 is GNOME's "inhibit the session being marked idle" flag.
    inhibited = _gdbus(
        "org.gnome.SessionManager", "/org/gnome/SessionManager",
        "org.gnome.SessionManager.IsInhibited", "8",
    )
    return {"seconds": seconds, "inhibited": "true" in (inhibited or ""), "source": "gnome"}


def _last_chat_age_s(now: datetime.datetime) -> Optional[float]:
    """Seconds since the last message a person sent in any chat."""
    s = _session()
    try:
        row = s.execute(text("SELECT MAX(timestamp) FROM llm_messages WHERE role = 'user'")).fetchone()
    except Exception as e:  # noqa: BLE001
        logger.debug("night shift: chat history unreadable: %s", e)
        return None
    finally:
        s.close()
    if not row or row[0] is None:
        return None
    return max(0.0, (now - row[0]).total_seconds())


def presence(now: Optional[datetime.datetime] = None, away_s: float = AWAY_S) -> dict:
    """Whether a person is at the machine, and what that was read from."""
    now = now or datetime.datetime.now()
    desk = desktop_idle()
    reason = None
    if desk["seconds"] is not None and desk["seconds"] < away_s:
        reason = "the mouse or keyboard was used in the last 10 minutes"
    elif desk["inhibited"]:
        reason = "something on screen is keeping the display awake (a video, a call)"
    chat_age = _last_chat_age_s(now)
    if reason is None and chat_age is not None and chat_age < away_s:
        reason = "a chat message was sent in the last 10 minutes"
    if reason is None:
        try:
            from backend.services import job_arbiter

            if job_arbiter.should_yield("scheduled_task"):
                reason = "a chat or voice turn is running"
        except Exception:  # noqa: BLE001
            pass
    return {
        "present": reason is not None,
        "reason": reason,
        "desktop_idle_s": desk["seconds"],
        "desktop_source": desk["source"],
        "chat_idle_s": chat_age,
    }


def _other_work() -> Optional[str]:
    """Why the machine is not free for a night job, or None.

    Asks the backend for every job in flight, of every kind, so a render a
    person started by hand holds the night shift between its clips too. No
    answer from the backend holds the shift: it does not start work blind.
    """
    try:
        from backend.utils.backend_http import request_json

        body = request_json("GET", "/api/jobs/active", params={"limit": 200}, read_timeout=20,
                            own_backend=True).body
        jobs = (body or {}).get("jobs") or []
    except Exception as e:  # noqa: BLE001
        return f"the jobs list did not answer ({type(e).__name__})"
    for job in jobs:
        meta = job.get("metadata") or {}
        if meta.get("waiting"):
            continue
        if job.get("status") == "pending" and meta.get("next_run_at"):
            continue  # a recurring job between runs
        return f"another job is in progress: {job.get('label') or job.get('id')}"
    try:
        from backend.services.chat_keep_ready import _gpu_busy_reason

        busy = _gpu_busy_reason()
        if busy:
            return busy
    except Exception:  # noqa: BLE001
        pass
    return None


def _disk_hold() -> Optional[str]:
    try:
        floor_gb = float(_get(MIN_FREE_GB_KEY, "") or DEFAULT_MIN_FREE_GB)
    except ValueError:
        floor_gb = DEFAULT_MIN_FREE_GB
    # Renders land under the uploads folder (Images, Videos) and exports under
    # outputs; the two can sit on different volumes, so the tighter one decides.
    free_gb = None
    try:
        from backend import config

        folders = [getattr(config, name, None) for name in ("UPLOAD_DIR", "OUTPUT_DIR")]
    except Exception:  # noqa: BLE001
        folders = [os.getcwd()]
    for folder in folders:
        try:
            here = shutil.disk_usage(folder).free / 1024 ** 3
        except (OSError, TypeError):
            continue
        free_gb = here if free_gb is None else min(free_gb, here)
    if free_gb is not None and free_gb < floor_gb:
        return f"only {free_gb:.1f} GB free where renders are saved; the night shift keeps {floor_gb:g} GB free"
    return None


# --- rows --------------------------------------------------------------------

_ROW_COLS = (
    "id, name, type, status, schedule_type, due_date, queue_rank, priority, created_at, "
    "last_run_at, error_message, progress, task_handler, updated_at"
)
_ORDER = "queue_rank ASC NULLS LAST, priority ASC, created_at ASC, id ASC"


def _rows(where: str, params: Optional[dict] = None, order: str = _ORDER, limit: int = 500) -> list[dict]:
    s = _session()
    try:
        res = s.execute(text(f"SELECT {_ROW_COLS} FROM tasks WHERE {where} ORDER BY {order} LIMIT {int(limit)}"),
                        params or {})
        return [dict(r._mapping) for r in res.fetchall()]
    finally:
        s.close()


def waiting_rows(schedule_type: Optional[str] = None) -> list[dict]:
    if schedule_type:
        return _rows("status = :st AND schedule_type = :t", {"st": WAITING, "t": schedule_type})
    return _rows("status = :st", {"st": WAITING})


def _utcnow() -> datetime.datetime:
    # tasks.due_date and updated_at hold naive UTC, as the release beat writes them.
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def release(task_id: int) -> bool:
    """Start one waiting job: claim it and hand it to the unified executor.

    The claim goes straight from ``waiting`` to ``queued`` with its job id, so
    neither release loop (both take ``pending`` rows) can pick it up as well.
    """
    job_id = f"task_{task_id}"
    s = _session()
    try:
        claimed = s.execute(
            text(
                "UPDATE tasks SET status = 'queued', job_id = :job, last_run_at = :local, updated_at = :utc, "
                "error_message = NULL, progress = 0 WHERE id = :id AND status = :waiting"
            ),
            {"job": job_id, "local": datetime.datetime.now(), "utc": _utcnow(), "id": task_id, "waiting": WAITING},
        ).rowcount
        s.commit()
        row = s.execute(text("SELECT type FROM tasks WHERE id = :id"), {"id": task_id}).fetchone()
    finally:
        s.close()
    if claimed != 1:
        return False
    try:
        from backend.tasks.task_scheduler_celery import _get_queue_for_task_type
        from backend.tasks.unified_task_executor import execute_unified_task

        execute_unified_task.apply_async(
            args=[task_id], task_id=job_id, queue=_get_queue_for_task_type(row[0] if row else None)
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("night shift: could not start task %s: %s", task_id, e)
        s = _session()
        try:
            s.execute(
                text("UPDATE tasks SET status = :waiting, job_id = NULL WHERE id = :id AND status = 'queued'"),
                {"waiting": WAITING, "id": task_id},
            )
            s.commit()
        finally:
            s.close()
        return False


def schedule(task_id: int, when: str, at: Optional[datetime.datetime] = None) -> dict:
    """Hold a job for the night shift or for a time. ``at`` is naive UTC.

    Refused for a job that is queued or running. A night-shift job is not
    re-run by itself after a failure, so a failed render is not rendered again
    while nobody is watching.
    """
    if when not in SCHEDULED_TYPES:
        raise ValueError(f"when must be one of {SCHEDULED_TYPES}")
    if when == AT and at is None:
        raise ValueError("a time is required")
    s = _session()
    try:
        rank = None
        if when == NIGHT_SHIFT:
            top = s.execute(
                text("SELECT MAX(queue_rank) FROM tasks WHERE status = :w AND schedule_type = :t"),
                {"w": WAITING, "t": NIGHT_SHIFT},
            ).fetchone()
            rank = (float(top[0]) if top and top[0] is not None else 0.0) + 1.0
        changed = s.execute(
            text(
                "UPDATE tasks SET status = :w, schedule_type = :t, due_date = :due, queue_rank = :rank, "
                "job_id = NULL, updated_at = :utc, "
                "max_retries = CASE WHEN :night THEN 0 ELSE max_retries END "
                "WHERE id = :id AND status NOT IN ('queued', 'in-progress', 'running')"
            ),
            {"w": WAITING, "t": when, "due": at if when == AT else None, "rank": rank,
             "night": when == NIGHT_SHIFT, "utc": _utcnow(), "id": task_id},
        ).rowcount
        s.commit()
    finally:
        s.close()
    if changed != 1:
        raise LookupError(f"job {task_id} was not found or is already running")
    return {"task_id": task_id, "when": when, "queue_rank": rank}


def reorder(task_ids: list[int]) -> int:
    """Set the night's order: the first id runs first. Returns rows changed."""
    s = _session()
    changed = 0
    try:
        for rank, task_id in enumerate(task_ids, start=1):
            changed += s.execute(
                text("UPDATE tasks SET queue_rank = :r WHERE id = :id AND status = :w"),
                {"r": float(rank), "id": int(task_id), "w": WAITING},
            ).rowcount
        s.commit()
    finally:
        s.close()
    return changed


# --- the every-minute pass ---------------------------------------------------

def _release_due(utcnow: datetime.datetime) -> list[int]:
    started = []
    for row in _rows("status = :st AND schedule_type = :t AND due_date IS NOT NULL AND due_date <= :now",
                     {"st": WAITING, "t": AT, "now": utcnow}, order="due_date ASC, id ASC"):
        if release(row["id"]):
            started.append(row["id"])
    return started


def _night_decision(now: datetime.datetime) -> dict:
    """Whether the next night job may start now, and why not. Updates the
    shift's state: which window opened it and whether the morning ended it."""
    window = get_window()
    opened = window_opening(now, window)
    state = _load_state()
    queue = waiting_rows(NIGHT_SHIFT)
    out: dict[str, Any] = {"window": window, "in_window": opened is not None, "queue": len(queue),
                           "next": queue[0]["id"] if queue else None, "may_start": False}

    if opened is not None:
        if state.get("opened") != opened.isoformat():
            state = {"opened": opened.isoformat(), "ended": False}
            _save_state(state)
    elif not state.get("opened") or state.get("ended"):
        out["hold"] = "the window is closed"
        return out
    out["opened"] = state.get("opened")

    who = presence(now)
    out["presence"] = who
    if opened is None:
        # Past the end time the shift keeps going only while the machine is
        # still unattended and there is work left; either ends it until tonight.
        if who["present"] or not queue:
            state["ended"] = True
            _save_state(state)
            out["hold"] = "the window is closed"
            return out
        out["overtime"] = True
    if not queue:
        out["hold"] = "no jobs are waiting for the night shift"
        return out
    if is_paused():
        out["hold"] = "the night shift is paused"
        return out
    if who["present"]:
        out["hold"] = who["reason"]
        return out
    for check in (_other_work, _disk_hold):
        hold = check()
        if hold:
            out["hold"] = hold
            return out
    out["may_start"] = True
    return out


def tick(now: Optional[datetime.datetime] = None) -> dict:
    """One scheduler pass: start `at` jobs whose time has come, then at most
    one night-shift job. Cheap when nothing is scheduled: two indexed reads."""
    now = now or datetime.datetime.now()
    try:
        from backend.services import job_handoff

        job_handoff.sync(_session)
    except Exception as e:  # noqa: BLE001 - following a batch must not stop releases
        logger.error("night shift: could not follow handed-off jobs: %s", e)
    result: dict[str, Any] = {"at_started": _release_due(_utcnow()), "night_started": None}
    state = _load_state()
    if not waiting_rows(NIGHT_SHIFT) and (not state.get("opened") or state.get("ended")):
        return result
    decision = _night_decision(now)
    result["night"] = decision
    if decision["may_start"] and release(decision["next"]):
        result["night_started"] = decision["next"]
        logger.info("night shift: started job %s", decision["next"])
    elif decision.get("hold") and decision["queue"]:
        logger.info("night shift: holding (%s)", decision["hold"])
    return result


def status(now: Optional[datetime.datetime] = None) -> dict:
    """What the strip and the morning card show. Reads only; the decision
    reported is the one the next pass would take."""
    now = now or datetime.datetime.now()
    window = get_window()
    state = _load_state()
    opened = window_opening(now, window)
    queue = waiting_rows(NIGHT_SHIFT)
    who = presence(now)
    hold = None
    if is_paused():
        hold = "the night shift is paused"
    elif opened is None and (not state.get("opened") or state.get("ended")):
        hold = "the window is closed"
    elif who["present"]:
        hold = who["reason"]
    else:
        hold = _other_work() or _disk_hold()
    since = state.get("opened")
    ran = []
    if since:
        ran = _rows(
            "schedule_type IN ('night_shift', 'at') AND last_run_at IS NOT NULL AND last_run_at >= :since",
            {"since": datetime.datetime.fromisoformat(since)}, order="last_run_at ASC",
        )
    start, end = window.split("-")
    return {
        "window": {"start": start, "end": end},
        "in_window": opened is not None,
        "paused": is_paused(),
        "away_minutes": int(AWAY_S // 60),
        "desktop_idle_available": who["desktop_source"] is not None,
        "presence": who,
        "hold": hold,
        "waiting": [_public(r) for r in queue],
        "at": [_public(r) for r in waiting_rows(AT)],
        "shift": {
            "opened": since,
            "ended": bool(state.get("ended")),
            "done": [_public(r) for r in ran if r["status"] in ("completed", "complete")],
            "failed": [_public(r) for r in ran if r["status"] in ("failed", "error")],
            "running": [_public(r) for r in ran if r["status"] in ("queued", "in-progress", "running")],
        },
    }


def _public(row: dict) -> dict:
    due = row.get("due_date")
    late = False
    if row.get("schedule_type") == AT and due is not None:
        ran_utc = None
        if row.get("status") == WAITING:
            ran_utc = _utcnow()
        elif row.get("last_run_at") is not None:
            ran_utc = row["last_run_at"].astimezone().astimezone(datetime.timezone.utc).replace(tzinfo=None)
        late = ran_utc is not None and (ran_utc - due).total_seconds() > LATE_AFTER_S
    return {
        "id": row["id"],
        "job_id": f"task:{row['id']}",
        "name": row.get("name"),
        "type": row.get("type"),
        "status": row.get("status"),
        "when": row.get("schedule_type"),
        "at": due.replace(tzinfo=datetime.timezone.utc).isoformat() if due is not None else None,
        "late": late,
        "queue_rank": row.get("queue_rank"),
        "progress": row.get("progress"),
        "error": row.get("error_message"),
        "started_at": row["last_run_at"].isoformat() if row.get("last_run_at") else None,
    }
