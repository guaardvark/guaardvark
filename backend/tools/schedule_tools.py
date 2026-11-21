"""Chat tools for scheduling: make something later instead of now.

  schedule_job    "schedule a video of a fox for tonight", "write that report at 6am"
  scheduled_jobs  "what's scheduled?", "remove job 91", "run job 91 now"

Both go through /api/night-shift (backend/services/night_shift.py), the same
routes the Jobs page uses, so a job scheduled in chat shows in the Scheduled
card and can be reordered, moved or removed there.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict

from backend.services import schedulable
from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import BackendError, request_json

logger = logging.getLogger(__name__)

_CHAT_FEATURES = sorted(f.id for f in schedulable.FEATURES.values() if not f.person_only)


def _when_text(when: str, at_local: datetime.datetime | None) -> str:
    if when == schedulable.AT and at_local is not None:
        return f"at {at_local:%H:%M on %a %b %d}"
    return "on the night shift (it starts once the machine has been left alone inside the night window)"


class ScheduleJobTool(BaseTool):
    """Save a job to run later."""

    name = "schedule_job"
    read_only = False
    destructive = False
    description = (
        "Schedule work to run LATER instead of now: on the night shift (when the machine is left "
        "alone at night) or at a set time. Use whenever the user says schedule, queue for tonight, "
        "overnight, on the night shift, 'at 3am', 'tomorrow morning', 'in 2 hours' about something "
        "to make or run. Do NOT also call the generate tool: this tool saves the job and the "
        "scheduler runs it. Give 'feature' and 'prompt' (image, video, infographic, music, speech, "
        "film_crew, file, csv, code, research, agent_task), or 'tool' and 'arguments' to schedule "
        "another tool call exactly. Returns the job number; the job shows on the Jobs page under "
        "Scheduled, where it can be reordered, moved or removed."
    )
    parameters = {
        "feature": ToolParameter(
            name="feature", type="string", required=False, enum=_CHAT_FEATURES,
            description="What to make: " + "; ".join(
                f"{f.id} = {f.label.lower()}" for f in schedulable.FEATURES.values() if not f.person_only),
        ),
        "prompt": ToolParameter(
            name="prompt", type="string", required=False,
            description="With feature: the one text the feature works from (the scene for an image or "
                        "video, the sound for music, the words for speech, what a file should contain).",
        ),
        "when": ToolParameter(
            name="when", type="string", required=False, default="night_shift", enum=["night_shift", "at"],
            description="'night_shift' (default): tonight, once nobody is at the machine. 'at': at 'time'.",
        ),
        "time": ToolParameter(
            name="time", type="string", required=False,
            description="For when='at': local time as 'YYYY-MM-DDTHH:MM', or 'in 2 hours', 'tomorrow at 9am', "
                        "'18:30'. A clock time without a day is the next time the clock reads it.",
        ),
        "count": ToolParameter(
            name="count", type="int", required=False, default=1, minimum=1, maximum=schedulable.MAX_COUNT,
            description="How many separate jobs to save from this prompt (default 1).",
        ),
        "options": ToolParameter(
            name="options", type="dict", required=False,
            description="With feature: extra arguments for its tool, e.g. {\"model\": \"wan22-5b\", "
                        "\"duration_s\": 5} for video or {\"seconds\": 60} for music.",
        ),
        "tool": ToolParameter(
            name="tool", type="string", required=False,
            description="Instead of feature: the exact tool to run later, one of "
                        + ", ".join(sorted(schedulable.SCHEDULABLE_TOOLS - schedulable.PERSON_ONLY_TOOLS)) + ".",
        ),
        "arguments": ToolParameter(
            name="arguments", type="dict", required=False,
            description="With tool: its arguments, exactly as that tool takes them.",
        ),
    }

    def execute(self, feature: str = None, prompt: str = None, when: str = None, time: str = None,
                count: int = None, options: Dict[str, Any] = None, tool: str = None,
                arguments: Dict[str, Any] = None, **kwargs) -> ToolResult:
        when = (when or "").strip().lower() or (schedulable.AT if time else schedulable.NIGHT_SHIFT)
        at_local = None
        try:
            if when == schedulable.AT or time:
                parsed = schedulable.parse_time_argument(time)
                when, at_local = parsed.when, parsed.at
            elif when != schedulable.NIGHT_SHIFT:
                return ToolResult(success=False, error="when must be 'night_shift' or 'at'")
            n = max(1, min(int(count or 1), schedulable.MAX_COUNT))
        except (TypeError, ValueError) as e:
            return ToolResult(success=False, error=str(e))

        body: Dict[str, Any] = {"when": when}
        if at_local is not None:
            body["at"] = at_local.astimezone().isoformat()
        feature = (feature or "").strip().lower()
        tool = (tool or "").strip()
        if feature:
            spec = schedulable.FEATURES.get(feature)
            if spec is None:
                return ToolResult(success=False, error=f"feature must be one of {_CHAT_FEATURES}")
            if spec.person_only:
                return ToolResult(success=False, error=(
                    f"{spec.label} needs your approval when it runs, so chat cannot schedule it. "
                    f"Schedule it yourself: Jobs page, Scheduled card, Schedule a job."))
            if not (prompt or "").strip():
                return ToolResult(success=False, error=f"prompt is required: {spec.hint.lower()}")
            body.update(feature=feature, prompt=prompt.strip(), count=n,
                        options=options if isinstance(options, dict) else None)
            what = f"{spec.label}: {prompt.strip()[:80]}"
        elif tool:
            if tool in schedulable.PERSON_ONLY_TOOLS:
                return ToolResult(success=False, error=(
                    f"{tool} needs your approval when it runs, so chat cannot schedule it. "
                    "Schedule it yourself: Jobs page, Scheduled card, Schedule a job."))
            body.update(tool=tool, arguments=arguments if isinstance(arguments, dict) else {})
            what = tool
        else:
            return ToolResult(success=False, error="Give feature and prompt, or tool and arguments.")

        try:
            data = request_json("POST", "/api/night-shift/jobs", payload=body).data or {}
        except BackendError as e:
            return ToolResult(success=False, error=f"Not scheduled: {e}")
        jobs = data.get("jobs") or [data]
        ids = [j.get("task_id") for j in jobs if j.get("task_id") is not None]
        if not ids:
            return ToolResult(success=False, error="The backend saved no job.")
        numbers = ", ".join(str(i) for i in ids)
        output = (f"Scheduled {_when_text(when, at_local)}: {what}. "
                  f"Job {numbers}." if len(ids) == 1 else
                  f"Scheduled {len(ids)} jobs {_when_text(when, at_local)}: {what}. Jobs {numbers}.")
        output += " Nothing has been made yet. Open the Jobs page (Scheduled) to reorder, move or remove it."
        return ToolResult(success=True, output=output, metadata={
            "task_ids": ids, "when": when, "at": body.get("at"), "jobs_url": "/tasks"})


class ScheduledJobsTool(BaseTool):
    """Read or change what is scheduled."""

    name = "scheduled_jobs"
    read_only = False
    destructive = False
    description = (
        "The jobs scheduled for later (night shift and set times): list them, remove one, start one "
        "now, or move one to another time. Use for 'what is scheduled?', 'what runs tonight?', "
        "'remove job 91', 'run job 91 now', 'move job 91 to 6am'. action 'list' (default) also "
        "reports the night window and why the night shift is waiting."
    )
    parameters = {
        "action": ToolParameter(
            name="action", type="string", required=False, default="list",
            enum=["list", "remove", "run_now", "move"],
            description="list (default), remove, run_now, or move (needs when/time).",
        ),
        "job": ToolParameter(
            name="job", type="int", required=False,
            description="The job number, for remove, run_now and move.",
        ),
        "when": ToolParameter(
            name="when", type="string", required=False, enum=["night_shift", "at"],
            description="For move: 'night_shift' or 'at' (with time).",
        ),
        "time": ToolParameter(
            name="time", type="string", required=False,
            description="For move with when='at': as in schedule_job.",
        ),
    }

    def execute(self, action: str = None, job: int = None, when: str = None, time: str = None,
                **kwargs) -> ToolResult:
        action = (action or "list").strip().lower()
        try:
            if action == "list":
                return self._list()
            if job is None:
                return ToolResult(success=False, error=f"job is required for {action}: the job number")
            job = int(job)
            if action == "remove":
                request_json("DELETE", f"/api/night-shift/jobs/{job}")
                return ToolResult(success=True, output=f"Job {job} was removed from the schedule.",
                                  metadata={"task_id": job})
            if action == "run_now":
                request_json("POST", f"/api/night-shift/jobs/{job}/schedule", payload={"when": "now"})
                return ToolResult(success=True, output=f"Job {job} has been started.",
                                  metadata={"task_id": job})
            if action == "move":
                body: Dict[str, Any] = {"when": (when or "").strip().lower() or
                                        (schedulable.AT if time else schedulable.NIGHT_SHIFT)}
                at_local = None
                if body["when"] == schedulable.AT or time:
                    parsed = schedulable.parse_time_argument(time)
                    body["when"], at_local = parsed.when, parsed.at
                    if at_local is not None:
                        body["at"] = at_local.astimezone().isoformat()
                request_json("POST", f"/api/night-shift/jobs/{job}/schedule", payload=body)
                return ToolResult(success=True, output=f"Job {job} now runs {_when_text(body['when'], at_local)}.",
                                  metadata={"task_id": job, **body})
            return ToolResult(success=False, error="action must be list, remove, run_now or move")
        except (TypeError, ValueError) as e:
            return ToolResult(success=False, error=str(e))
        except BackendError as e:
            return ToolResult(success=False, error=str(e))

    def _list(self) -> ToolResult:
        data = request_json("GET", "/api/night-shift/status").data or {}
        window = data.get("window") or {}
        lines = [f"Night shift window {window.get('start')} to {window.get('end')}"
                 + (", paused" if data.get("paused") else "")
                 + (f". Waiting because {data['hold']}." if data.get("hold") else ".")]

        def row(r: dict) -> str:
            text = f"job {r.get('id')}: {r.get('name')}"
            if r.get("at"):
                at = datetime.datetime.fromisoformat(r["at"]).astimezone()
                text += f" (at {at:%H:%M on %a %b %d}{', late' if r.get('late') else ''})"
            return text

        for title, key in (("Night shift, in order", "waiting"), ("At a time", "at"),
                           ("Held (no time set)", "held"), ("Repeating", "recurring")):
            rows = data.get(key) or []
            if rows:
                lines.append(f"{title} ({len(rows)}):")
                lines.extend(f"- {row(r)}" for r in rows[:40])
                if len(rows) > 40:
                    lines.append(f"- and {len(rows) - 40} more")
        running = (data.get("shift") or {}).get("running") or []
        if running:
            lines.append("Running now: " + "; ".join(row(r) for r in running))
        if len(lines) == 1:
            lines.append("Nothing is scheduled.")
        total = sum(len(data.get(k) or []) for k in ("waiting", "at", "held", "recurring"))
        return ToolResult(success=True, output="\n".join(lines), metadata={"scheduled": total})
