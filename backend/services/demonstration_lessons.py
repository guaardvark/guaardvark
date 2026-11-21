#!/usr/bin/env python3
"""
Demonstration lessons: a guided replay a person signed off, step by step,
becomes one Lesson.

Each signed-off step is stored as a pearl (a ToolFeedback row carrying the
attempt's lesson id), the same rows Begin/End Lesson collects in chat. When
every step of an attempt is signed off, the pearls go through the lesson
distiller and are saved as one lesson memory, which the screen agent and chat
both recall.
"""

import hashlib
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PEARL_TOOL_NAME = "demonstration"

# The recorder asks the vision model about "the UI element at pixel
# coordinates (x, y)" and the reply usually repeats them. A position is only
# true for the layout it was recorded on, so it stays out of anything used to
# find the element again or to teach the step.
_COORD_LEAD_RE = re.compile(
    r"^\s*(?:the\s+)?(?:ui\s+)?element\s+(?:located\s+)?at\s+(?:pixel\s+)?(?:coordinates?\s+)?"
    r"\(?\s*\d+\s*,\s*\d+\s*\)?\s*(?:is|appears\s+to\s+be|shows)\s+",
    re.IGNORECASE,
)
# "The element is a folder icon..." reads badly after "Click".
_ELEMENT_LEAD_RE = re.compile(
    r"^\s*(?:the|this)\s+(?:marked\s+|ui\s+)?element\s+is\s+", re.IGNORECASE
)
_COORD_ANY_RE = re.compile(
    r"\s*(?:(?:located\s+)?at|near)?\s*(?:pixel\s+)?(?:coordinates?\s+)?\(\s*\d+\s*,\s*\d+\s*\)",
    re.IGNORECASE,
)


def clean_target_description(text: Optional[str]) -> str:
    """A recorded target description with its pixel coordinates removed."""
    if not isinstance(text, str):
        return ""
    cleaned = _COORD_LEAD_RE.sub("", text.strip())
    cleaned = _COORD_ANY_RE.sub("", cleaned)
    cleaned = _ELEMENT_LEAD_RE.sub("", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def scroll_amount(step: Dict[str, Any], default: int = -3) -> int:
    """How far a recorded scroll step moved; negative is down.

    The recorder keeps the amount in the step's text column.
    """
    for value in (step.get("scroll_amount"), step.get("text")):
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if amount:
            return amount
    return default


def describe_step(step: Dict[str, Any]) -> str:
    """One demonstration step as a plain instruction, without coordinates."""
    action = step.get("action_type", "")
    target = clean_target_description(step.get("target_description")).rstrip(".")
    if action == "click":
        return f"Click {target}" if target else "Click the recorded spot"
    if action == "type":
        text = step.get("text") or ""
        return f'Type "{text[:120]}"' if text else "Type the recorded text"
    if action == "hotkey":
        return f"Press {step.get('keys') or 'the recorded keys'}"
    if action == "scroll":
        direction = "down" if scroll_amount(step) < 0 else "up"
        return f"Scroll {direction} over {target}" if target else f"Scroll {direction}"
    return f"{action or 'Step'} {target}".strip()


def new_attempt_lesson_id(demonstration_id: int) -> str:
    """The id one attempt's pearls and its lesson share.

    The demo<id>- prefix is how a demonstration's lesson is found again.
    """
    return f"demo{demonstration_id}-{uuid.uuid4().hex[:12]}"[:36]


def steps_fingerprint(steps: List[Dict[str, Any]]) -> str:
    """Changes when a step's action, target, text or keys change."""
    rows = [
        [s.get("action_type"), s.get("target_description"), s.get("text"), s.get("keys")]
        for s in steps
    ]
    return hashlib.sha1(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()


def record_step_pearl(lesson_id: str, demonstration_id: int, step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Store one signed-off step as a pearl. Needs an app context.

    Returns the pearl as a dict, or None when it could not be written.
    """
    from backend.models import db, ToolFeedback

    try:
        pearl = ToolFeedback(
            lesson_id=lesson_id,
            tool_name=PEARL_TOOL_NAME,
            task=describe_step(step),
            positive=True,
            verdict="up",
            kind=f"demo:{demonstration_id}:step:{step.get('step_index', 0)}",
        )
        db.session.add(pearl)
        db.session.commit()
        return pearl.to_dict()
    except Exception as e:
        logger.error(f"[DEMO-LESSON] pearl for demonstration {demonstration_id} not saved: {e}")
        db.session.rollback()
        return None


def find_demonstration_lesson(demonstration_id: int):
    """The active lesson memory saved from this demonstration, newest first, or None."""
    from backend.models import AgentMemory

    return (
        AgentMemory.query
        .filter(AgentMemory.lesson_id.like(f"demo{int(demonstration_id)}-%"))
        .filter(AgentMemory.source == "lesson_summary")
        .filter(AgentMemory.status == "active")
        .order_by(AgentMemory.created_at.desc())
        .first()
    )


def lesson_brief(memory) -> Optional[Dict[str, Any]]:
    """What the Trainer and the Training page show about a saved lesson."""
    if memory is None:
        return None
    payload = (memory.extra_data or {}).get("lesson") or {}
    if not payload:
        try:
            payload = json.loads(memory.content or "{}")
        except (ValueError, TypeError):
            payload = {}
    return {
        "memory_id": memory.id,
        "title": payload.get("title") or "Lesson",
        "steps": len(payload.get("steps") or []),
    }


def lessons_by_demonstration(demonstration_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """lesson_brief for each of these demonstrations that has a lesson."""
    found = {}
    for demo_id in demonstration_ids:
        brief = lesson_brief(find_demonstration_lesson(demo_id))
        if brief:
            found[demo_id] = brief
    return found


def save_demonstration_lesson(app, demonstration_id: int, lesson_id: str) -> Optional[Dict[str, Any]]:
    """Distill one fully signed-off attempt's pearls into the demonstration's Lesson.

    A demonstration has one lesson. When its steps have not changed since that
    lesson was saved, the lesson is left as it is, edits a person made to it
    included. When they have, the new lesson is saved and the old one archived.

    Returns {"memory_id", "title", "steps", "already_saved"}, or None when no
    lesson could be made.
    """
    from backend.api.lessons_api import _distill_lesson_pearls

    with app.app_context():
        from backend.models import db, Demonstration

        demo = db.session.get(Demonstration, demonstration_id)
        if demo is None:
            return None
        fingerprint = steps_fingerprint([s.to_dict() for s in demo.steps])
        title_hint = (demo.name or "").strip()
        existing = find_demonstration_lesson(demonstration_id)
        if existing is not None and (existing.extra_data or {}).get("steps_fingerprint") == fingerprint:
            return {**lesson_brief(existing), "already_saved": True}
        existing_id = existing.id if existing is not None else None

    summary = _distill_lesson_pearls(
        app,
        lesson_id,
        None,
        title_hint=title_hint,
        extra_metadata={"demonstration_id": demonstration_id, "steps_fingerprint": fingerprint},
    )
    if not summary:
        return None

    if existing_id and existing_id != summary["memory_id"]:
        with app.app_context():
            from backend.models import db
            from backend.api.memory_api import update_memory_entry

            try:
                update_memory_entry(existing_id, {"status": "archived"}, actor=PEARL_TOOL_NAME)
            except Exception as e:
                logger.warning(f"[DEMO-LESSON] could not archive lesson {existing_id}: {e}")
                db.session.rollback()

    return {
        "memory_id": summary["memory_id"],
        "title": summary["title"],
        "steps": len(summary["steps"]),
        "already_saved": False,
    }
