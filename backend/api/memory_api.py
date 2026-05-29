"""Memory API — durable memory write, recall, curation, and audit flows.

Memory tiers in this app are intentionally separate:

- Turn context: current request text, attachments, RAG snippets, and tool state.
- CLI working memory: structured active-file/recent-file hints from `llx`.
- Session history: `LLMSession` / `LLMMessage` chat records and summaries.
- Durable memory: scoped `AgentMemory` rows for reusable facts/preferences/notes.
- Lessons: validated structured procedures stored as durable memories.
- Belief updates: agent-observed contradictions that can later stage fixes.
- Rules/self-knowledge: independent prompt channels that outrank regular memory.

`backend.services.memory_contract` is the single source of truth for allowed
`AgentMemory.type`/`source` values. Legacy `instruction` writes normalize to
`note` so prompt sections stay predictable.
"""

import json
import logging
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import or_

from backend.models import db, AgentMemory, AgentMemoryAudit
from backend.services.memory_contract import (
    MEMORY_SOURCES,
    MEMORY_STATUSES,
    MEMORY_TYPES,
    SOURCE_TRUST_WEIGHTS,
    coerce_confidence,
    coerce_importance,
    memory_match_score,
    normalize_memory_source,
    normalize_memory_status,
    normalize_memory_type,
    normalize_tags,
    source_trust_weight,
    utcnow,
    validate_lesson_payload,
)

logger = logging.getLogger(__name__)

memory_bp = Blueprint("memory", __name__, url_prefix="/api/memory")


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


def _memory_snapshot(memory: AgentMemory | None):
    return memory.to_dict() if memory is not None else None


def _write_memory_audit(action: str, memory_id: str | None, before=None, after=None, actor=None):
    audit = AgentMemoryAudit(
        memory_id=memory_id,
        action=action,
        actor=actor,
        before=before,
        after=after,
    )
    db.session.add(audit)
    return audit


def _rank_reason(memory: AgentMemory, query: str | None = None) -> str:
    tags = normalize_tags(memory.tags)
    parts = [
        f"type={memory.type or 'note'}",
        f"source_trust={source_trust_weight(memory.source):.2f}",
        f"importance={float(memory.importance or 0):.2f}",
    ]
    if getattr(memory, "project_id", None):
        parts.append("project scoped")
    if getattr(memory, "session_id", None):
        parts.append("session scoped")
    if getattr(memory, "workspace_root", None):
        parts.append("workspace scoped")
    match = memory_match_score(memory.content or "", tags, query)
    if match:
        parts.append(f"query_match={match:.2f}")
    if getattr(memory, "last_accessed_at", None):
        parts.append("recalled before")
    return ", ".join(parts)


def _parse_lesson_content(content: str, memory_type: str, source: str):
    if memory_type not in ("lesson", "lesson_summary") and source != "lesson_summary":
        return None
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("Lesson content must be valid JSON")
    ok, error = validate_lesson_payload(payload)
    if not ok:
        raise ValueError(error)
    return payload


def add_memory(
    content: str,
    memory_type: str = "note",
    source: str = "manual",
    importance=None,
    session_id=None,
    tags=None,
    project_id=None,
    user_id=None,
    workspace_root=None,
    lesson_id=None,
    metadata=None,
    confidence=None,
    status="active",
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
    memory_type = normalize_memory_type(memory_type)
    source = normalize_memory_source(source)
    status = normalize_memory_status(status)
    tags_list = normalize_tags(tags)
    importance = coerce_importance(importance, memory_type)
    confidence = coerce_confidence(confidence)
    try:
        lesson_payload = _parse_lesson_content(body, memory_type, source)
    except ValueError as err:
        logger.warning(f"Rejected invalid lesson memory: {err}")
        return None
    extra_data = metadata if isinstance(metadata, dict) else {}
    if lesson_payload:
        extra_data = {**extra_data, "lesson": lesson_payload}
    mem_id = uuid.uuid4().hex[:12]
    try:
        memory = AgentMemory(
            id=mem_id,
            content=body,
            source=source,
            session_id=session_id,
            project_id=project_id,
            user_id=user_id,
            workspace_root=workspace_root,
            lesson_id=lesson_id,
            tags=json.dumps(tags_list) if tags_list else None,
            type=memory_type,
            importance=importance,
            confidence=confidence,
            status=status,
            extra_data=extra_data or None,
        )
        db.session.add(memory)
        _write_memory_audit("create", mem_id, after=memory.to_dict(), actor=source)
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

    memory_type = normalize_memory_type(data.get("type", "note"))
    source = normalize_memory_source(data.get("source", "manual"))
    try:
        _parse_lesson_content(data["content"], memory_type, source)
    except ValueError as err:
        return jsonify({"success": False, "error": str(err)}), 400

    memory = add_memory(
        content=data["content"],
        memory_type=memory_type,
        source=source,
        # None lets DEFAULT_IMPORTANCE_BY_TYPE pick the per-type weight; an
        # explicit numeric value from the client still wins.
        importance=data.get("importance"),
        session_id=data.get("session_id"),
        tags=data.get("tags", []),
        project_id=data.get("project_id"),
        user_id=data.get("user_id"),
        workspace_root=data.get("workspace_root"),
        lesson_id=data.get("lesson_id"),
        metadata=data.get("metadata"),
        confidence=data.get("confidence"),
        status=data.get("status", "active"),
    )
    if memory is None:
        return jsonify({"success": False, "error": "Failed to save memory"}), 500
    return jsonify({"success": True, "memory": memory.to_dict()}), 201


@memory_bp.route("", methods=["GET"])
def list_memories():
    """
    GET /api/memory?search=&limit=50&offset=0&type=&source=&status=&sort=
    List or search memories. sort=trust orders by importance * source trust weight.
    """
    try:
        query = db.session.query(AgentMemory)

        # Filter by type
        mem_type = request.args.get("type")
        if mem_type:
            query = query.filter(AgentMemory.type == normalize_memory_type(mem_type))

        source = request.args.get("source")
        if source:
            query = query.filter(AgentMemory.source == normalize_memory_source(source))

        status = request.args.get("status")
        if status:
            query = query.filter(AgentMemory.status == normalize_memory_status(status))
        else:
            query = query.filter((AgentMemory.status == None) | (AgentMemory.status != "wrong"))

        project_id = request.args.get("project_id", type=int)
        if project_id is not None:
            query = query.filter((AgentMemory.project_id == project_id) | (AgentMemory.project_id == None))

        workspace_root = request.args.get("workspace_root", "").strip()
        if workspace_root:
            query = query.filter((AgentMemory.workspace_root == workspace_root) | (AgentMemory.workspace_root == None))

        # Search by content substring (case-insensitive)
        search = request.args.get("search", "").strip().lower()
        if search:
            query = query.filter(
                (AgentMemory.content.ilike(f"%{search}%")) |
                (AgentMemory.tags.ilike(f"%{search}%"))
            )

        sort_mode = (request.args.get("sort") or "").strip().lower()
        if sort_mode in ("trust", "rank"):
            from sqlalchemy import case, func

            trust_weight = case(
                *[(AgentMemory.source == src, weight) for src, weight in SOURCE_TRUST_WEIGHTS.items()],
                else_=0.7,
            )
            query = query.order_by(
                (
                    func.coalesce(AgentMemory.importance, 0.5)
                    * func.coalesce(AgentMemory.confidence, 1.0)
                    * trust_weight
                ).desc(),
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
        serialized = []
        for memory in memories:
            item = memory.to_dict()
            item["rank_reason"] = _rank_reason(memory, search)
            serialized.append(item)

        return jsonify({
            "success": True,
            "memories": serialized,
            "total": total,
            "limit": limit,
            "offset": offset,
            "allowed_types": sorted(MEMORY_TYPES),
            "allowed_sources": sorted(MEMORY_SOURCES),
            "allowed_statuses": sorted(MEMORY_STATUSES),
        })
    except Exception as e:
        logger.error(f"Failed to load memories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("/<memory_id>", methods=["PATCH"])
def update_memory(memory_id):
    """
    PATCH /api/memory/<id>
    Body: { content?, tags?, type?, importance?, status?, confidence?, metadata? }

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

        before = _memory_snapshot(memory)
        new_type = normalize_memory_type(data.get("type", memory.type)) if data.get("type") else memory.type

        if "content" in data:
            content = (data.get("content") or "").strip()
            if not content:
                return jsonify({"success": False, "error": "Content cannot be empty"}), 400
            try:
                lesson_payload = _parse_lesson_content(content, new_type, data.get("source", memory.source))
            except ValueError as err:
                return jsonify({"success": False, "error": str(err)}), 400
            memory.content = content
            if lesson_payload:
                metadata = dict(memory.extra_data or {})
                metadata["lesson"] = lesson_payload
                memory.extra_data = metadata
        if "tags" in data:
            tags = normalize_tags(data.get("tags") or [])
            memory.tags = json.dumps(tags) if tags else None
        if "type" in data and data["type"]:
            if "content" not in data:
                try:
                    lesson_payload = _parse_lesson_content(memory.content, new_type, data.get("source", memory.source))
                except ValueError as err:
                    return jsonify({"success": False, "error": str(err)}), 400
                if lesson_payload:
                    metadata = dict(memory.extra_data or {})
                    metadata["lesson"] = lesson_payload
                    memory.extra_data = metadata
            memory.type = new_type
        if "importance" in data and data["importance"] is not None:
            try:
                float(data["importance"])
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "importance must be numeric"}), 400
            memory.importance = coerce_importance(data["importance"], memory.type)
        if "confidence" in data and data["confidence"] is not None:
            try:
                float(data["confidence"])
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "confidence must be numeric"}), 400
            memory.confidence = coerce_confidence(data["confidence"])
        if "status" in data and data["status"]:
            memory.status = normalize_memory_status(data["status"])
        if "project_id" in data:
            memory.project_id = data.get("project_id")
        if "user_id" in data:
            memory.user_id = data.get("user_id")
        if "workspace_root" in data:
            memory.workspace_root = data.get("workspace_root")
        if "lesson_id" in data:
            memory.lesson_id = data.get("lesson_id")
        if "metadata" in data:
            memory.extra_data = data.get("metadata") if isinstance(data.get("metadata"), dict) else None

        memory.updated_at = datetime.now()
        _write_memory_audit(
            "update",
            memory_id,
            before=before,
            after=memory.to_dict(),
            actor=(data.get("source") or "manual"),
        )
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

        before = _memory_snapshot(memory)
        db.session.delete(memory)
        _write_memory_audit("delete", memory_id, before=before, actor="manual")
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
    data = request.get_json(silent=True) or {}
    if data.get("confirmation") != "CLEAR_MEMORIES":
        return jsonify({
            "success": False,
            "error": "confirmation must be CLEAR_MEMORIES",
        }), 400
    try:
        snapshots = [memory.to_dict() for memory in db.session.query(AgentMemory).all()]
        count = db.session.query(AgentMemory).delete()
        _write_memory_audit(
            "clear",
            None,
            before={"count": count, "memories": snapshots[:100]},
            actor="manual",
        )
        db.session.commit()
        logger.info(f"All {count} memories cleared")
        return jsonify({"success": True, "message": f"All {count} memories cleared"})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to clear memories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("/recall-debug", methods=["GET", "POST"])
def recall_debug():
    """Return selected memory ids and scores for a recall query."""
    data = request.get_json(silent=True) if request.method == "POST" else request.args
    data = data or {}
    memories = _query_memories(
        limit=int(data.get("limit", 10)),
        query=data.get("query"),
        session_id=data.get("session_id"),
        project_id=data.get("project_id"),
        workspace_root=data.get("workspace_root"),
        user_id=data.get("user_id"),
        types=data.get("types") if isinstance(data.get("types"), list) else None,
        sources=data.get("sources") if isinstance(data.get("sources"), list) else None,
    )
    return jsonify({
        "success": True,
        "results": [
            {
                "id": memory.id,
                "type": memory.type,
                "source": memory.source,
                "score": getattr(memory, "_recall_score", None),
                "rank_reason": getattr(memory, "_rank_reason", _rank_reason(memory, data.get("query"))),
                "content_preview": (memory.content or "")[:160],
            }
            for memory in memories
        ],
    })


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
    session_id: str = None,
    project_id=None,
    user_id: str = None,
    workspace_root: str = None,
    status: str = "active",
    include_global: bool = True,
    cli_working_memory: dict | None = None,
):
    """Single source of truth for memory SELECT.

    sources / types: optional lists of strings. Empty/None means no filter on
    that column. Scopes include matching rows plus global rows by default.
    Ordering uses a lightweight hybrid rank: importance, source trust, query/tag
    match, confidence, recency, and scope match. Embeddings can layer on later.
    """
    try:
        q = db.session.query(AgentMemory)
        if sources:
            normalized_sources = [normalize_memory_source(src) for src in sources]
            q = q.filter(AgentMemory.source.in_(normalized_sources))
        if types:
            normalized_types = [normalize_memory_type(mem_type) for mem_type in types]
            q = q.filter(AgentMemory.type.in_(normalized_types))
        if status:
            q = q.filter(AgentMemory.status == normalize_memory_status(status))
        else:
            q = q.filter((AgentMemory.status == None) | (AgentMemory.status != "wrong"))
        if session_id:
            q = q.filter(
                or_(AgentMemory.session_id == session_id, AgentMemory.session_id == None)
                if include_global else AgentMemory.session_id == session_id
            )
        if project_id is not None:
            q = q.filter(
                or_(AgentMemory.project_id == project_id, AgentMemory.project_id == None)
                if include_global else AgentMemory.project_id == project_id
            )
        if user_id:
            q = q.filter(
                or_(AgentMemory.user_id == user_id, AgentMemory.user_id == None)
                if include_global else AgentMemory.user_id == user_id
            )
        if workspace_root:
            q = q.filter(
                or_(AgentMemory.workspace_root == workspace_root, AgentMemory.workspace_root == None)
                if include_global else AgentMemory.workspace_root == workspace_root
            )
        recall_query = query
        if cli_working_memory:
            file_hints = []
            for key in ("active_file", "pending_edit_target"):
                if cli_working_memory.get(key):
                    file_hints.append(str(cli_working_memory[key]))
            for item in cli_working_memory.get("recent_files") or []:
                file_hints.append(str(item))
            if file_hints:
                recall_query = f"{query or ''} {' '.join(file_hints)}".strip()
        if recall_query:
            terms = list(recall_query.split())[:8]
            clauses = []
            for term in terms:
                if len(term) >= 3:
                    search_term = f"%{term.lower()}%"
                    clauses.extend([
                        AgentMemory.content.ilike(search_term),
                        AgentMemory.tags.ilike(search_term),
                    ])
            if clauses:
                q = q.filter(or_(*clauses))

        candidates = q.order_by(
            AgentMemory.importance.desc(),
            AgentMemory.created_at.desc(),
        ).limit(max(limit * 5, 50)).all()

        def score(memory):
            tags = normalize_tags(memory.tags)
            trust = source_trust_weight(memory.source)
            match = memory_match_score(memory.content or "", tags, recall_query)
            confidence = float(memory.confidence or 1.0)
            importance = float(memory.importance or 0.5)
            recency = 0.0
            if memory.created_at:
                age_days = max(0, (utcnow() - memory.created_at).days)
                recency = max(0.0, 1.0 - min(age_days, 90) / 90)
            scope = 0.0
            if session_id and memory.session_id == session_id:
                scope += 0.4
            if project_id is not None and memory.project_id == project_id:
                scope += 0.3
            if workspace_root and memory.workspace_root == workspace_root:
                scope += 0.2
            if user_id and memory.user_id == user_id:
                scope += 0.1
            scope = min(scope, 1.0)
            return (
                importance * 0.35
                + confidence * 0.10
                + trust * 0.20
                + match * 0.25
                + recency * 0.05
                + scope * 0.05
            )

        always_on = []
        if recall_query and not types:
            always_q = db.session.query(AgentMemory).filter(
                AgentMemory.type.in_(["fact", "note"]),
                AgentMemory.importance >= 0.85,
                AgentMemory.status == normalize_memory_status(status),
            )
            if sources:
                always_q = always_q.filter(AgentMemory.source.in_(normalized_sources))
            if project_id is not None:
                always_q = always_q.filter(
                    or_(AgentMemory.project_id == project_id, AgentMemory.project_id == None)
                    if include_global else AgentMemory.project_id == project_id
                )
            if user_id:
                always_q = always_q.filter(
                    or_(AgentMemory.user_id == user_id, AgentMemory.user_id == None)
                    if include_global else AgentMemory.user_id == user_id
                )
            if workspace_root:
                always_q = always_q.filter(
                    or_(AgentMemory.workspace_root == workspace_root, AgentMemory.workspace_root == None)
                    if include_global else AgentMemory.workspace_root == workspace_root
                )
            always_on = always_q.order_by(
                AgentMemory.importance.desc(),
                AgentMemory.created_at.desc(),
            ).limit(3).all()

        ranked = sorted(candidates, key=score, reverse=True)
        selected = []
        seen = set()
        for memory in always_on + ranked:
            if memory.id in seen:
                continue
            memory._recall_score = round(score(memory), 4)
            memory._rank_reason = _rank_reason(memory, recall_query)
            selected.append(memory)
            seen.add(memory.id)
            if len(selected) >= limit:
                break

        if selected:
            now = utcnow()
            for memory in selected:
                memory.access_count = int(memory.access_count or 0) + 1
                memory.last_accessed_at = now
            db.session.commit()
        return selected
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning(f"Memory query failed (sources={sources}, types={types}): {e}")
        return []


def get_memories_for_context(
    limit: int = 20,
    max_tokens: int = 500,
    query: str = None,
    session_id: str = None,
    project_id=None,
    user_id: str = None,
    workspace_root: str = None,
    cli_working_memory: dict | None = None,
) -> str:
    """
    Load relevant memories formatted for injection into the LLM system prompt.
    Called by unified_chat_engine and agent_brain during prompt construction.

    If a query is provided, it attempts a keyword search. Otherwise, it returns
    the most important/recent memories. Output is one line per memory; lesson
    summaries are flattened from their stored JSON shape into imperative text.

    Returns a formatted string, or empty string if no memories exist.
    """
    memories = _query_memories(
        limit=limit,
        query=query,
        session_id=session_id,
        project_id=project_id,
        user_id=user_id,
        workspace_root=workspace_root,
        cli_working_memory=cli_working_memory,
    )

    if not memories:
        return ""

    char_budget = max_tokens * 4  # rough chars-to-tokens ratio
    used = 0

    # Group by category. lesson_summary stays as its own bucket (source-based);
    # everything else groups by type so each lands under a framing header
    # tailored to how strictly the model should treat it.
    groups = {"fact": [], "note": [], "preference": [], "lesson_summary": [], "other": []}
    for m in memories:
        if m.source == "lesson_summary" or m.type in ("lesson", "lesson_summary"):
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
    session_id: str = None,
    project_id=None,
    workspace_root: str = None,
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
        session_id=session_id,
        project_id=project_id,
        workspace_root=workspace_root,
    )
    rows = list(lesson_rows)
    if include_belief_updates:
        belief_rows = _query_memories(
            types=["belief_update"],
            limit=belief_limit,
            session_id=session_id,
            project_id=project_id,
            workspace_root=workspace_root,
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
