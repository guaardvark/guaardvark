"""
Memory API — Save and recall knowledge from chat.

Stores memories as append-only JSONL in data/memory/memories.jsonl.
Same pattern as servo_archive.jsonl and recipes.json — flat files, no DB.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

from backend.config import GUAARDVARK_ROOT

logger = logging.getLogger(__name__)

memory_bp = Blueprint("memory", __name__, url_prefix="/api/memory")

MEMORY_DIR = Path(GUAARDVARK_ROOT) / "data" / "memory"
MEMORY_FILE = MEMORY_DIR / "memories.jsonl"


def _ensure_memory_dir():
    """Create data/memory/ if it doesn't exist."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_memories():
    """Read all memories from the JSONL file."""
    if not MEMORY_FILE.exists():
        return []
    memories = []
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                memories.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return memories


def _append_memory(entry: dict):
    """Append a single memory entry to the JSONL file."""
    _ensure_memory_dir()
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _rewrite_memories(memories: list):
    """Rewrite the entire file (used for deletes)."""
    _ensure_memory_dir()
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        for entry in memories:
            f.write(json.dumps(entry) + "\n")


@memory_bp.route("", methods=["POST"])
def save_memory():
    """
    POST /api/memory
    Body: { content, source?, session_id?, tags?, type? }

    Save a memory entry. Content is the only required field.
    """
    data = request.get_json(silent=True)
    if not data or not data.get("content", "").strip():
        return jsonify({"success": False, "error": "Content is required"}), 400

    entry = {
        "id": uuid.uuid4().hex[:12],
        "content": data["content"].strip(),
        "source": data.get("source", "manual"),  # manual, chat, cli, auto
        "session_id": data.get("session_id"),
        "tags": data.get("tags", []),
        "type": data.get("type", "note"),  # note, fact, instruction, snippet
        "created_at": datetime.now().isoformat(),
        "timestamp": time.time(),
    }

    try:
        _append_memory(entry)
        logger.info(f"Memory saved: {entry['id']} ({entry['type']}) from {entry['source']}")
        return jsonify({"success": True, "memory": entry}), 201
    except Exception as e:
        logger.error(f"Failed to save memory: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("", methods=["GET"])
def list_memories():
    """
    GET /api/memory?search=&limit=50&offset=0&type=
    List or search memories.
    """
    try:
        memories = _load_memories()
    except Exception as e:
        logger.error(f"Failed to load memories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    # Filter by type
    mem_type = request.args.get("type")
    if mem_type:
        memories = [m for m in memories if m.get("type") == mem_type]

    # Search by content substring (case-insensitive)
    search = request.args.get("search", "").strip().lower()
    if search:
        memories = [
            m for m in memories
            if search in m.get("content", "").lower()
            or any(search in t.lower() for t in m.get("tags", []))
        ]

    # Sort newest first
    memories.sort(key=lambda m: m.get("timestamp", 0), reverse=True)

    # Paginate
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    total = len(memories)
    memories = memories[offset:offset + limit]

    return jsonify({
        "success": True,
        "memories": memories,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@memory_bp.route("/<memory_id>", methods=["DELETE"])
def delete_memory(memory_id):
    """
    DELETE /api/memory/<id>
    Remove a single memory by ID.
    """
    try:
        memories = _load_memories()
        original_count = len(memories)
        memories = [m for m in memories if m.get("id") != memory_id]

        if len(memories) == original_count:
            return jsonify({"success": False, "error": "Memory not found"}), 404

        _rewrite_memories(memories)
        logger.info(f"Memory deleted: {memory_id}")
        return jsonify({"success": True, "message": f"Deleted memory {memory_id}"})
    except Exception as e:
        logger.error(f"Failed to delete memory: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("/clear", methods=["DELETE"])
def clear_memories():
    """
    DELETE /api/memory/clear
    Remove all memories. Use with caution.
    """
    try:
        if MEMORY_FILE.exists():
            MEMORY_FILE.unlink()
        logger.info("All memories cleared")
        return jsonify({"success": True, "message": "All memories cleared"})
    except Exception as e:
        logger.error(f"Failed to clear memories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# -- Helper for LLM context injection --

def get_memories_for_context(limit: int = 20, max_tokens: int = 500) -> str:
    """
    Load recent memories formatted for injection into LLM system prompt.
    Called by unified_chat_engine and brain_state during prompt construction.

    Returns a formatted string, or empty string if no memories exist.
    """
    try:
        memories = _load_memories()
    except Exception:
        return ""

    if not memories:
        return ""

    # Most recent first, capped
    memories.sort(key=lambda m: m.get("timestamp", 0), reverse=True)
    memories = memories[:limit]

    lines = ["User's saved memories (treat as facts/preferences):"]
    char_budget = max_tokens * 4  # rough chars-to-tokens ratio
    used = len(lines[0])

    for m in memories:
        content = m.get("content", "").strip()
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
