"""
Memory API — Save and recall knowledge from chat.

Stores memories in the PostgreSQL database via AgentMemory model.
"""

import json
import logging
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from backend.models import db, AgentMemory

logger = logging.getLogger(__name__)

memory_bp = Blueprint("memory", __name__, url_prefix="/api/memory")


# Per-type defaults applied when the caller doesn't pass an explicit importance.
# Facts and notes are load-bearing for behavior (ground truth + operating rules),
# so they outrank softer preferences in the importance-DESC recall order used by
# _query_memories. Lesson summaries and belief updates have their own semantics
# documented next to the lesson reconciler.
DEFAULT_IMPORTANCE_BY_TYPE = {
    "fact": 0.85,
    "note": 0.80,
    "preference": 0.65,
    "lesson_summary": 0.75,
    "belief_update": 0.55,
}

# Per-type per-line truncation budgets. Notes are imperative rules and need
# room to state the rule. Facts are typically short attributes. Preferences
# sit in the middle. Anything else falls back to the legacy 300-char cap.
TRUNCATE_BY_TYPE = {
    "fact": 200,
    "note": 400,
    "preference": 250,
}

# Section headers framing each group for the LLM. The framing words tell the
# model how strictly to weigh the content: facts override defaults, notes are
# imperative rules, preferences yield to explicit per-turn user requests.
SECTION_HEADERS = {
    "fact": "Known facts about the user (treat as ground truth — do not contradict):",
    "note": "Operating notes (treat as imperative rules — follow when applicable):",
    "preference": "User preferences (apply by default; the user can override per turn):",
}


def add_memory(
    content: str,
    memory_type: str = "note",
    source: str = "manual",
    importance=None,
    session_id=None,
    tags=None,
):
    """In-process callable for writing a memory row.

    The HTTP route `POST /api/memory` is a thin wrapper around this; everything
    else inside the app (agent_control_service belief writes, lesson distillers,
    background reconcilers) should call this directly rather than POSTing to
    itself. Single source of truth for the column defaults + side effects.

    Returns the created AgentMemory on success, or None on blank content /
    DB error. Errors are logged; the caller decides whether a failed memory
    write is fatal (usually not).
    """
    body = (content or "").strip()
    if not body:
        return None
    tags_list = list(tags) if tags else []
    mem_id = uuid.uuid4().hex[:12]
    if importance is None:
        importance = DEFAULT_IMPORTANCE_BY_TYPE.get(memory_type, 0.7)
    try:
        memory = AgentMemory(
            id=mem_id,
            content=body,
            source=source,
            session_id=session_id,
            tags=json.dumps(tags_list) if tags_list else None,
            type=memory_type,
            importance=importance,
        )
        db.session.add(memory)
        db.session.commit()
        logger.info(f"Memory saved: {mem_id} ({memory.type}) from {memory.source}")
        return memory
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to save memory: {e}")
        return None


@memory_bp.route("", methods=["POST"])
def save_memory():
    """
    POST /api/memory
    Body: { content, source?, session_id?, tags?, type?, importance? }

    Save a memory entry. Content is the only required field.
    """
    data = request.get_json(silent=True)
    if not data or not data.get("content", "").strip():
        return jsonify({"success": False, "error": "Content is required"}), 400

    memory = add_memory(
        content=data["content"],
        memory_type=data.get("type", "note"),
        source=data.get("source", "manual"),
        # None lets DEFAULT_IMPORTANCE_BY_TYPE pick the per-type weight; an
        # explicit numeric value from the client still wins.
        importance=data.get("importance"),
        session_id=data.get("session_id"),
        tags=data.get("tags", []),
    )
    if memory is None:
        return jsonify({"success": False, "error": "Failed to save memory"}), 500
    return jsonify({"success": True, "memory": memory.to_dict()}), 201


@memory_bp.route("", methods=["GET"])
def list_memories():
    """
    GET /api/memory?search=&limit=50&offset=0&type=&sort=
    List or search memories. sort=trust orders by importance * source trust weight.
    """
    try:
        query = db.session.query(AgentMemory)
        
        # Filter by type
        mem_type = request.args.get("type")
        if mem_type:
            query = query.filter(AgentMemory.type == mem_type)

        # Search by content substring (case-insensitive)
        search = request.args.get("search", "").strip().lower()
        if search:
            query = query.filter(
                (AgentMemory.content.ilike(f"%{search}%")) |
                (AgentMemory.tags.ilike(f"%{search}%"))
            )

        sort_mode = (request.args.get("sort") or "").strip().lower()
        if sort_mode == "trust":
            from sqlalchemy import case

            trust_weight = case(
                (AgentMemory.source == "manual", 1.0),
                (AgentMemory.source == "cli", 0.95),
                (AgentMemory.source == "chat", 0.88),
                (AgentMemory.source == "agent", 0.82),
                else_=0.7,
            )
            query = query.order_by(
                (AgentMemory.importance * trust_weight).desc(),
                AgentMemory.created_at.desc(),
            )
        else:
            # Sort newest first
            query = query.order_by(AgentMemory.created_at.desc())

        # Paginate
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)
        
        total = query.count()
        memories = query.offset(offset).limit(limit).all()

        return jsonify({
            "success": True,
            "memories": [m.to_dict() for m in memories],
            "total": total,
            "limit": limit,
            "offset": offset,
        })
    except Exception as e:
        logger.error(f"Failed to load memories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("/<memory_id>", methods=["PATCH"])
def update_memory(memory_id):
    """
    PATCH /api/memory/<id>
    Body: { content?, tags?, type?, importance? }

    Update a memory in place. Used by the post-lesson summary modal to save
    edited steps, and by the general memory UI edit affordance. The `source`
    and `session_id` fields are intentionally immutable here — they're keys
    the pearl distiller relies on for UPSERT behavior.
    """
    data = request.get_json(silent=True) or {}
    try:
        memory = db.session.query(AgentMemory).filter_by(id=memory_id).first()
        if not memory:
            return jsonify({"success": False, "error": "Memory not found"}), 404

        if "content" in data:
            content = (data.get("content") or "").strip()
            if not content:
                return jsonify({"success": False, "error": "Content cannot be empty"}), 400
            memory.content = content
        if "tags" in data:
            tags = data.get("tags") or []
            memory.tags = json.dumps(tags) if tags else None
        if "type" in data and data["type"]:
            memory.type = data["type"]
        if "importance" in data and data["importance"] is not None:
            try:
                memory.importance = float(data["importance"])
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "importance must be numeric"}), 400

        memory.updated_at = datetime.now()
        db.session.commit()

        logger.info(f"Memory updated: {memory_id}")
        return jsonify({"success": True, "memory": memory.to_dict()})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to update memory {memory_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("/<memory_id>", methods=["DELETE"])
def delete_memory(memory_id):
    """
    DELETE /api/memory/<id>
    Remove a single memory by ID.
    """
    try:
        memory = db.session.query(AgentMemory).filter_by(id=memory_id).first()
        if not memory:
            return jsonify({"success": False, "error": "Memory not found"}), 404

        db.session.delete(memory)
        db.session.commit()

        logger.info(f"Memory deleted: {memory_id}")
        return jsonify({"success": True, "message": f"Deleted memory {memory_id}"})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to delete memory: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("/clear", methods=["DELETE"])
def clear_memories():
    """
    DELETE /api/memory/clear
    Remove all memories. Use with caution.
    """
    try:
        count = db.session.query(AgentMemory).delete()
        db.session.commit()
        logger.info(f"All {count} memories cleared")
        return jsonify({"success": True, "message": f"All {count} memories cleared"})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to clear memories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# -- Helpers for LLM context injection --
#
# Two output shapes share one SQL query: `_query_memories()` is the single
# source of truth for *which* rows get selected. The two public formatters
# (`get_memories_for_context`, `get_lessons_for_agent_prompt`) decide how the
# selected rows render. All three callers — unified_chat_engine, agent_brain,
# agent_control_service — go through this file so changes to ordering, source
# filtering, or de-dup behaviour land in exactly one place.


def _query_memories(
    sources=None,
    types=None,
    limit: int = 20,
    query: str = None,
):
    """Single source of truth for memory SELECT.

    sources / types: optional lists of strings. Empty/None means no filter on
    that column. Ordering is always importance DESC, created_at DESC — the
    canonical recall order across every caller in the codebase.
    """
    try:
        q = db.session.query(AgentMemory)
        if sources:
            q = q.filter(AgentMemory.source.in_(list(sources)))
        if types:
            q = q.filter(AgentMemory.type.in_(list(types)))
        if query:
            search_term = f"%{query.lower()}%"
            q = q.filter(
                (AgentMemory.content.ilike(search_term)) |
                (AgentMemory.tags.ilike(search_term))
            )
        return q.order_by(
            AgentMemory.importance.desc(),
            AgentMemory.created_at.desc(),
        ).limit(limit).all()
    except Exception as e:
        logger.warning(f"Memory query failed (sources={sources}, types={types}): {e}")
        return []


def get_memories_for_context(limit: int = 20, max_tokens: int = 500, query: str = None) -> str:
    """
    Load relevant memories formatted for injection into the LLM system prompt.
    Called by unified_chat_engine and agent_brain during prompt construction.

    If a query is provided, it attempts a keyword search. Otherwise, it returns
    the most important/recent memories. Output is one line per memory; lesson
    summaries are flattened from their stored JSON shape into imperative text.

    Returns a formatted string, or empty string if no memories exist.
    """
    memories = _query_memories(limit=limit, query=query)

    if not memories:
        return ""

    char_budget = max_tokens * 4  # rough chars-to-tokens ratio
    used = 0

    # Group by category. lesson_summary stays as its own bucket (source-based);
    # everything else groups by type so each lands under a framing header
    # tailored to how strictly the model should treat it.
    groups = {"fact": [], "note": [], "preference": [], "lesson_summary": [], "other": []}
    for m in memories:
        if m.source == "lesson_summary":
            groups["lesson_summary"].append(m)
        elif m.type in groups:
            groups[m.type].append(m)
        else:
            groups["other"].append(m)

    sections = []

    def _render_plain(items, truncate):
        """Render a flat group with a per-line truncation cap. Returns the
        list of body lines actually fit into the char budget."""
        nonlocal used
        out = []
        for m in items:
            content = (m.content or "").strip()
            if not content:
                continue
            if len(content) > truncate:
                content = content[: truncate - 3] + "..."
            line = f"- {content}"
            if used + len(line) > char_budget:
                return out
            out.append(line)
            used += len(line)
        return out

    for type_name in ("fact", "note", "preference"):
        body = _render_plain(groups[type_name], TRUNCATE_BY_TYPE[type_name])
        if body:
            sections.append("\n".join([SECTION_HEADERS[type_name]] + body))

    # Lessons keep their bespoke JSON-flatten path; they need parameter
    # expansion that plain memories don't. The 1200-char cap reflects step
    # lists + parameter definitions needing the room.
    if groups["lesson_summary"]:
        lesson_lines = []
        for m in groups["lesson_summary"]:
            content = (m.content or "").strip()
            if not content:
                continue
            try:
                lesson_data = json.loads(content)
                title = (lesson_data.get("title") or "Task").strip()
                steps = lesson_data.get("steps") or []
                step_texts = []
                for s in sorted(steps, key=lambda x: x.get("order", 0) if isinstance(x, dict) else 0):
                    if isinstance(s, dict):
                        text = (s.get("text") or "").strip()
                    else:
                        text = str(s).strip()
                    if text:
                        step_texts.append(f"{s.get('order') if isinstance(s, dict) else len(step_texts) + 1}. {text}")
                flattened = f"LESSON ({title}): " + " -> ".join(step_texts)

                # PARAMETERS line teaches the model that "{channel}" in the
                # steps is a slot to fill from the current user request.
                parameters = lesson_data.get("parameters") or []
                if parameters:
                    param_strs = []
                    for p in parameters:
                        if not isinstance(p, dict):
                            continue
                        name = (p.get("name") or "").strip()
                        desc = (p.get("description") or "").strip()
                        example = (p.get("example") or "").strip()
                        if not name:
                            continue
                        token = f"{{{name}}}"
                        parts = [desc] if desc else []
                        if example:
                            parts.append(f"e.g. {example}")
                        suffix = f" ({'; '.join(parts)})" if parts else ""
                        param_strs.append(f"{token}{suffix}")
                    if param_strs:
                        flattened += " | PARAMETERS: " + ", ".join(param_strs)

                if len(flattened) > 1200:
                    flattened = flattened[:1197] + "..."
                content = flattened
            except Exception as parse_err:
                logger.warning(
                    f"Lesson memory {m.id} has malformed JSON content; "
                    f"falling back to truncated raw: {parse_err}"
                )
                if len(content) > 300:
                    content = content[:297] + "..."
            line = f"- {content}"
            if used + len(line) > char_budget:
                break
            lesson_lines.append(line)
            used += len(line)
        if lesson_lines:
            sections.append(
                "\n".join(
                    ["Learned procedures (apply when the task matches):"] + lesson_lines
                )
            )

    # Catch-all for any non-standard type (legacy "instruction", "snippet",
    # belief_update emitted through this path, etc). Kept under a neutral
    # header so they still surface, just without type-specific framing.
    if groups["other"]:
        other_body = _render_plain(groups["other"], 300)
        if other_body:
            sections.append("\n".join(["Other saved memories:"] + other_body))

    if not sections:
        return ""
    return "\n\n".join(sections)


def get_lessons_for_agent_prompt(
    max_rows: int = 6,
    max_chars: int = 2500,
    include_belief_updates: bool = True,
    belief_limit: int = 4,
) -> str:
    """Structured Markdown for the screen-control agent's persistent knowledge.

    Distinct from `get_memories_for_context` because the agent prompt has more
    room and benefits from the multi-line `### Title / 1. step` shape — chat
    has to stay terse and one-line-per-memory. Both formatters share
    `_query_memories` so source filtering and ordering live in one place.

    Sources kept: lesson_summary (End-Lesson distillations) and manual
    (user-typed notes). belief_update memories are merged in optionally — they
    surface as short hedges next to the lessons they qualify.

    Returns the full block including its section header, or empty string when
    there are no rows to show.
    """
    lesson_rows = _query_memories(
        sources=["lesson_summary", "manual"],
        limit=max_rows,
    )
    rows = list(lesson_rows)
    if include_belief_updates:
        belief_rows = _query_memories(
            types=["belief_update"],
            limit=belief_limit,
        )
        seen_ids = {r.id for r in rows}
        rows.extend(r for r in belief_rows if r.id not in seen_ids)

    if not rows:
        return ""

    sections = []
    total = 0
    for row in rows:
        content = (row.content or "").strip()
        if not content:
            continue
        block = ""
        if row.source == "lesson_summary":
            try:
                payload = json.loads(content)
                title = (payload.get("title") or "Lesson").strip()
                steps = payload.get("steps") or []
                step_lines = []
                for s in steps:
                    text = (s.get("text") or s.get("step") or "").strip() if isinstance(s, dict) else str(s).strip()
                    if text:
                        step_lines.append(f"  {len(step_lines)+1}. {text[:200]}")
                if step_lines:
                    block = f"### {title}\n" + "\n".join(step_lines)
            except Exception:
                block = f"### Lesson\n{content[:600]}"
        elif getattr(row, "type", "") == "belief_update":
            block = f"- Belief update: {content[:400]}"
        else:  # manual notes / facts / instructions
            block = f"- {content[:400]}"
        if not block:
            continue
        if total + len(block) > max_chars:
            break
        sections.append(block)
        total += len(block) + 2

    if not sections:
        return ""
    return "## Lessons & Notes (cross-session memory — apply when relevant)\n" + "\n\n".join(sections)
