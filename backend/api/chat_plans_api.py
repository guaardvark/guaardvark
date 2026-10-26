"""Plans written in plan mode: read, approve, edit, reject, continue.

Approving is a status change done atomically (one tab wins), after which the
plan runs on the session's chat-turn thread exactly as a message would, so
Stop and a new message reach it the usual way.
"""

import logging
import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

chat_plans_bp = Blueprint("chat_plans", __name__, url_prefix="/api/chat/plans")

APPROVAL_MODES = ("ask", "allow_plan_tools")


def _plan_or_404(plan_id):
    from backend.models import ChatPlan, db
    plan = db.session.get(ChatPlan, plan_id)
    if plan is None:
        return None, (jsonify({"success": False, "error": "No such plan."}), 404)
    return plan, None


@chat_plans_bp.route("/<plan_id>", methods=["GET"])
def get_plan(plan_id):
    plan, err = _plan_or_404(plan_id)
    if err:
        return err
    return jsonify({"success": True, "plan": plan.to_dict()})


def _start_run(plan_id: str, session_id: str):
    """Run the plan on the session's turn thread; None when the slot is held."""
    from backend.api.unified_chat_api import start_turn_thread
    from backend.services.chat_turn import build_router, make_session_emit, run_chat_turn
    from backend.services.plan_executor import execute_plan
    from backend.services.unified_chat_engine import begin_new_turn

    app = current_app._get_current_object()
    agent_brain, _engine, router_error = build_router(app)
    if agent_brain is None:
        return None, router_error or "Running a plan needs AgentBrain, which is switched off."
    turn_mark = begin_new_turn(session_id)
    emit_fn = make_session_emit(session_id, turn_mark, log_tag="PLAN-RUN")
    request_id = str(uuid.uuid4())

    def run():
        run_chat_turn(
            session_id=session_id, turn_mark=turn_mark, emit_fn=emit_fn, is_voice_message=False,
            log_tag="PLAN-RUN",
            body=lambda: execute_plan(plan_id, session_id=session_id, emit_fn=emit_fn, app=app,
                                      agent_brain=agent_brain, request_id=request_id),
        )

    thread = start_turn_thread(session_id, run, f"plan-run-{plan_id[:8]}")
    if thread is None:
        return None, "Something else is still running in this chat; stop it first."
    return request_id, None


def _move(plan_id: str, frm: tuple, to: dict) -> bool:
    """Atomic status change; False when the plan was not in a ``frm`` status."""
    from backend.models import ChatPlan, db
    n = ChatPlan.query.filter(ChatPlan.id == plan_id, ChatPlan.status.in_(frm)).update(
        to, synchronize_session=False)
    db.session.commit()
    return n == 1


@chat_plans_bp.route("/<plan_id>/approve", methods=["POST"])
def approve_plan(plan_id):
    """Body: {mode: "ask" | "allow_plan_tools", revision?}. Turns plan mode off
    for the chat, as Claude Code does, and starts the run."""
    from backend.models import LLMSession, db

    plan, err = _plan_or_404(plan_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    mode = body.get("mode") or "ask"
    if mode not in APPROVAL_MODES:
        return jsonify({"success": False, "error": f"mode must be one of {APPROVAL_MODES}"}), 400
    if body.get("revision") is not None and int(body["revision"]) != plan.revision:
        return jsonify({"success": False, "error": "This plan has a newer revision."}), 409
    session_id = plan.session_id
    granted = sorted({t for s in plan.steps for t in (s.get("tools") or [])}) if mode == "allow_plan_tools" else []
    if not _move(plan_id, ("awaiting_approval",), {
            "status": "executing", "approval_mode": mode, "granted_tools": granted,
            "approved_at": datetime.now()}):
        return jsonify({"success": False, "error": "This plan is no longer waiting for approval."}), 409
    session = db.session.get(LLMSession, session_id)
    if session is not None:
        session.plan_mode = False
        db.session.commit()
    request_id, problem = _start_run(plan_id, session_id)
    if problem:
        _move(plan_id, ("executing",), {"status": "awaiting_approval", "approved_at": None})
        if session is not None:
            session.plan_mode = True
            db.session.commit()
        return jsonify({"success": False, "error": problem}), 409
    _emit_mode(session_id, False)
    return jsonify({"success": True, "plan_id": plan_id, "request_id": request_id, "plan_mode": False})


@chat_plans_bp.route("/<plan_id>/continue", methods=["POST"])
def continue_plan(plan_id):
    """Body: {action: "retry" | "skip"} for a paused plan."""
    plan, err = _plan_or_404(plan_id)
    if err:
        return err
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in ("retry", "skip"):
        return jsonify({"success": False, "error": "action must be retry or skip"}), 400
    from backend.models import db
    steps = [dict(s) for s in plan.steps]
    stuck = next((s for s in steps if s.get("status") not in ("done", "skipped")), None)
    if plan.status != "paused" or stuck is None:
        return jsonify({"success": False, "error": "This plan is not paused at a step."}), 409
    stuck["status"] = "skipped" if action == "skip" else "pending"
    stuck.pop("error", None)
    plan.steps = steps
    db.session.commit()
    if not _move(plan_id, ("paused",), {"status": "executing", "paused_reason": None}):
        return jsonify({"success": False, "error": "This plan is not paused."}), 409
    request_id, problem = _start_run(plan_id, plan.session_id)
    if problem:
        _move(plan_id, ("executing",), {"status": "paused", "paused_reason": "stopped"})
        return jsonify({"success": False, "error": problem}), 409
    return jsonify({"success": True, "plan_id": plan_id, "request_id": request_id})


@chat_plans_bp.route("/<plan_id>/replan", methods=["POST"])
def replan(plan_id):
    """Back to plan mode for a paused plan: the next message revises the
    remaining steps, keeping the ones that ran."""
    from backend.models import LLMSession, db
    plan, err = _plan_or_404(plan_id)
    if err:
        return err
    if plan.status != "paused":
        return jsonify({"success": False, "error": "Only a paused plan can be planned again."}), 409
    session = db.session.get(LLMSession, plan.session_id)
    if session is not None:
        session.plan_mode = True
        db.session.commit()
    _emit_mode(plan.session_id, True)
    return jsonify({"success": True, "plan_mode": True})


@chat_plans_bp.route("/<plan_id>/reject", methods=["POST"])
def reject_plan(plan_id):
    plan, err = _plan_or_404(plan_id)
    if err:
        return err
    if not _move(plan_id, ("awaiting_approval", "paused"), {"status": "rejected"}):
        return jsonify({"success": False, "error": "This plan cannot be rejected now."}), 409
    return jsonify({"success": True})


@chat_plans_bp.route("/<plan_id>/edit", methods=["POST"])
def edit_plan(plan_id):
    """Body: {revision, goal?, steps: [{title, action, kind, tools?}]}. Saves
    the person's edit as the next revision, still waiting for approval."""
    from backend.services.plan_mode import render_reply, save_plan, validate

    plan, err = _plan_or_404(plan_id)
    if err:
        return err
    if plan.status != "awaiting_approval":
        return jsonify({"success": False, "error": "Only a plan waiting for approval can be edited."}), 409
    body = request.get_json(silent=True) or {}
    if body.get("revision") is not None and int(body["revision"]) != plan.revision:
        return jsonify({"success": False, "error": "This plan has a newer revision."}), 409
    if not isinstance(body.get("steps"), list) or not body["steps"]:
        return jsonify({"success": False, "error": "steps must be a non-empty list"}), 400
    registry = getattr(getattr(current_app, "brain_state", None), "tool_registry", None)
    # A person's own words are the source here; nothing they type is flagged
    # as invented.
    haystack = "\n".join(str(s.get("action", "")) for s in body["steps"] if isinstance(s, dict))
    fields, warnings = validate({"goal": body.get("goal", plan.goal), "steps": body["steps"],
                                 "checks": plan.checks, "risks": plan.risks, "questions": plan.questions},
                                registry=registry, haystack=haystack, session_id=plan.session_id,
                                state=getattr(current_app, "brain_state", None))
    new = save_plan(plan.session_id, project_id=plan.project_id, objective=plan.objective,
                    goal=fields["goal"], steps=fields["steps"], checks=fields["checks"], risks=fields["risks"],
                    questions=fields["questions"], warnings=warnings, model=plan.model,
                    compose_path="user_edit", research=plan.research, options=plan.options,
                    parent=plan, attachments=plan.attachments)
    from backend.services.agent_brain import _persist_turn
    payload = new.to_dict()
    _persist_turn(current_app._get_current_object(), plan.session_id, "assistant", render_reply(new),
                  {"messageType": "plan", "planId": new.id, "plan": payload}, project_id=plan.project_id)
    return jsonify({"success": True, "plan": payload})


def _emit_mode(session_id: str, on: bool) -> None:
    try:
        from backend.socketio_instance import socketio
        socketio.emit("chat:plan_mode", {"session_id": session_id, "plan_mode": on}, room=session_id)
    except Exception:
        logger.debug("chat:plan_mode not emitted", exc_info=True)
