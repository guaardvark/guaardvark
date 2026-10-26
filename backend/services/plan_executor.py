"""Run an approved plan, one step at a time.

Each step is its own turn through AgentBrain.process, so the routing that
already handles pictures, video, the agent screen and tool calls does the
work, and a small model gets one short instruction at a time. The run stops
at a step that fails or is stopped; the person then retries it, skips it,
asks for a new plan for the rest, or stops.
"""

import base64
import logging
import mimetypes
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import event

from backend.services import plan_mode_data as pmd

logger = logging.getLogger(__name__)

# File names a step's tools reported making, and that a later step's action
# may name.
_FILE_NAME = re.compile(
    r"[\w-]+\.(?:md|txt|csv|json|html|py|js|jsx|ts|css|xml|yaml|yml|docx|xlsx|pdf"
    r"|png|jpe?g|webp|gif|mp4|webm|wav|mp3|flac)\b", re.I)

# The plan step a thread is running, so the rows it saves are tagged.
_step_tag = threading.local()


def _tag_step_rows(mapper, connection, target):
    tag = getattr(_step_tag, "value", None)
    if not tag:
        return
    extra = dict(target.extra_data or {})
    extra.setdefault("planId", tag["plan_id"])
    extra.setdefault("planStep", tag["index"])
    if target.role == "user":
        # The step's instruction stands in for the person's message; the chat
        # shows it as a step heading, not as something they typed.
        extra["messageType"] = "plan_step"
        extra["planStepTitle"] = tag["title"]
    target.extra_data = extra


def _install_listener():
    from backend.models import LLMMessage
    if not event.contains(LLMMessage, "before_insert", _tag_step_rows):
        event.listen(LLMMessage, "before_insert", _tag_step_rows)


def sweep_interrupted(app) -> int:
    """At boot: a plan left executing by a restart is paused, never resumed
    on its own."""
    try:
        with app.app_context():
            from backend.models import ChatPlan, db
            n = ChatPlan.query.filter_by(status="executing").update(
                {"status": "paused", "paused_reason": "interrupted"}, synchronize_session=False)
            db.session.commit()
            if n:
                logger.warning("Paused %d plan run(s) a restart interrupted", n)
            return n
    except Exception:
        logger.debug("plan sweep skipped", exc_info=True)
        return 0


def _attachment_data(url: str) -> Optional[str]:
    """A saved chat picture as base64, for a step that edits it."""
    try:
        from backend.config import UPLOAD_DIR
        rel = url.split("/uploads/", 1)[-1] if "/uploads/" in url else url.lstrip("/")
        path = (Path(UPLOAD_DIR) / rel).resolve()
        if not str(path).startswith(str(Path(UPLOAD_DIR).resolve())) or not path.is_file():
            return None
        return base64.b64encode(path.read_bytes()).decode()
    except Exception:
        return None


def _context(plan, index: int) -> str:
    lines = [f"You are carrying out step {index} of an approved plan. Do this step only."]
    if plan.goal:
        lines.append(f"The plan's goal: {plan.goal}")
    done = [s for s in plan.steps if s.get("status") == "done" and s.get("index", 0) < index]
    for s in done:
        lines.append(f"Step {s['index']} ({s['title']}) is done: {(s.get('summary') or '')[:pmd.STEP_SUMMARY_CHARS]}")
    return "\n".join(lines)


class _StepWatch:
    """Wraps the turn's emit for one step: tags events with the plan and step,
    keeps the step's chat:complete from ending the whole run in the browser,
    and notes what the step's tools did."""

    def __init__(self, emit_fn: Callable, plan_id: str, index: int):
        self.emit_fn = emit_fn
        self.plan_id = plan_id
        self.index = index
        self.tool_ok = 0
        self.tool_failed: List[str] = []
        self.response = ""
        self.images: List[Any] = []
        self.made: List[str] = []

    def __call__(self, event_name: str, payload: Dict[str, Any]):
        payload = dict(payload or {})
        payload["plan_id"] = self.plan_id
        payload["plan_step"] = self.index
        if event_name == "chat:tool_result":
            if payload.get("success") is False:
                self.tool_failed.append(str(payload.get("error") or payload.get("tool") or "tool failed")[:300])
            elif payload.get("success"):
                self.tool_ok += 1
        if event_name in ("chat:image", "chat:video"):
            self.tool_ok += 1
        if event_name == "chat:complete":
            self.response = str(payload.get("response") or "")
            self.images = payload.get("generated_images") or []
            for step in payload.get("steps") or []:
                for call in step.get("tool_calls") or []:
                    if call.get("success"):
                        self.tool_ok += 1
                        self.made += _FILE_NAME.findall(f"{call.get('params')} {call.get('output_preview')}")
            self.emit_fn("chat:plan_step_done", payload)
            return
        if event_name == "chat:error":
            # A step's error pauses the plan; the run's own chat:complete
            # reports it, so clients keep listening until then.
            self.tool_failed.append(str(payload.get("error") or "the step failed")[:300])
            self.emit_fn("chat:plan_step_error", payload)
            return
        if event_name == "chat:response":
            return
        self.emit_fn(event_name, payload)


def execute_plan(plan_id: str, *, session_id: str, emit_fn: Callable, app, agent_brain,
                 request_id: Optional[str] = None) -> None:
    """Run the plan's pending steps in order. Runs on the session's turn thread."""
    from backend.models import ChatPlan, db
    from backend.services.unified_chat_engine import begin_plan_run, end_plan_run, is_aborted

    _install_listener()
    request_id = request_id or str(uuid.uuid4())
    with app.app_context():
        plan = db.session.get(ChatPlan, plan_id)
        if plan is None:
            emit_fn("chat:error", {"error": "That plan no longer exists."})
            return
        mode = plan.approval_mode or "ask"
        tools = sorted({t for s in plan.steps for t in (s.get("tools") or [])})
        project_id = plan.project_id
        base_options = dict(plan.options or {})
        attachments = list(plan.attachments or [])
    begin_plan_run(session_id, mode, tools)
    outcome = "done"
    try:
        for index in range(1, 1 + _step_count(app, plan_id)):
            with app.app_context():
                plan = db.session.get(ChatPlan, plan_id)
                step = dict(plan.steps[index - 1])
                if step.get("status") in ("done", "skipped"):
                    continue
                context = _context(plan, index)
                made_before = {name.lower(): s["index"] for s in plan.steps[:index - 1]
                               if s.get("status") == "done" for name in s.get("made") or []}
            if is_aborted(session_id):
                outcome = "stopped"
                _set_step(app, plan_id, index, status="pending")
                break
            _set_step(app, plan_id, index, status="running")
            emit_fn("chat:plan_step", {"plan_id": plan_id, "index": index, "title": step["title"]})
            _emit_plan(app, emit_fn, plan_id)

            options = {
                **base_options,
                **pmd.STEP_KINDS.get(step.get("kind"), {}).get("options", {}),
                **(step.get("direct") or {}),
                "request_id": str(uuid.uuid4()),
                "plan_step": {"plan_id": plan_id, "index": index},
                "context": context,
                "skip_memory_capture": True,
            }
            image_data = None
            if attachments and step.get("kind") in ("image", "video"):
                image_data = _attachment_data(attachments[0])
            watch = _StepWatch(emit_fn, plan_id, index)
            _step_tag.value = {"plan_id": plan_id, "index": index, "title": step["title"]}
            try:
                result = agent_brain.process(
                    session_id=session_id, message=step["action"], options=options,
                    emit_fn=watch, app=app, project_id=project_id, image_data=image_data,
                    image_url=attachments[0] if image_data else None,
                ) or {}
            except Exception as e:  # noqa: BLE001 - a crashed step pauses the plan
                logger.error("plan %s step %d crashed: %s", plan_id, index, e, exc_info=True)
                result = {"success": False, "error": str(e)}
            finally:
                _step_tag.value = None

            summary = (watch.response or str(result.get("response") or ""))[:pmd.STEP_SUMMARY_CHARS]
            if is_aborted(session_id):
                outcome = "stopped"
                _set_step(app, plan_id, index, status="stopped", summary=summary)
                break
            error = _step_error(step, result, watch)
            if error and not watch.tool_ok and not watch.tool_failed:
                already = _made_earlier(step, made_before)
                if already:
                    error, summary = "", already
            if error:
                outcome = "step_failed"
                _set_step(app, plan_id, index, status="failed", summary=summary, error=error)
                break
            _set_step(app, plan_id, index, status="done", summary=summary,
                      made=sorted(set(watch.made))[:20] or None)
            _emit_plan(app, emit_fn, plan_id)
    finally:
        end_plan_run(session_id)
    _finish(app, emit_fn, plan_id, session_id, outcome, request_id, project_id)


def _step_count(app, plan_id: str) -> int:
    from backend.models import ChatPlan, db
    with app.app_context():
        plan = db.session.get(ChatPlan, plan_id)
        return len(plan.steps or []) if plan is not None else 0


def _made_earlier(step: Dict[str, Any], made_before: Dict[str, int]) -> str:
    """The step's summary when an earlier step already made a file it names,
    or "". A plan that writes a file's text in one step and saves it in the
    next runs no tool in the second when the first already saved it."""
    named = [n for n in _FILE_NAME.findall(f"{step.get('title')} {step.get('action')}")
             if n.lower() in made_before]
    return f"{named[0]} was already made in step {made_before[named[0].lower()]}." if named else ""


def _step_error(step: Dict[str, Any], result: Dict[str, Any], watch: _StepWatch) -> str:
    """Why a finished step counts as failed, or "" when it worked."""
    if result.get("success") is False:
        return str(result.get("error") or "The step reported a failure.")[:400]
    if step.get("kind") in pmd.TEXT_KINDS:
        return ""
    if step.get("kind") == "desktop" and result.get("success"):
        return ""
    if watch.tool_ok:
        return ""
    if watch.tool_failed:
        return watch.tool_failed[-1]
    return "Nothing ran for this step: no tool was called."


def _set_step(app, plan_id: str, index: int, **fields) -> None:
    from backend.models import ChatPlan, db
    with app.app_context():
        plan = db.session.get(ChatPlan, plan_id)
        if plan is None:
            return
        steps = [dict(s) for s in plan.steps]
        steps[index - 1].update({k: v for k, v in fields.items() if v is not None})
        plan.steps = steps
        db.session.commit()


def _emit_plan(app, emit_fn: Callable, plan_id: str) -> None:
    from backend.models import ChatPlan, db
    with app.app_context():
        plan = db.session.get(ChatPlan, plan_id)
        if plan is not None:
            emit_fn("chat:plan_update", {"plan": plan.to_dict()})


def _finish(app, emit_fn: Callable, plan_id: str, session_id: str, outcome: str,
            request_id: str, project_id) -> None:
    from backend.models import ChatPlan, db
    from backend.services.agent_brain import _persist_turn
    from backend.services.plan_mode import render_markdown

    with app.app_context():
        plan = db.session.get(ChatPlan, plan_id)
        if plan is None:
            return
        steps = plan.steps or []
        done = sum(1 for s in steps if s.get("status") == "done")
        if outcome == "done":
            plan.status = "done"
            plan.paused_reason = None
            plan.finished_at = datetime.now()
            text = f"Plan finished: {done} of {len(steps)} steps done."
        else:
            plan.status = "paused"
            plan.paused_reason = outcome
            stuck = next((s for s in steps if s.get("status") in ("failed", "stopped", "pending", "running")), None)
            if outcome == "step_failed" and stuck:
                text = (f"Plan paused at step {stuck['index']} ({stuck['title']}): {stuck.get('error') or 'it failed'}. "
                        "Retry it, skip it, ask for a new plan for the rest, or stop.")
            else:
                at = f" at step {stuck['index']}" if stuck else ""
                text = f"Plan stopped{at}. {done} of {len(steps)} steps done; continue when you are ready."
        plan.markdown = render_markdown(plan)
        db.session.commit()
        payload = plan.to_dict()
    emit_fn("chat:complete", {"response": text, "request_id": request_id, "plan": payload,
                              "plan_result": {"outcome": outcome, "plan_id": plan_id}})
    _persist_turn(app, session_id, "assistant", text,
                  {"messageType": "plan_result", "planId": plan_id, "plan": payload},
                  emit_fn=emit_fn, request_id=request_id, project_id=project_id)
