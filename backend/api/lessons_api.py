#!/usr/bin/env python3
"""
Lessons API — explicit Begin/End bracket around a sequence of positive feedback
("pearls") so the agent learns in coherent chunks instead of drowning in
self-reflective notes. Blueprint auto-discovered by blueprint_discovery.py.

Flow:
    POST /api/lessons/start           → mint lesson_id, track in ACTIVE_LESSONS
    (thumbs-up during active lesson)  → ToolFeedback rows carry lesson_id
    POST /api/lessons/<id>/end        → distill all pearls into one AgentMemory
                                        with source="lesson_summary" and
                                        content = JSON {title, steps:[...]}
"""

import json
import logging
import threading
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

lessons_bp = Blueprint("lessons", __name__, url_prefix="/api/lessons")

# Module-level registry of live lessons keyed by session_id.
# Trade-off: dies on backend restart — pearls in DB survive but End Lesson from
# a stale UI returns 404. Acceptable for single-user local tool; UI surfaces
# a "lesson not found" toast on mismatch.
ACTIVE_LESSONS: dict = {}
_REGISTRY_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_active_lesson_id(session_id: str) -> str | None:
    """Read-through helper used by the feedback endpoint to auto-attach
    a lesson_id when the frontend forgets to send one (belt-and-suspenders)."""
    if not session_id:
        return None
    with _REGISTRY_LOCK:
        rec = ACTIVE_LESSONS.get(session_id)
        return rec["id"] if rec else None


def _distill_lesson_pearls(app, lesson_id: str, session_id: str) -> dict | None:
    """Run one structured distillation over all positive pearls captured
    during a lesson. Returns the parsed {title, steps, memory_id} payload
    or None on failure.

    Unlike the per-👍 distiller (_distill_pearl_memory), this one asks the
    LLM for strict JSON so the frontend can render steps as editable rows
    and so the context formatter can flatten them into readable bullets.
    """
    if not app or not lesson_id:
        return None
    with app.app_context():
        try:
            from backend.models import db, LLMMessage, ToolFeedback, AgentMemory
            from backend.config import OLLAMA_BASE_URL
            import re
            import requests

            pearls = (
                ToolFeedback.query
                .filter_by(lesson_id=lesson_id, positive=True)
                .order_by(ToolFeedback.created_at.asc())
                .all()
            )
            pearl_tasks = [(p.task or "").strip() for p in pearls if p.task]
            if not pearl_tasks:
                logger.info(f"[LESSON-DISTILL] lesson {lesson_id[:12]}: no pearls to distill")
                return None

            pearl_lines = [f"{i+1}. {t[:200]}" for i, t in enumerate(pearl_tasks)]

            # Recent conversation context for the lesson's session
            convo_lines: list[str] = []
            if session_id:
                messages = (
                    LLMMessage.query
                    .filter_by(session_id=session_id)
                    .order_by(LLMMessage.timestamp.desc())
                    .limit(20)
                    .all()
                )
                messages.reverse()
                for m in messages[-12:]:
                    role = (m.role or "?")[:1].upper()
                    content = (m.content or "")[:220]
                    convo_lines.append(f"{role}: {content}")

            try:
                from backend.utils.llm_service import get_saved_active_model_name
                active_model = get_saved_active_model_name() or "gemma4:e4b"
            except Exception:
                active_model = "gemma4:e4b"

            # Strict-JSON prompt. Model frequently obliges; regex fallback
            # below handles the cases where it wraps the JSON in prose.
            #
            # The parameters block is what makes lessons GENERALIZABLE —
            # specific values (channel names, search terms, URLs, client names)
            # get replaced with {snake_case} placeholders so the same lesson
            # applies to any future target. The parameter list explains each
            # placeholder so Gemma4 can map a new user request onto the slots.
            prompt = (
                "You are Guaardvark's lesson distiller. The user has just finished "
                "teaching the agent a sequence of actions via thumbs-up pearls. "
                "Produce a short REPLAYABLE GENERIC guide that can be reused with "
                "different targets — not tied to the specific names in these pearls.\n\n"
                "Return STRICT JSON only — no prose, no markdown fences, no commentary:\n"
                '{"title": "<3-7 word generic task name>", '
                '"steps": [{"order": 1, "text": "<imperative single action, use {placeholder} for variables>"}, ...], '
                '"parameters": [{"name": "placeholder_name", "description": "what this slot represents", "example": "concrete value from this session"}, ...]}\n\n'
                "Rules:\n"
                "- One step per logical action (a click, a keystroke, a confirmation).\n"
                "- Order matches the pearls provided.\n"
                "- Keep each step under 120 characters.\n"
                "- REPLACE specific names/strings (channel names, search queries, client names, "
                "  URLs, video titles, usernames) with {snake_case_placeholder} tokens. "
                "  UI element names (Search, Subscribe, Back, Comment) stay literal.\n"
                "- Title must be generic too — 'Subscribe to {channel}' not 'Subscribe to Albenze Inc'.\n"
                "- Every placeholder used in steps MUST appear in parameters with a description "
                "  and the concrete example from this session.\n"
                "- If a lesson has no variable parts, return \"parameters\": [].\n"
                "- Do NOT editorialize or add steps that weren't in the pearls.\n\n"
                "=== Positive pearls (in order) ===\n"
                + "\n".join(pearl_lines)
                + "\n\n=== Recent conversation ===\n"
                + ("\n".join(convo_lines) if convo_lines else "(no prior turns loaded)")
                + "\n\nJSON:"
            )

            raw = ""
            try:
                resp = requests.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": active_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 1024, "temperature": 0.3},
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                raw = (resp.json().get("response") or "").strip()
            except Exception as llm_err:
                logger.warning(f"[LESSON-DISTILL] LLM call failed: {llm_err}")

            # Parse: try direct JSON, then regex-extract the first {...} block.
            parsed: dict | None = None
            if raw:
                candidates: list[str] = [raw]
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    candidates.append(match.group(0))
                for candidate in candidates:
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict) and "steps" in parsed:
                            break
                        parsed = None
                    except Exception:
                        parsed = None

            # Fallback: synthesize steps from raw pearl tasks. Always produces
            # a usable summary even if the model refuses to emit JSON. No
            # placeholders in the fallback path — user can add them in the
            # post-distill edit modal.
            if not parsed or "steps" not in parsed:
                logger.info(f"[LESSON-DISTILL] fallback to raw-pearl synthesis for {lesson_id[:12]}")
                parsed = {
                    "title": (pearl_tasks[0][:60] or "Lesson") if pearl_tasks else "Lesson",
                    "steps": [
                        {"order": i + 1, "text": (t[:200] or f"Step {i+1}")}
                        for i, t in enumerate(pearl_tasks)
                    ],
                    "parameters": [],
                }

            # Normalize + clamp
            title = str(parsed.get("title", "Lesson")).strip()[:120] or "Lesson"
            steps_in = parsed.get("steps") or []
            steps: list[dict] = []
            for i, s in enumerate(steps_in):
                if isinstance(s, dict):
                    text = str(s.get("text") or s.get("step") or "").strip()[:300]
                    order = int(s.get("order", i + 1))
                elif isinstance(s, str):
                    text = s.strip()[:300]
                    order = i + 1
                else:
                    continue
                if text:
                    steps.append({"order": order, "text": text})

            if not steps:
                logger.warning(f"[LESSON-DISTILL] empty steps after parse for {lesson_id[:12]}")
                return None

            # Normalize parameters. Missing/malformed → empty list; each param
            # keeps name/description/example with sensible defaults. Model may
            # omit example for lessons without concrete values, which is fine.
            params_in = parsed.get("parameters") or []
            parameters: list[dict] = []
            seen_names = set()
            for p in params_in:
                if not isinstance(p, dict):
                    continue
                name = str(p.get("name") or "").strip().lower()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                parameters.append({
                    "name": name[:40],
                    "description": str(p.get("description") or "").strip()[:200],
                    "example": str(p.get("example") or "").strip()[:160],
                })

            summary = {"title": title, "steps": steps, "parameters": parameters}

            mem = AgentMemory(
                id=uuid.uuid4().hex[:12],
                content=json.dumps(summary),
                source="lesson_summary",
                session_id=lesson_id,  # reused as lesson FK — avoids new column
                type="lesson",
                importance=0.85,
                tags=json.dumps(["lesson", title.lower().replace(" ", "-")[:40]]),
            )
            db.session.add(mem)
            db.session.commit()

            logger.info(
                f"[LESSON-DISTILL] saved memory {mem.id} for lesson {lesson_id[:12]}: "
                f"{title} ({len(steps)} steps, {len(parameters)} params)"
            )
            return {
                "memory_id": mem.id,
                "title": title,
                "steps": steps,
                "parameters": parameters,
            }

        except Exception as e:
            logger.error(f"[LESSON-DISTILL] lesson {lesson_id[:12]} failed: {e}", exc_info=True)
            try:
                from backend.models import db
                db.session.rollback()
            except Exception:
                pass
            return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@lessons_bp.route("/start", methods=["POST"])
def start_lesson():
    """Begin a lesson bracket for the given chat session.
    Body: {"session_id": str, "title"?: str}
    """
    try:
        data = request.get_json() or {}
        session_id = (data.get("session_id") or "").strip()
        if not session_id:
            return jsonify({"success": False, "error": "session_id is required"}), 400

        with _REGISTRY_LOCK:
            existing = ACTIVE_LESSONS.get(session_id)
            if existing:
                return jsonify({
                    "success": False,
                    "error": "A lesson is already active for this session",
                    "lesson_id": existing["id"],
                }), 409
            lesson_id = uuid.uuid4().hex
            ACTIVE_LESSONS[session_id] = {
                "id": lesson_id,
                "session_id": session_id,
                "started_at": datetime.now().isoformat(),
                "title": (data.get("title") or "").strip() or None,
            }

        try:
            from backend.socketio_events import emit_lesson_event
            emit_lesson_event("started", {
                "lesson_id": lesson_id,
                "session_id": session_id,
            })
        except Exception as emit_err:
            logger.warning(f"[LESSON] emit started failed (non-fatal): {emit_err}")

        logger.info(f"[LESSON] started {lesson_id[:12]} for session {session_id[:12]}")
        return jsonify({"success": True, "lesson_id": lesson_id})

    except Exception as e:
        logger.error(f"[LESSON] start failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@lessons_bp.route("/<lesson_id>/end", methods=["POST"])
def end_lesson(lesson_id: str):
    """Close a lesson bracket, run distillation, return the summary + memory id."""
    try:
        # Find + remove the active record. Tolerate the UI calling end after a
        # backend restart — still attempt distillation from DB pearls.
        removed_session = None
        with _REGISTRY_LOCK:
            for sid, rec in list(ACTIVE_LESSONS.items()):
                if rec.get("id") == lesson_id:
                    removed_session = sid
                    ACTIVE_LESSONS.pop(sid, None)
                    break

        # session_id needed for conversation context in the distill prompt;
        # if the record is gone (restart), look up any pearl to recover it.
        session_id = removed_session
        if not session_id:
            try:
                from backend.models import ToolFeedback
                first = (
                    ToolFeedback.query
                    .filter_by(lesson_id=lesson_id)
                    .first()
                )
                if first:
                    session_id = first.session_id
                else:
                    return jsonify({
                        "success": False,
                        "error": "lesson not found — no pearls recorded",
                    }), 404
            except Exception as lookup_err:
                logger.warning(f"[LESSON] pearl lookup failed: {lookup_err}")
                return jsonify({"success": False, "error": "lesson not found"}), 404

        from flask import current_app
        app = current_app._get_current_object()
        summary = _distill_lesson_pearls(app, lesson_id, session_id)

        try:
            from backend.socketio_events import emit_lesson_event
            emit_lesson_event("ended", {
                "lesson_id": lesson_id,
                "session_id": session_id,
                "memory_id": summary["memory_id"] if summary else None,
            })
        except Exception as emit_err:
            logger.warning(f"[LESSON] emit ended failed (non-fatal): {emit_err}")

        if not summary:
            return jsonify({
                "success": False,
                "error": "distillation produced no summary — no positive pearls?",
            }), 422

        return jsonify({
            "success": True,
            "memory_id": summary["memory_id"],
            "summary": {
                "title": summary["title"],
                "steps": summary["steps"],
                "parameters": summary.get("parameters", []),
            },
        })

    except Exception as e:
        logger.error(f"[LESSON] end {lesson_id[:12]} failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@lessons_bp.route("/<lesson_id>", methods=["GET"])
def get_lesson(lesson_id: str):
    """Return all pearls + active flag for a lesson (used by floater hydration)."""
    try:
        from backend.models import ToolFeedback

        pearls = (
            ToolFeedback.query
            .filter_by(lesson_id=lesson_id)
            .order_by(ToolFeedback.created_at.asc())
            .all()
        )

        with _REGISTRY_LOCK:
            active = any(rec.get("id") == lesson_id for rec in ACTIVE_LESSONS.values())

        return jsonify({
            "success": True,
            "lesson_id": lesson_id,
            "active": active,
            "pearls": [p.to_dict() for p in pearls],
        })
    except Exception as e:
        logger.error(f"[LESSON] get {lesson_id[:12]} failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@lessons_bp.route("/active", methods=["GET"])
def get_active_for_session():
    """Look up the active lesson for a session (frontend recovery after refresh)."""
    session_id = (request.args.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"success": False, "error": "session_id is required"}), 400

    with _REGISTRY_LOCK:
        rec = ACTIVE_LESSONS.get(session_id)

    if not rec:
        return jsonify({"success": True, "active": False, "lesson_id": None})
    return jsonify({"success": True, "active": True, **rec})
