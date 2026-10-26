"""Plan mode: research an objective with read-only tools, then write a plan.

While a chat session is in plan mode, every turn comes here before any
routing (see chat_turn.route_turn). The chat model researches the request on
a registry that holds only research tools, so nothing can change while it
looks. A question is answered as usual; a request to do something ends with a
plan the person approves, edits or rejects. Running an approved plan is
plan_executor's job.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.services.agent_tools import ToolRegistry, ToolResult, is_plan_research_tool
from backend.services import plan_mode_data as pmd

logger = logging.getLogger(__name__)

# Options a client may never set: the server decides them each turn.
SERVER_ONLY_OPTIONS = ("plan_mode", "plan_step", "plan_mode_research", "complete_extra", "finalize_fn")

# Tools whose output can carry someone else's words into a plan.
_WEB_TOOLS = frozenset({"web_search", "fetch_url", "analyze_website", "browser_extract", "browser_get_html"})
# Step tools whose effect reaches outside the conversation or cannot be undone.
_SENSITIVE_TOOLS = frozenset({
    "system_command", "execute_python", "execute_javascript", "file_bulk_operation", "edit_code",
    "delete_memory", "outreach_draft_post", "outreach_approve_draft", "outreach_run_pass",
    "outreach_execute_intent", "request_publish", "launch_swarm", "gui_click", "gui_type",
    "gui_hotkey", "browser_click", "browser_fill", "browser_execute_js", "mcp_execute",
})
_PATH_LIKE = re.compile(r"(?:https?://[^\s)'\"`]+|(?<![\w/])(?:[\w.-]+/)+[\w.-]+|\b[\w-]+\.(?:py|js|jsx|ts|tsx|json|md|txt|csv|yaml|yml|html|css|sh|png|jpg|jpeg|mp4|wav|mp3|pdf|docx|xlsx)\b)")
_NUMBERED = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.+)$", re.M)


class PlanResearchRegistry(ToolRegistry):
    """The registry plan mode researches with: research tools only.

    The chat engine lists, selects and runs tools only through its registry,
    so a tool outside this one can be neither offered nor run. A call to an
    action tool the model names anyway is refused and kept as an intended
    action for the plan.
    """

    def __init__(self, full: ToolRegistry):
        super().__init__()
        self.full = full
        self.tools = {name: tool for name, tool in full.tools.items() if is_plan_research_tool(tool)}
        self.attempted: List[Dict[str, Any]] = []

    def execute_tool(self, tool_name: str, /, agent_context=None, on_output=None, **kwargs) -> ToolResult:
        if tool_name not in self.tools and self.full.get_tool(tool_name) is not None:
            shown = {k: v for k, v in kwargs.items() if not k.startswith("_")}
            self.attempted.append({"tool": tool_name, "params": json.dumps(shown, default=str)[:300],
                                   "args": shown})
            return ToolResult(
                success=False,
                error=(f"{tool_name} changes something, so it waits for an approved plan. "
                       "Nothing was run; put it in the plan as a step."),
            )
        return super().execute_tool(tool_name, agent_context=agent_context, on_output=on_output, **kwargs)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

def default_plan_mode() -> bool:
    """Settings → "Start new chats in plan mode" (off unless switched on)."""
    try:
        from backend.utils.settings_utils import get_setting
        return str(get_setting("plan_mode_default", default="false")).strip().lower() in ("true", "1", "yes", "on")
    except Exception:
        return False


def session_in_plan_mode(session_id: Optional[str]) -> bool:
    """Whether the stored session is in plan mode (False on any doubt about a
    session that does not exist; the gate reads the same row)."""
    if not session_id:
        return False
    try:
        from backend.models import LLMSession, db
        session = db.session.get(LLMSession, session_id)
        return bool(session is not None and session.plan_mode)
    except Exception:
        return False


def plan_mode_refusal(session_id: Optional[str], tool_name: Optional[str], registry=None) -> Optional[str]:
    """Why a route that runs a tool directly must not run it now, or None:
    in plan mode only research tools run before a plan is approved."""
    if not session_in_plan_mode(session_id):
        return None
    if tool_name and registry is not None and is_plan_research_tool(registry.get_tool(tool_name)):
        return None
    return ("This chat is in plan mode: describe what you want and approve the plan, "
            "or turn plan mode off to run it directly.")


def switch_on(session_id: Optional[str]) -> bool:
    """Put a chat in plan mode from the server side (and tell its page)."""
    if not session_id or not plan_mode_supported(session_id):
        return False
    try:
        from backend.models import LLMSession, db
        session = db.session.get(LLMSession, session_id)
        if session is None:
            session = LLMSession(id=session_id, user="default", plan_mode=True)
            db.session.add(session)
        else:
            session.plan_mode = True
        db.session.commit()
    except Exception:
        logger.warning("could not switch plan mode on for %s", session_id, exc_info=True)
        return False
    try:
        from backend.socketio_instance import socketio
        socketio.emit("chat:plan_mode", {"session_id": session_id, "plan_mode": True}, room=session_id)
    except Exception:
        pass
    return True


def plan_mode_supported(session_id: str) -> bool:
    """Discord chats have no way to show or answer a plan card."""
    return not str(session_id or "").startswith("discord_")


def active_chat_model() -> Optional[str]:
    """The chat model plan mode would plan with."""
    try:
        from flask import current_app
        model = getattr(getattr(current_app, "brain_state", None), "active_model", None)
        if model:
            return str(model)
    except Exception:
        pass
    try:
        from backend.utils.llm_service import get_saved_active_model_name
        return get_saved_active_model_name()
    except Exception:
        return None


def availability(model: Optional[str]) -> Tuple[bool, str]:
    """(available, reason) for plan mode on ``model``, from the measurements
    recorded next to the model (model_capability_data.PLAN_MODE_MEASURED)."""
    try:
        from backend.services.model_capability_data import PLAN_MODE_MEASURED
    except Exception:
        PLAN_MODE_MEASURED = {}
    row = PLAN_MODE_MEASURED.get(model or "")
    if not row:
        return True, "not measured yet on this model"
    if row.get("wording_sha") != pmd.wording_sha():
        return True, "measured with older wording; not re-measured yet"
    score = float(row.get("lcb", 0.0))
    if score < pmd.PLAN_MODE_FLOOR:
        return False, (f"{model} writes usable plans {score:.0%} of the time on the plan-mode test "
                       f"(needs {pmd.PLAN_MODE_FLOOR:.0%}); choose a stronger chat model")
    return True, f"measured {score:.0%} on the plan-mode test"


def latest_open_plan(session_id: str):
    """The session's newest plan awaiting approval or paused, or None."""
    from backend.models import ChatPlan
    return (ChatPlan.query.filter(ChatPlan.session_id == session_id,
                                  ChatPlan.status.in_(("awaiting_approval", "paused")))
            .order_by(ChatPlan.created_at.desc()).first())


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def handle_turn(
    *,
    session_id: str,
    message: str,
    options: Dict[str, Any],
    emit_fn: Callable,
    app,
    project_id=None,
    image_data=None,
    image_url=None,
    is_voice_message: bool = False,
    agent_brain=None,
    engine=None,
) -> bool:
    """Run this turn in plan mode. False lets normal routing have it (a
    research slash command such as /gpu runs as usual)."""
    from backend.services.slash_command_executor import resolve_slash_direct_tool

    state = getattr(agent_brain, "state", None)
    full = getattr(state, "tool_registry", None) or getattr(engine, "registry", None)
    llm = getattr(state, "llm", None) or getattr(engine, "llm", None)
    if full is None or llm is None:
        emit_fn("chat:error", {"error": "Plan mode needs the chat model, and it is not loaded."})
        return True

    tool_name, params = resolve_slash_direct_tool(options)
    if tool_name:
        tool = full.get_tool(tool_name)
        if tool is None or is_plan_research_tool(tool):
            return False
        _plan_slash_command(session_id, message, options, tool_name, params, emit_fn, app,
                            project_id=project_id, model=getattr(llm, "model", None))
        return True

    PlanModeRunner(full, llm, state).run(
        session_id=session_id, message=message, options=options, emit_fn=emit_fn, app=app,
        project_id=project_id, image_data=image_data, image_url=image_url,
        is_voice_message=is_voice_message,
    )
    return True


def _plan_slash_command(session_id, message, options, tool_name, params, emit_fn, app, *,
                        project_id=None, model=None):
    """An action slash command (/imagine …) is its own one-step plan."""
    from backend.services.turn_router import DIRECT_TOOL_SIDE_EFFECT

    kind = {"generate_image": "image", "edit_image": "image", "remove_background": "image",
            "outpaint_image": "image", "inpaint_image": "image", "generate_video": "video",
            "generate_music_video": "video", "start_film_crew": "video", "generate_music": "audio",
            "generate_speech": "audio", "save_memory": "memory"}.get(tool_name, "other")
    direct = {k: options[k] for k in ("direct_tool", "direct_tool_params", "slash_command", "slash_args",
                                       "slash_prompt", "image_model") if k in options}
    step = {"title": message.strip()[:80] or f"Run {tool_name}", "action": message.strip() or tool_name,
            "kind": kind, "tools": [tool_name], "done_when": "", "direct": direct}
    warnings = []
    if DIRECT_TOOL_SIDE_EFFECT.get(tool_name) == "gpu_job":
        warnings.append("Uses the GPU; other GPU work waits while it runs.")
    with app.app_context():
        from backend.models import LLMSession, db
        if db.session.get(LLMSession, session_id) is None:
            db.session.add(LLMSession(id=session_id, user="default", project_id=project_id, plan_mode=True))
            db.session.commit()
        from backend.services.agent_brain import _persist_turn
        _persist_turn(app, session_id, "user", message, None, project_id=project_id)
        plan = save_plan(session_id, project_id=project_id, objective=message, goal=f"Run {message.strip()}",
                         steps=[step], checks=[], risks=[], questions=[], warnings=warnings,
                         model=model, compose_path="slash", research={}, options=_kept_options(options))
        text = render_reply(plan)
        payload = plan.to_dict()
    request_id = str(options.get("request_id") or uuid.uuid4())
    emit_fn("chat:complete", {"response": text, "request_id": request_id, "plan": payload, "steps": []})
    from backend.services.agent_brain import _persist_turn
    _persist_turn(app, session_id, "assistant", text, {"messageType": "plan", "planId": payload["id"], "plan": payload},
                  emit_fn=emit_fn, request_id=request_id, project_id=project_id)


def _kept_options(options: Dict[str, Any]) -> Dict[str, Any]:
    """The turn options a plan carries into its run."""
    keep = ("project_root", "use_rag", "image_model", "agent_mode", "attached_image_urls")
    return {k: options[k] for k in keep if k in options}


# ---------------------------------------------------------------------------
# Research and compose
# ---------------------------------------------------------------------------

@dataclass
class ComposeResult:
    kind: Optional[str]
    data: Dict[str, Any] = field(default_factory=dict)
    path: str = ""
    error: str = ""


class PlanModeRunner:
    def __init__(self, full: ToolRegistry, llm, state=None):
        self.full = full
        self.llm = llm
        self.state = state

    def run(self, *, session_id, message, options, emit_fn, app, project_id=None,
            image_data=None, image_url=None, is_voice_message=False):
        from backend.services.agent_brain import SOCIAL_TIER2_PATTERNS
        from backend.services.unified_chat_engine import UnifiedChatEngine, is_aborted

        research = PlanResearchRegistry(self.full)
        extra: Dict[str, Any] = {}
        social = bool(SOCIAL_TIER2_PATTERNS.fullmatch((message or "").strip()))

        def finalize(reply: str, steps: List[Dict[str, Any]]) -> Optional[str]:
            if social or is_aborted(session_id):
                extra["plan_kind"] = "answer"
                return None
            current = latest_open_plan(session_id)
            result = self.compose(
                objective=message, reply=reply, steps=steps, attempted=research.attempted,
                current=current, session_id=session_id,
            )
            misreported = (result.kind in ("answer", None) and not research.attempted
                           and not _QUESTION.search(message or "") and misreports(reply))
            if misreported:
                # The research reply says it cannot be done or that it is done;
                # neither is known while planning. One more try with that said.
                retry = self.compose(
                    objective=message, reply=reply, steps=steps, attempted=research.attempted,
                    current=current, session_id=session_id, correction=True,
                )
                if retry.kind is not None:
                    result = retry
            extra["plan_kind"] = result.kind or "none"
            if result.kind == "question" and result.data.get("question"):
                return str(result.data["question"]).strip()
            if result.kind == "plan" and _QUESTION.search(message or "") and not changes_something(
                    result.data, self.full):
                # Steps that only look things up and write text, for a
                # question: the research reply is the answer.
                result = ComposeResult("answer", result.data, result.path)
                extra["plan_kind"] = "answer"
            if result.kind != "plan" and research.attempted:
                # It tried to act while researching: what it tried is the plan.
                result = plan_from_attempts(research.attempted, objective=message)
            if result.kind != "plan":
                if result.kind is None and research.attempted:
                    # It meant to act and no plan could be written: say so
                    # rather than leave a reply that reads as if it acted.
                    return ((reply or "").strip() + "\n\n" if reply else "") + (
                        "I could not write that up as a plan. Tell me a little more about what you "
                        "want done and I'll try again; nothing has been changed.")
                if _CLAIMS_DONE.search(reply or ""):
                    # Nothing that changes anything ran while researching.
                    return (reply or "").strip() + "\n\n" + NOTHING_CHANGED
                return None
            plan = self.save(result, session_id=session_id, project_id=project_id, objective=message,
                             current=current, research=research, steps=steps, options=options,
                             image_url=image_url)
            payload = plan.to_dict()
            extra.update({"messageType": "plan", "planId": payload["id"], "plan": payload})
            # A reply that said it cannot be done, or is done, would
            # contradict the plan under it.
            findings = "" if misreported else (reply or "").strip()[:pmd.COMPOSE_REPLY_CHARS]
            extra["findings"] = findings
            return f"{findings}\n\n{render_reply(plan)}" if findings else render_reply(plan)

        opts = {k: v for k, v in (options or {}).items()
                if k not in ("direct_tool", "direct_tool_params", "slash_command", "slash_args")}
        opts.update({
            "skip_direct_intercepts": True,
            "skip_memory_capture": True,
            "plan_mode_research": True,
            "finalize_fn": finalize,
            "complete_extra": extra,
        })
        engine = UnifiedChatEngine(tool_registry=research, llm_instance=self.llm,
                                   max_iterations=pmd.PLAN_RESEARCH_MAX_ITERATIONS)
        engine.chat(
            session_id=session_id, message=message, options=opts, emit_fn=emit_fn, app=app,
            project_id=project_id, image_data=image_data, image_url=image_url,
            is_voice_message=is_voice_message, brain_state=self.state, skip_tools=social,
            prompt_role="chat",
        )

    # -- compose ------------------------------------------------------------

    def compose(self, *, objective, reply, steps, attempted, current, session_id,
                correction: bool = False) -> ComposeResult:
        model = getattr(self.llm, "model", None) or ""
        try:
            from backend.utils.ollama_resource_manager import refresh_context_window
            num_ctx = refresh_context_window(self.llm)
        except Exception:
            num_ctx = None
        available = sorted(self.full.list_tools())
        system = pmd.COMPOSE_SYSTEM.format(
            max_steps=pmd.PLAN_MAX_STEPS, kinds=pmd.kinds_text(available), tools=", ".join(available),
            missing=pmd.compose_unavailable(available, web=web_access_on()))
        user = compose_input(objective=objective, reply=reply, steps=steps, attempted=attempted,
                             current=current, history=_history_text(session_id))
        if correction:
            user += "\n\n" + pmd.COMPOSE_CORRECTION.format(abilities=pmd.abilities(available))
        return compose_plan(model, system, user, num_ctx=num_ctx, reply=reply,
                            schema=pmd.compose_schema(available))

    # -- save ---------------------------------------------------------------

    def save(self, result: ComposeResult, *, session_id, project_id, objective, current, research,
             steps, options, image_url):
        haystack = "\n".join([
            objective or "", _history_text(session_id),
            research_digest(steps),
            current.markdown if current is not None and current.markdown else "",
        ])
        web_used = any(
            (call.get("tool_name") in _WEB_TOOLS)
            for s in steps for call in (s.get("tool_calls") or []) if isinstance(call, dict)
        )
        fields, warnings = validate(result.data, registry=self.full, haystack=haystack, web_used=web_used,
                                    session_id=session_id, state=self.state,
                                    project_root=(options or {}).get("project_root"))
        if current is not None and current.status == "paused":
            # A replan after a failed step keeps the steps that already ran.
            finished = [s for s in (current.steps or []) if s.get("status") in ("done", "skipped")]
            fields["steps"] = finished + [s for s in fields["steps"] if s.get("status") not in ("done", "skipped")]
        kept = _kept_options(options or {})
        if image_url:
            kept["attachments"] = [image_url]
        return save_plan(
            session_id, project_id=project_id,
            objective=current.objective if current is not None else objective,
            goal=fields["goal"], steps=fields["steps"], checks=fields["checks"], risks=fields["risks"],
            questions=fields["questions"], warnings=warnings, model=getattr(self.llm, "model", None),
            compose_path=result.path, research={"attempted": research.attempted[:10], "web_used": web_used},
            options=kept, parent=current, attachments=[image_url] if image_url else [],
        )


_QUESTION = re.compile(r"^\s*(where|which|how|why|what|who|when|is|are|does|do|can|could)\b|\?\s*$", re.I)

# A research reply that says the work cannot be done ("I am unable to create
# pictures", "I am a text-based model"), with the action tools hidden from
# the model, or that it is done ("I have remembered that..."), when nothing
# that changes anything can run during research. Both came from gemma4:e2b
# in plan mode (logs/plan_mode_evals, 2026-10-26).
_CANNOT = re.compile(
    r"\bI\s+(?:can(?:not|'t|\u2019t)|am\s+(?:not\s+able|unable)|was\s+unable"
    r"|(?:do\s+not|don't|don\u2019t)\s+have\s+(?:a\s+|the\s+|any\s+)?(?:tool|tools|access|ability|capability|function))"
    r"|\bI\s+(?:can\s+)?only\s+(?:generate|process|write|produce|answer|provide)\b"
    r"|\btext-based\s+(?:model|assistant|AI)\b",
    re.I,
)
_CLAIMS_DONE = re.compile(
    r"\bI(?:'ve|\u2019ve|\s+have)\s+(?:now\s+|successfully\s+|just\s+|also\s+)?"
    r"(?:remembered|saved|stored|noted|created|made|generated|written|sent|deleted|removed|updated"
    r"|added|opened|launched|started|scheduled|copied|moved|renamed|installed|posted)\b",
    re.I,
)
NOTHING_CHANGED = ("Nothing has been changed yet: in plan mode I only look things up until you "
                   "approve a plan.")


def misreports(reply: Optional[str]) -> bool:
    """Whether a research reply says the work cannot be done or is done."""
    return bool(reply) and bool(_CANNOT.search(reply) or _CLAIMS_DONE.search(reply))


def web_access_on() -> bool:
    """Settings → web access (off by default); False when it cannot be read."""
    try:
        from backend.utils.settings_utils import web_access_block_reason
        return web_access_block_reason("search the web") is None
    except Exception:
        return False


def changes_something(data: Dict[str, Any], registry) -> bool:
    """Whether a composed plan has a step that does more than look things up
    and write text."""
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("kind") not in pmd.TEXT_KINDS:
            return True
        call = _tool_call(str(step.get("action") or ""))
        for name in _strings(step.get("tools"), 6) + ([call[0]] if call else []):
            tool = registry.get_tool(name) if registry is not None else None
            if tool is not None and not is_plan_research_tool(tool):
                return True
    return False


def plan_from_attempts(attempted: List[Dict[str, Any]], *, objective: str) -> "ComposeResult":
    """A plan made of the actions the model tried during research, each as
    the exact call it tried; validate() turns those calls into direct steps."""
    steps = []
    for a in attempted[:pmd.PLAN_MAX_STEPS]:
        args = ", ".join(f"{k}={v!r}" for k, v in (a.get("args") or {}).items())
        steps.append({"title": f"Run {a['tool']}", "action": f"{a['tool']}({args})",
                      "kind": pmd.tool_kind(a["tool"]) or "other", "tools": [a["tool"]]})
    return ComposeResult("plan", {"kind": "plan", "goal": objective[:200], "steps": steps}, "attempted")


def compose_input(*, objective, reply, steps, attempted, current, history) -> str:
    parts = [f"Request: {objective}"]
    if history:
        parts.append(f"Conversation before it (latest last):\n{history[-pmd.COMPOSE_HISTORY_CHARS:]}")
    if current is not None:
        done = [s for s in (current.steps or []) if s.get("status") in ("done", "skipped")]
        parts.append(f"The plan so far (revise it; the request is the person's feedback):\n{current.markdown or ''}")
        if done:
            parts.append("These steps already ran; write only the steps that remain:\n"
                         + "\n".join(f"- {s.get('title')}" for s in done))
    found = research_digest(steps)
    if found:
        parts.append(f"What the research found:\n{found}")
    if reply:
        parts.append(f"Your research reply:\n{reply[:pmd.COMPOSE_REPLY_CHARS]}")
    if attempted:
        parts.append("Actions you tried during research, which wait for approval:\n"
                     + "\n".join(f"- {a['tool']} {a['params']}" for a in attempted[:8]))
    return "\n\n".join(parts)


def research_digest(steps: List[Dict[str, Any]]) -> str:
    """Tool calls and their results from the research, newest last, cut to fit."""
    lines: List[str] = []
    for step in steps or []:
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = call.get("tool_name") or "?"
            outcome = "" if call.get("success", True) else "(failed) "
            lines.append(f"[{name}] {json.dumps(call.get('params') or {}, default=str)[:200]}\n"
                         f"{outcome}{str(call.get('output_preview') or '')[:1500]}")
    text = "\n\n".join(lines)
    return text[-pmd.COMPOSE_RESEARCH_CHARS:]


def _history_text(session_id: str) -> str:
    try:
        from backend.models import LLMMessage
        rows = (LLMMessage.query.filter_by(session_id=session_id)
                .order_by(LLMMessage.timestamp.desc()).limit(6).all())
        return "\n".join(f"{r.role}: {(r.content or '')[:300]}" for r in reversed(rows))
    except Exception:
        return ""


def compose_plan(model: str, system: str, user: str, *, num_ctx=None, reply: str = "",
                 schema: Optional[Dict[str, Any]] = None) -> ComposeResult:
    """Ask for the plan as JSON, falling back step by step; never raises."""
    schema = schema or pmd.COMPOSE_SCHEMA
    data, err = _chat_json(model, system, user, schema, num_ctx)
    path = "schema"
    # A call that failed or timed out would fail the same way again; only the
    # reply's own numbered list is left to try.
    if data is None and not err.startswith(_CALL_FAILED):
        data, err = _chat_json(model, system, user, "json", num_ctx)
        path = "json"
    problem = shape_problem(data) if data is not None else err
    if data is not None and problem:
        repair = (f"{user}\n\nYour last answer was not usable: {problem}. "
                  f"Answer again with JSON that fits the schema.")
        fixed, err2 = _chat_json(model, system, repair, schema, num_ctx)
        if fixed is not None and not shape_problem(fixed):
            return ComposeResult(fixed.get("kind"), fixed, "repair")
        problem = shape_problem(fixed) if fixed is not None else err2
    elif data is not None:
        return ComposeResult(data.get("kind"), data, path)
    listed = [m.strip() for m in _NUMBERED.findall(reply or "") if len(m.strip()) > 3]
    if len(listed) >= 2:
        steps = [{"title": s[:80], "action": s, "kind": "other"} for s in listed[:pmd.PLAN_MAX_STEPS]]
        return ComposeResult("plan", {"kind": "plan", "goal": "", "steps": steps}, "list")
    return ComposeResult(None, {}, "", problem or "no plan")


def shape_problem(data: Any) -> str:
    """Why ``data`` is not a usable compose result, or ""."""
    if not isinstance(data, dict):
        return "the answer is not a JSON object"
    kind = data.get("kind")
    if kind not in pmd.PLAN_KINDS:
        return f'kind must be one of {sorted(pmd.PLAN_KINDS)}, got {kind!r}'
    if kind == "question" and not str(data.get("question") or "").strip():
        return "a question needs the question text"
    if kind == "plan":
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            return "a plan needs at least one step"
        for i, step in enumerate(steps, start=1):
            action = str(step.get("action") or "").strip() if isinstance(step, dict) else ""
            if not action:
                return f"step {i} has no action"
            if _tool_call(action) is None and len(action.split()) < 3:
                return (f"step {i}'s action ({action!r}) must say in words what to do, naming what it "
                        "changes or makes, e.g. \"Change DEFAULT_STEPS from 8 to 9 in backend/config.py\"")
    return ""


def _fit_params(tool, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``params`` renamed to the tool's own parameter names where the spelling
    is close; None when one cannot be placed or a required one is missing."""
    import difflib
    declared = dict(getattr(tool, "parameters", None) or {})
    if not declared:
        return None
    fitted: Dict[str, Any] = {}
    for key, value in params.items():
        name = key if key in declared else next(iter(difflib.get_close_matches(key, declared, n=1, cutoff=0.75)), None)
        if name is None or name in fitted:
            return None
        fitted[name] = value
    if any(p.required and name not in fitted for name, p in declared.items()):
        return None
    return fitted


_CALL = re.compile(r"^\s*([A-Za-z_]\w*)\s*\((.*)\)\s*$", re.S)


def _tool_call(action: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """(tool, params) when an action is written as a call, e.g.
    generate_image(prompt='a red fox'); None otherwise. Keyword arguments
    with literal values only."""
    import ast
    m = _CALL.match(action or "")
    if not m:
        return None
    call = None
    # A model writes multi-line text (a poem, a file) with raw line breaks
    # inside the quotes; read those as \n.
    for args in (m.group(2), m.group(2).replace("\n", "\\n")):
        try:
            call = ast.parse(f"f({args})", mode="eval").body
            params = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
            break
        except (SyntaxError, ValueError):
            call = None
    if call is None:
        return None
    if call.args:
        return None
    return m.group(1), params


_CALL_FAILED = "the model call failed"


def _chat_json(model, system, user, fmt, num_ctx) -> Tuple[Optional[dict], str]:
    try:
        from backend.config import get_chat_keep_alive
        from backend.services.nl_control_plane import _client_for, parse_json_reply
        from backend.utils.ollama_resource_manager import request_options, think_payload

        response = _client_for(pmd.COMPOSE_TIMEOUT_S).chat(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            format=fmt,
            options=request_options(model, temperature=0.2, num_ctx=num_ctx,
                                    num_predict=pmd.COMPOSE_MAX_TOKENS),
            keep_alive=get_chat_keep_alive(),
            **think_payload(model),
        )
    except Exception as e:  # noqa: BLE001 - a failed call ends the compose
        logger.warning("plan compose call failed (%s): %s", fmt if isinstance(fmt, str) else "schema", e)
        return None, f"{_CALL_FAILED}: {e}"
    msg = getattr(response, "message", None) or (response.get("message") if isinstance(response, dict) else None)
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    ok, value = parse_json_reply((content or "").strip())
    if not ok or not isinstance(value, dict):
        return None, "the answer was not JSON"
    return value, ""


# ---------------------------------------------------------------------------
# Validation, storage, rendering
# ---------------------------------------------------------------------------

def _strings(value, limit: int = 8) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip()[:400] for v in value if str(v).strip()][:limit]


def validate(data: Dict[str, Any], *, registry, haystack: str, web_used: bool = False,
             session_id: Optional[str] = None, state=None,
             project_root: Optional[str] = None) -> Tuple[Dict[str, Any], List[str]]:
    """The plan's fields cleaned up, and the warnings a person should read.

    Checked by code, not by the model: step count, known kinds and tools,
    names the research never saw, actions that may come from web text, and
    whether each step would run the way its kind says.
    """
    warnings: List[str] = []
    raw_steps = [s for s in (data.get("steps") or []) if isinstance(s, dict)]
    if len(raw_steps) > pmd.PLAN_MAX_STEPS:
        warnings.append(f"The plan had {len(raw_steps)} steps; only the first {pmd.PLAN_MAX_STEPS} are kept.")
        raw_steps = raw_steps[:pmd.PLAN_MAX_STEPS]
    known = set(registry.list_tools()) if registry is not None else set()
    offered = pmd.offered_kinds(known if registry is not None else None)
    lowered = (haystack or "").lower()
    roots = _code_roots(project_root)
    steps = []
    for i, raw in enumerate(raw_steps, start=1):
        action = str(raw.get("action") or "").strip()[:1200]
        call = _tool_call(action)
        named = [t for t in _strings(raw.get("tools"), 6) if t in known]
        if call and call[0] in known:
            named = [call[0]]
        elif not named:
            # "Use the agent_task_execute tool to ..." with the tools left
            # empty. Only tools a step kind lists count, so "without using
            # system_command" grants nothing.
            named = list(dict.fromkeys(w for w in re.findall(r"\b[a-z][a-z0-9_]+\b", action)
                                       if w in known and pmd.tool_kind(w)))[:6]
        kind = raw.get("kind") if raw.get("kind") in offered else "other"
        # The kind decides how the step runs (a screen step gets the agent's
        # screen), so an "other" step whose tools all belong to one kind is
        # filed there. A step given a kind keeps it, and _fits_kind drops
        # tools that kind may not carry: a writing step that names a screen
        # tool must not take it into the grants.
        owners = {pmd.tool_kind(t) for t in named}
        if kind == "other" and len(owners) == 1 and None not in owners:
            kind = owners.pop()
        tools = [t for t in named if _fits_kind(t, kind, registry)]
        step = {
            "index": i,
            "title": (str(raw.get("title") or "").strip() or action[:60])[:120],
            "action": action,
            "kind": kind,
            "tools": tools,
            "done_when": str(raw.get("done_when") or "").strip()[:300],
            "status": "pending",
        }
        if raw.get("status") in ("done", "skipped"):
            step.update({k: raw[k] for k in ("status", "summary") if k in raw})
        if call and call[0] in known:
            step["tools"] = [call[0]]
            params = _fit_params(registry.get_tool(call[0]), call[1])
            if params is not None:
                # Written as a call to a real tool with arguments it takes:
                # run exactly that call.
                step["direct"] = {"direct_tool": call[0], "direct_tool_params": params}
        for target in sorted(set(_PATH_LIKE.findall(action))):
            target = target.rstrip(".,;:")
            if _invented(target, lowered, roots):
                warnings.append(f"Step {i} names {target}, which the research did not find.")
        if web_used and set(tools) & _SENSITIVE_TOOLS:
            warnings.append(f"Step {i} acts after reading web pages; check it asks for what you asked for.")
        route = preview_route(action, kind, session_id=session_id, state=state)
        if route:
            step["route"] = route
            expected = pmd.STEP_KINDS[kind]["routes"]
            if expected is not None and route not in expected:
                warnings.append(f"Step {i} would run as {route.replace('_', ' ')}, not as {kind.replace('_', ' ')}.")
        steps.append(step)
    fields = {
        "goal": str(data.get("goal") or "").strip()[:400],
        "steps": steps,
        "checks": _strings(data.get("checks")),
        "risks": _strings(data.get("risks")),
        "questions": _strings(data.get("questions")),
    }
    return fields, warnings


def _code_roots(project_root: Optional[str]) -> List[str]:
    roots = [project_root] if project_root else []
    try:
        from backend.tools.llama_code_tools import PROJECT_ROOT
        roots.append(str(PROJECT_ROOT))
    except Exception:
        pass
    return roots


def _invented(target: str, lowered_haystack: str, roots: List[str]) -> bool:
    """A URL or a path with a folder that appears nowhere in the request, the
    conversation or the research, and is not on disk. A bare file name is
    usually one the plan will create, so it is never flagged."""
    import os
    if target.lower() in lowered_haystack:
        return False
    if target.startswith(("http://", "https://")):
        bare = target.split("://", 1)[1].rstrip("/").lower()
        return bare not in lowered_haystack and bare.removeprefix("www.") not in lowered_haystack
    if "/" not in target:
        return False
    rel = target.lstrip("./")
    return not any(os.path.exists(os.path.join(root, rel)) for root in roots)


def _fits_kind(tool_name: str, kind: str, registry) -> bool:
    """Whether a step of ``kind`` may name ``tool_name``. The tools a plan
    names are what "allow these actions" grants, so a writing step cannot
    carry a screen-control tool into the run."""
    if kind in pmd.TEXT_KINDS:
        return is_plan_research_tool(registry.get_tool(tool_name)) if registry is not None else False
    if kind != "desktop" and pmd.tool_kind(tool_name) == "desktop":
        return False
    return True


def preview_route(action: str, kind: str, *, session_id=None, state=None) -> Optional[str]:
    """The route a step's action would take, from the same code the brain runs."""
    try:
        from backend.services.turn_router import compute_facts, legacy_route
        opts = {**pmd.STEP_KINDS.get(kind, {}).get("options", {}), "plan_step": {"preview": True}}
        facts = compute_facts(action, opts, session_id=session_id, state=state)
        route, _args = legacy_route(action, opts, facts)
        return route.value
    except Exception:
        logger.debug("route preview skipped for a plan step", exc_info=True)
        return None


def save_plan(session_id, *, project_id, objective, goal, steps, checks, risks, questions, warnings,
              model, compose_path, research, options, parent=None, attachments=None):
    """Store a plan awaiting approval; any older open plan in the session is superseded."""
    from backend.models import ChatPlan, LLMSession, db

    if db.session.get(LLMSession, session_id) is None:
        db.session.add(LLMSession(id=session_id, user="default", project_id=project_id, plan_mode=True))
        db.session.flush()
    ChatPlan.query.filter(ChatPlan.session_id == session_id,
                          ChatPlan.status.in_(("awaiting_approval", "paused"))).update(
        {"status": "superseded"}, synchronize_session=False)
    plan = ChatPlan(
        id=uuid.uuid4().hex,
        session_id=session_id,
        project_id=project_id,
        parent_id=parent.id if parent is not None else None,
        revision=(parent.revision + 1) if parent is not None else 1,
        status="awaiting_approval",
        objective=objective or "",
        goal=goal or "",
        steps=[{**s, "index": i} for i, s in enumerate(steps, start=1)],
        checks=checks, risks=risks, questions=questions, warnings=warnings,
        attachments=list(attachments or []),
        options=options or {},
        model=model, wording_sha=pmd.wording_sha(), compose_path=compose_path,
        research=research or {},
    )
    plan.markdown = render_markdown(plan)
    db.session.add(plan)
    db.session.commit()
    return plan


def render_markdown(plan) -> str:
    lines = []
    if plan.goal:
        lines.append(f"**Goal:** {plan.goal}")
    lines.append("")
    for s in plan.steps or []:
        tools = f" ({', '.join(s['tools'])})" if s.get("tools") else ""
        mark = " — done" if s.get("status") == "done" else (" — skipped" if s.get("status") == "skipped" else "")
        lines.append(f"{s['index']}. **{s['title']}**{mark}: {s['action']}{tools}")
    for title, items in (("Checks", plan.checks), ("Risks", plan.risks),
                         ("Questions", plan.questions), ("Warnings", plan.warnings)):
        if items:
            lines.append(f"\n**{title}**")
            lines.extend(f"- {item}" for item in items)
    return "\n".join(lines).strip()


def render_reply(plan) -> str:
    """The plan as the reply text: what history and plain clients show."""
    lead = "Here is the plan. Nothing has run yet: approve it to start, or tell me what to change."
    return f"{lead}\n\n{plan.markdown or render_markdown(plan)}"
