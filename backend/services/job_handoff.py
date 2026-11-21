"""Long jobs as scheduler rows: started on the backend's own queue, then followed.

A render batch runs in the backend process and takes minutes to hours. The
scheduler's worker runs one task at a time and the release beat runs on it,
so a task that waited for a batch would stop every other job. A hand-off
kind is started with the same HTTP call its page makes, the executor returns
at once, and the every-minute beat copies the job's progress onto the
``tasks`` row until the job ends.

A kind is declared once in ``HANDOFFS``; the executor, the beat, the
schedule API and the Jobs page read it from there. To make another feature
schedulable, add its entry: the routes its page already calls, and how its
status reply reads.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

_START_TIMEOUT_S = 120
_STATUS_TIMEOUT_S = 20

_ENDED = {"completed": "completed", "complete": "completed", "done": "completed", "error": "failed",
          "failed": "failed", "cancelled": "cancelled", "canceled": "cancelled"}

# The chat tool whose scheduled call is followed as a batch once it has
# queued one, so the row ends when the render does and not when it is queued.
_TOOL_BATCH_KIND = {"generate_video": "video_batch", "generate_image": "image_batch"}


def _dig(data: Any, path: Optional[str]) -> Any:
    """``data['a']['b']`` for the path ``'a.b'``; None where a step is missing."""
    if not path:
        return None
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


@dataclass(frozen=True)
class Handoff:
    """One schedulable job kind.

    A start route receives the row's ``workflow_config['request']`` as its
    JSON body. A kind with more than one way to start (video from text or
    from images) names them in ``starts`` and the row picks one with
    ``workflow_config['variant']``. ``{name}`` in a route is filled from
    ``workflow_config['path']`` (the Cast member a training run is for), and
    ``{id}`` with the job the start reply named (``id_keys``, first one found)
    or with the path value ``id_from_path`` names.

    ``status`` is read once a minute. The default reader takes the fields
    named here; a reply shaped another way gets its own ``read`` function
    returning ``{"ended": None|"completed"|"failed"|"cancelled", "percent",
    "summary", "error"}``. A kind without ``status`` has nothing to follow:
    its row is done when the start call is accepted, and ``started_note``
    says where the work shows.
    """

    label: str
    page: str
    starts: dict
    status: Optional[str] = None
    cancel: Optional[str] = None
    # Routes that hold the job without losing it and let it go on.
    pause: Optional[str] = None
    resume: Optional[str] = None
    cancel_method: str = "POST"
    cancel_body: Optional[dict] = None
    id_keys: tuple = ("batch_id",)
    id_from_path: Optional[str] = None
    path_params: tuple = ()
    state: str = "status"
    total: Optional[str] = None
    done: Optional[str] = None
    failed: Optional[str] = None
    percent: Optional[str] = None
    percent_scale: float = 1.0
    error: tuple = ("failure.message", "error")
    noun: str = "items"
    read: Optional[Callable[[dict], dict]] = field(default=None, compare=False)
    started_note: str = ""
    # The start body may be empty (the route takes everything from its path).
    empty_body: bool = False
    # Reply fields that mean the work finished inside the start call (a short
    # song comes back with its file instead of a job to follow).
    done_keys: tuple = ()
    # Body fields a scheduled start always sends, whatever the saved request
    # says: a scheduled run has nobody waiting on the reply.
    always: Optional[dict] = None


def _read_cast_lora(data: dict) -> dict:
    state = str(data.get("training_status") or "")
    ended = {"trained": "completed", "failed": "failed", "untrained": "cancelled"}.get(state)
    return {"ended": ended, "percent": None,
            "summary": "LoRA trained" if ended == "completed" else "LoRA training ended",
            "error": data.get("training_error")}


def _read_music_video(data: dict) -> dict:
    stage = str(data.get("current_stage") or "")
    status = str(data.get("status") or "").lower()
    ended = {"complete": "completed", "failed": "failed", "cancelled": "cancelled"}.get(stage)
    if ended is None and status.startswith(("failed", "cancelled")):
        ended = "failed" if status.startswith("failed") else "cancelled"
    # Back at the gate with nothing rendering: the render was stopped or refused.
    if ended is None and stage == "awaiting_approval":
        ended = "cancelled"
    blob = data.get("error_blob") if isinstance(data.get("error_blob"), dict) else {}
    return {"ended": ended, "percent": _dig(data, "progress.overall_pct"),
            "summary": f"{int(data.get('clips_done') or 0)} of {int(data.get('clip_count') or 0)} clips done",
            "error": blob.get("message")}


def _read_lesson_replay(data: dict) -> dict:
    attempt = data.get("attempt")
    if isinstance(attempt, dict):
        total = int(attempt.get("total_steps") or 0)
        step = int(attempt.get("step_index") or 0)
        return {"ended": None, "percent": int(100 * step / total) if total else 0, "summary": "", "error": None}
    last = data.get("last_attempt") if isinstance(data.get("last_attempt"), dict) else {}
    summary = f"{int(last.get('steps_completed') or 0)} of {int(last.get('total_steps') or 0)} steps done"
    return {"ended": "completed" if last.get("success") else "failed", "percent": None,
            "summary": summary, "error": last.get("failure_reason") or summary}


_FILM_STAGE_PERCENT = {"screenwriting": 10, "casting": 25, "cinematography": 40, "storyboard_gen": 55,
                       "awaiting_approval": 70, "rendering": 85}


def _read_film_crew(data: dict) -> dict:
    """A scheduled leg of a production ends where the crew next needs a
    person (casting, storyboard approval) or when the film is finished."""
    stage = str(data.get("current_stage") or "")
    status = str(data.get("status") or "").lower()
    blob = data.get("error_blob") if isinstance(data.get("error_blob"), dict) else {}
    ended = summary = None
    if status.startswith("failed"):
        ended, summary = "failed", "The production failed"
    elif status.startswith("cancelled"):
        ended, summary = "cancelled", "The production was cancelled"
    elif stage == "complete" or status == "complete":
        ended, summary = "completed", "The film is finished"
    elif stage == "casting":
        ended, summary = "completed", "The screenplay is written; casting waits for you on the Film Crew page"
    elif stage == "awaiting_approval":
        ended, summary = "completed", "The storyboards are drawn; approve them on the Film Crew page to render"
    return {"ended": ended, "percent": _FILM_STAGE_PERCENT.get(stage), "summary": summary or "",
            "error": blob.get("message") or blob.get("error")}


def _read_tool_job(data: dict) -> dict:
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    ended = {"done": "completed", "failed": "failed"}.get(str(data.get("status") or ""))
    output = result.get("output")
    summary = output if isinstance(output, str) else ""
    return {"ended": ended, "percent": None,
            "summary": summary.strip()[:400] or ("Done" if ended == "completed" else ""),
            "error": result.get("error"), "metadata": result.get("metadata") or {}, "tool": data.get("tool")}


_BATCH_IMAGE = dict(
    status="/api/batch-image/status/{id}", cancel="/api/batch-image/cancel/{id}",
    total="total_images", done="completed_images", failed="failed_images",
    percent="progress_percentage", noun="images",
)

HANDOFFS: dict[str, Handoff] = {
    "image_batch": Handoff(
        label="Image batch", page="/batch-images",
        starts={"prompts": "/api/batch-image/generate/prompts"}, **_BATCH_IMAGE,
    ),
    "video_batch": Handoff(
        label="Video batch", page="/video",
        starts={
            "text": "/api/batch-video/generate/text",
            "image": "/api/batch-video/generate/image",
            "references": "/api/batch-video/generate/references",
        },
        status="/api/batch-video/status/{id}",
        cancel="/api/batch-video/batch/{id}/cancel",
        pause="/api/batch-video/batch/{id}/pause", resume="/api/batch-video/batch/{id}/resume",
        total="total_videos", done="completed_videos", failed="failed_videos",
        percent="progress_pct", noun="clips",
    ),
    "infographic": Handoff(
        label="Infographic", page="/infographic",
        starts={"batch": "/api/infographic/batch"}, **_BATCH_IMAGE,
    ),
    "cast_lora": Handoff(
        label="Cast LoRA training", page="/cast",
        starts={"train": "/api/cast-library/subjects/{subject_id}/train"},
        path_params=("subject_id",), id_from_path="subject_id", empty_body=True,
        status="/api/cast-library/subjects/{id}",
        cancel="/api/cast-library/subjects/{id}/train/cancel",
        read=_read_cast_lora,
    ),
    "model_training": Handoff(
        label="Model training", page="/training",
        starts={"new": "/api/training/jobs", "existing": "/api/training/jobs/{job_id}/start"},
        id_keys=("id",), empty_body=True, always={"start_immediately": True},
        status="/api/training/jobs/{id}", cancel="/api/training/jobs/{id}/cancel",
        percent="progress", error=("error_message",), noun="steps",
    ),
    "music_video": Handoff(
        label="Music video render", page="/music-video",
        starts={"render": "/api/music-video/{mv_id}/approve"},
        path_params=("mv_id",), id_from_path="mv_id", empty_body=True,
        status="/api/music-video/{id}", cancel="/api/music-video/{id}/cancel",
        read=_read_music_video,
    ),
    "upscale": Handoff(
        label="Video upscale", page="/upscaling",
        starts={"video": "/api/upscaling/upscale/video"},
        id_keys=("job_id",),
        status="/api/upscaling/jobs/{id}", cancel="/api/upscaling/jobs/{id}", cancel_method="DELETE",
        percent="progress", percent_scale=100.0, noun="files",
    ),
    "music": Handoff(
        label="Song", page="/audio",
        starts={"music": "/api/audio-foundry/generate/music"},
        id_keys=("job_id",), always={"async": True}, done_keys=("path",),
        status="/api/audio-foundry/jobs/{id}", cancel="/api/audio-foundry/jobs/{id}/cancel",
        noun="songs",
    ),
    # One leg of a production per job: write the screenplay (to casting), draw
    # the storyboards (to approval), or render the film. Each stops where the
    # crew needs a person.
    "film_crew": Handoff(
        label="Film Crew", page="/film-crew",
        starts={"start": "/api/production",
                "storyboards": "/api/production/{prod_id}/casting/confirm",
                "render": "/api/production/{prod_id}/storyboard/approve"},
        id_keys=("id", "production_id"), id_from_path="prod_id", empty_body=True,
        status="/api/production/{id}", read=_read_film_crew,
    ),
    "swarm": Handoff(
        label="Coding swarm", page="/swarm",
        starts={"launch": "/api/swarm/launch"},
        started_note="The swarm is running; its agents and merges are on the Swarm page.",
    ),
    "lesson_replay": Handoff(
        label="Agent lesson replay", page="/training",
        starts={"replay": "/api/agent-control/learn/demonstrations/{demo_id}/attempt"},
        path_params=("demo_id",), id_from_path="demo_id", empty_body=True,
        status="/api/agent-control/learn/status",
        read=_read_lesson_replay,
    ),
    # Any chat tool, with its arguments, run as a tool job (backend/services/tool_jobs.py).
    "tool_call": Handoff(
        label="Tool", page="/tools",
        starts={"run": "/api/tools/execute"},
        id_keys=("job_id",), always={"as_job": True},
        status="/api/tools/jobs/{id}",
        read=_read_tool_job,
    ),
}


def is_handoff(task_type: Optional[str]) -> bool:
    return task_type in HANDOFFS


def _call(method: str, path: str, payload: Any = None, timeout: float = _STATUS_TIMEOUT_S):
    """One call to this checkout's backend, with its API key where it has one."""
    from backend.utils.backend_http import request_json

    return request_json(method, path, payload=payload, read_timeout=timeout, own_backend=True)


def check_request(task_type: str, request_body: Any, variant: Optional[str], path: Any) -> tuple[str, dict]:
    """The variant and path values a job of this kind will start with.
    Raises ValueError with the reason a request cannot be saved."""
    kind = HANDOFFS.get(task_type)
    if kind is None:
        raise ValueError(f"type must be one of {sorted(HANDOFFS)}")
    variant = variant or next(iter(kind.starts))
    if variant not in kind.starts:
        raise ValueError(f"variant must be one of {sorted(kind.starts)}")
    if not isinstance(request_body, dict) or (not request_body and not kind.empty_body):
        raise ValueError("request must be the body the job is started with")
    path = path if isinstance(path, dict) else {}
    try:
        kind.starts[variant].format(**{**path, "id": ""})
    except KeyError as e:
        raise ValueError(f"path.{e.args[0]} is required for this job") from e
    return variant, {k: str(v) for k, v in path.items()}


def start(task: dict) -> dict:
    """Start the row's job on the backend. Returns ``{"handoff": {...}}`` for
    the row's ``handler_config`` (a job to follow), ``{"output": ...}`` (a
    kind with nothing to follow, already under way), or ``{"error": ...}``."""
    from backend.utils.backend_http import BackendError

    kind = HANDOFFS[task["type"]]
    config = task.get("workflow_config") or {}
    try:
        variant, path = check_request(task["type"], config.get("request") or {}, config.get("variant"),
                                      config.get("path"))
    except ValueError as e:
        return {"error": f"the job has no usable saved request ({e})"}
    try:
        body = {**(config.get("request") or {}), **(kind.always or {})}
        data = _call("POST", kind.starts[variant].format(**path), body, _START_TIMEOUT_S).data
    except BackendError as e:
        return {"error": str(e)}
    if isinstance(data, dict) and data.get("success") is False:
        return {"error": str(data.get("error") or data.get("message") or "the backend refused the job")}
    if not kind.status:
        return {"output": kind.started_note or f"{kind.label} started."}
    job_id = path.get(kind.id_from_path) if kind.id_from_path else None
    if job_id is None and isinstance(data, dict):
        job_id = next((_dig(data, k) for k in kind.id_keys if _dig(data, k) not in (None, "")), None)
    if job_id in (None, ""):
        if isinstance(data, dict) and any(data.get(k) for k in kind.done_keys):
            return {"output": f"{kind.label} finished."}
        return {"error": "the backend started the job without naming it"}
    return {"handoff": {"kind": task["type"], "batch_id": str(job_id), "path": path,
                        "started_at": datetime.datetime.now().isoformat(timespec="seconds")}}


def _ref(handler_config: Any) -> Optional[dict]:
    if isinstance(handler_config, str):
        try:
            handler_config = json.loads(handler_config)
        except ValueError:
            return None
    ref = handler_config.get("handoff") if isinstance(handler_config, dict) else None
    return ref if isinstance(ref, dict) and ref.get("batch_id") else None


def cancel(task_type: Optional[str], handler_config: Any) -> bool:
    """Cancel the job a handed-off row is following. False when the row has
    no job yet, its kind cannot be cancelled, or the backend did not cancel it."""
    from backend.utils.backend_http import BackendError

    ref = _ref(handler_config)
    if not is_handoff(task_type) or not ref:
        return False
    kind = HANDOFFS.get(ref.get("kind") or task_type)
    if kind is None or not kind.cancel:
        return False
    values = {**(ref.get("path") or {}), "id": ref["batch_id"]}
    body = {k: str(v).format(**values) for k, v in kind.cancel_body.items()} if kind.cancel_body else None
    try:
        _call(kind.cancel_method, kind.cancel.format(**values), body)
        return True
    except BackendError as e:
        logger.warning("could not cancel %s %s: %s", kind.label, ref["batch_id"], e)
        return False


def set_paused(task_type: Optional[str], handler_config: Any, paused: bool) -> bool:
    """Pause or resume the job a handed-off row is following. False when the
    row has no job yet, its kind cannot be paused, or the backend refused."""
    from backend.utils.backend_http import BackendError

    ref = _ref(handler_config)
    kind = HANDOFFS.get((ref or {}).get("kind") or task_type or "")
    route = (kind.pause if paused else kind.resume) if kind else None
    if not ref or not route:
        return False
    try:
        _call("POST", route.format(**{**(ref.get("path") or {}), "id": ref["batch_id"]}))
        return True
    except BackendError as e:
        logger.warning("could not %s %s %s: %s", "pause" if paused else "resume", kind.label, ref["batch_id"], e)
        return False


def can_pause(task_type: Optional[str], handler_config: Any) -> bool:
    ref = _ref(handler_config)
    kind = HANDOFFS.get((ref or {}).get("kind") or task_type or "")
    return bool(ref and kind and kind.pause)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _read_fields(kind: Handoff, data: dict) -> dict:
    """The default status reader: the fields the kind names."""
    total, done, failed = (int(_dig(data, k) or 0) if k else 0 for k in (kind.total, kind.done, kind.failed))
    try:
        percent = float(_dig(data, kind.percent)) * kind.percent_scale
    except (TypeError, ValueError):
        percent = 100 * (done + failed) / total if total else 0
    word = str(_dig(data, kind.state) or "").lower()
    ended = _ENDED.get(word)
    if total:
        summary = f"{done} of {total} {kind.noun} done" + (f", {failed} failed" if failed else "")
        # A batch that ended with nothing rendered failed, whatever word it ended on.
        if ended == "completed" and done == 0:
            ended = "failed"
    else:
        summary = f"{kind.label} finished" if ended == "completed" else f"{kind.label} ended"
    error = next((_dig(data, k) for k in kind.error if _dig(data, k)), None)
    return {"ended": ended, "percent": percent, "summary": summary, "error": error, "paused": word == "paused"}


def sync(session_factory) -> dict:
    """Copy each running hand-off's job state onto its row.

    Every pass stamps ``updated_at``, which is what keeps stuck-job recovery
    from re-running a row whose job is still going. No answer from the
    backend leaves the row as it is: a restart is not a failure, and video
    batches pick up again from disk. A job the backend no longer knows
    fails the row.
    """
    from backend.utils.backend_http import BackendError

    out = {"followed": 0, "ended": 0}
    s = session_factory()
    try:
        rows = s.execute(
            text("SELECT id, type, handler_config FROM tasks WHERE status IN ('in-progress', 'paused') "
                 "AND type = ANY(:kinds)"),
            {"kinds": list(HANDOFFS)},
        ).fetchall()
        for task_id, task_type, config in rows:
            ref = _ref(config)
            if not ref:
                continue  # the executor has not recorded the job yet
            kind = HANDOFFS.get(ref.get("kind") or task_type)
            if kind is None or not kind.status:
                continue
            out["followed"] += 1
            update: dict[str, Any] = {"utc": _utcnow(), "id": task_id}
            values = {**(ref.get("path") or {}), "id": ref["batch_id"]}
            try:
                data = _call("GET", kind.status.format(**values)).data
            except BackendError as e:
                if e.status == 404:
                    reason = (e.body or {}).get("reason") if isinstance(e.body, dict) else None
                    update["err"] = ("the backend restarted while this job was running" if reason == "restarted"
                                     else f"{kind.label} {ref['batch_id']} is no longer known to the backend")
                    s.execute(text("UPDATE tasks SET status = 'failed', error_message = :err, updated_at = :utc "
                                   "WHERE id = :id AND status IN ('in-progress', 'paused')"), update)
                    out["ended"] += 1
                else:
                    s.execute(text("UPDATE tasks SET updated_at = :utc WHERE id = :id AND status IN ('in-progress', 'paused')"),
                              update)
                s.commit()
                continue
            if not isinstance(data, dict):
                data = {}
            state = (kind.read or (lambda d: _read_fields(kind, d)))(data)
            ended = state.get("ended")
            try:
                percent = max(0, min(int(float(state.get("percent"))), 99))
            except (TypeError, ValueError):
                percent = None
            next_kind = _TOOL_BATCH_KIND.get(str(state.get("tool") or ""))
            next_id = (state.get("metadata") or {}).get("batch_id")
            if ended == "completed" and next_kind and next_id and (state.get("metadata") or {}).get("queued"):
                # The tool queued a render and returned: follow the render.
                ref = {**ref, "kind": next_kind, "batch_id": str(next_id), "path": {}}
                s.execute(text("UPDATE tasks SET handler_config = CAST(:config AS JSON), updated_at = :utc "
                               "WHERE id = :id AND status IN ('in-progress', 'paused')"),
                          {**update, "config": json.dumps({"handoff": ref})})
                s.commit()
                continue
            if ended is None:
                update["p"] = percent
                # A job a person paused shows as paused, and the night shift moves on.
                update["st"] = "paused" if state.get("paused") else "in-progress"
                s.execute(text("UPDATE tasks SET status = :st, progress = COALESCE(:p, progress), updated_at = :utc "
                               "WHERE id = :id AND status IN ('in-progress', 'paused')"), update)
                s.commit()
                continue
            summary = str(state.get("summary") or f"{kind.label} ended")
            update.update(
                st=ended, res=f"{summary} ({kind.label} {ref['batch_id']})"[:4000],
                err=None if ended == "completed" else str(state.get("error") or summary)[:1000],
                p=100 if ended == "completed" else percent,
            )
            s.execute(text("UPDATE tasks SET status = :st, result = :res, error_message = :err, "
                           "progress = COALESCE(:p, progress), updated_at = :utc "
                           "WHERE id = :id AND status IN ('in-progress', 'paused')"), update)
            s.commit()
            out["ended"] += 1
    finally:
        s.close()
    return out
