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

    tags = data.get("tags", [])
    mem_id = uuid.uuid4().hex[:12]
    
    try:
        memory = AgentMemory(
            id=mem_id,
            content=data["content"].strip(),
            source=data.get("source", "manual"),  # manual, chat, cli, auto, agent
            session_id=data.get("session_id"),
            tags=json.dumps(tags) if tags else None,
            type=data.get("type", "note"),  # note, fact, instruction, snippet
            importance=data.get("importance", 0.5)
        )
        db.session.add(memory)
        db.session.commit()
        
        logger.info(f"Memory saved: {mem_id} ({memory.type}) from {memory.source}")
        return jsonify({"success": True, "memory": memory.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to save memory: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("", methods=["GET"])
def list_memories():
    """
    GET /api/memory?search=&limit=50&offset=0&type=
    List or search memories.
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


# -- Helper for LLM context injection --

def get_memories_for_context(limit: int = 20, max_tokens: int = 500, query: str = None) -> str:
    """
    Load relevant memories formatted for injection into LLM system prompt.
    Called by unified_chat_engine and brain_state during prompt construction.

    If a query is provided, it attempts a keyword search. Otherwise, it returns
    the most important/recent memories.

    Returns a formatted string, or empty string if no memories exist.
    """
    try:
        db_query = db.session.query(AgentMemory)
        
        if query:
            # Simple keyword search on content or tags
            search_term = f"%{query.lower()}%"
            db_query = db_query.filter(
                (AgentMemory.content.ilike(search_term)) |
                (AgentMemory.tags.ilike(search_term))
            )
            
        # Order by importance first, then recency
        memories = db_query.order_by(
            AgentMemory.importance.desc(), 
            AgentMemory.created_at.desc()
        ).limit(limit).all()
        
    except Exception as e:
        logger.warning(f"Failed to fetch memories for context: {e}")
        return ""

    if not memories:
        return ""

    lines = ["User's saved memories (treat as facts/preferences):"]
    char_budget = max_tokens * 4  # rough chars-to-tokens ratio
    used = len(lines[0])

    for m in memories:
        content = m.content.strip()
        if not content:
            continue
        # Truncate individual entries that are too long
        if len(content) > 300:
            content = content[:297] + "..."
        line = f"- {content}"
        if used + len(line) > char_budget:
            break
        lines.append(line)
        used += len(line)

    if len(lines) == 1:
        return ""  # header only, no actual memories

    return "\n".join(lines)
