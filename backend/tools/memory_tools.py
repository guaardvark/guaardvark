"""
Memory Tools — Allows the agent to manage its own long-term memory.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.models import db, AgentMemory, AgentMemoryAudit
from backend.api.memory_api import add_memory, _query_memories
from backend.utils.backend_http import BackendError, is_mcp_transport, request_json

logger = logging.getLogger(__name__)

class SaveMemoryTool(BaseTool):
    """Save a fact, preference, or note to long-term memory."""
    
    name = "save_memory"
    read_only = False
    destructive = False
    description = "Save a fact, user preference, or instruction to long-term memory. Use this to remember things the user tells you about themselves, their projects, or how they want you to behave."
    is_dangerous = False
    requires_approval = False

    parameters = {
        "content": ToolParameter(
            name="content",
            type="string",
            description="The fact, preference, or instruction to remember. Be specific and concise.",
            required=True
        ),
        "type": ToolParameter(
            name="type",
            type="string",
            description="Type of memory: 'fact', 'preference', or 'note'. Legacy 'instruction' is normalized to 'note'.",
            required=False,
            default="fact"
        ),
        "tags": ToolParameter(
            name="tags",
            type="list",
            description="List of string tags for categorization (e.g. ['python', 'formatting']).",
            required=False,
            default=[]
        ),
        "importance": ToolParameter(
            name="importance",
            type="float",
            description="Importance score from 0.0 to 1.0. Higher means it should be retrieved more often.",
            required=False,
            default=0.8
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        content = kwargs.get("content")
        mem_type = kwargs.get("type", "fact")
        tags = kwargs.get("tags", [])
        importance = kwargs.get("importance", 0.8)
        agent_context = kwargs.get("_agent_context", {})
        session_id = agent_context.get("session_id")
        project_id = agent_context.get("project_id")
        workspace_root = agent_context.get("workspace_root")

        if not (content or "").strip():
            return ToolResult(success=False, error="content is required.")

        if is_mcp_transport(self):
            return self._save_via_backend(
                content, mem_type, tags, importance, session_id, project_id, workspace_root,
            )

        try:
            memory = add_memory(
                content=content,
                memory_type=mem_type,
                source="agent",
                session_id=session_id,
                project_id=project_id,
                workspace_root=workspace_root,
                tags=tags,
                importance=importance,
            )
            if memory is None:
                return ToolResult(success=False, error="Memory was rejected or could not be saved.")

            logger.info(f"Agent saved memory {memory.id}: {content[:50]}...")
            return ToolResult(
                success=True,
                output=f"Successfully saved to long-term memory (ID: {memory.id}).",
                metadata={"id": memory.id, "content": memory.content, "type": memory.type}
            )
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            logger.error(f"Failed to save memory: {e}")
            return ToolResult(success=False, error=f"Database error: {str(e)}")

    def _save_via_backend(self, content, mem_type, tags, importance, session_id, project_id, workspace_root) -> ToolResult:
        # The MCP server process has no Flask app; POST /api/memory owns
        # validation, the audit row and the commit.
        payload = {
            "content": content,
            "type": mem_type,
            "source": "agent",
            "tags": tags,
            "importance": importance,
            "session_id": session_id,
            "project_id": project_id,
            "workspace_root": workspace_root,
        }
        try:
            body = request_json("POST", "/api/memory", payload=payload).body
        except BackendError as e:
            return ToolResult(success=False, error=f"Memory was not saved: {e}")
        memory = (body or {}).get("memory") or {}
        if not memory.get("id"):
            return ToolResult(success=False, error="Memory was not saved: the backend returned no memory id.")
        return ToolResult(
            success=True,
            output=f"Successfully saved to long-term memory (ID: {memory['id']}).",
            metadata={"id": memory["id"], "content": memory.get("content"), "type": memory.get("type")},
        )


class SearchMemoryTool(BaseTool):
    """Search the agent's long-term memory."""
    
    name = "search_memory"
    read_only = True
    description = "Search your long-term memory for previously saved facts, preferences, or instructions."
    is_dangerous = False
    requires_approval = False
    
    parameters = {
        "query": ToolParameter(
            name="query",
            type="string",
            description="Search query or keyword to look for in memories.",
            required=True
        ),
        "limit": ToolParameter(
            name="limit",
            type="integer",
            description="Maximum number of results to return (default 5).",
            required=False,
            default=5
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        query = kwargs.get("query", "").lower()
        limit = kwargs.get("limit", 5)

        if is_mcp_transport(self):
            return self._search_via_backend(query, limit)

        try:
            memories = _query_memories(query=query, limit=limit, raise_errors=True)
            
            if not memories:
                return ToolResult(
                    success=True,
                    output=f"No memories found matching '{query}'.",
                    metadata={"results": []}
                )
                
            results = []
            output_lines = [f"Found {len(memories)} memories matching '{query}':"]
            for m in memories:
                results.append(m.to_dict())
                output_lines.append(f"- [ID: {m.id}] ({m.type}) {m.content}")
                
            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata={"results": results}
            )
        except Exception as e:
            logger.error(f"Failed to search memory: {e}")
            return ToolResult(success=False, error=f"Database error: {str(e)}")

    def _search_via_backend(self, query: str, limit) -> ToolResult:
        # GET /api/memory matches the query as one substring of content or tags
        # and orders by trust; the in-process ranker also weighs recency and scope.
        params = {"search": query, "limit": limit, "status": "active", "sort": "trust"}
        try:
            body = request_json("GET", "/api/memory", params=params).body
        except BackendError as e:
            return ToolResult(success=False, error=f"Memory search failed: {e}")
        memories = (body or {}).get("memories") or []
        if not memories:
            return ToolResult(
                success=True,
                output=f"No memories found matching '{query}'.",
                metadata={"results": []},
            )
        output_lines = [f"Found {len(memories)} memories matching '{query}':"]
        output_lines += [f"- [ID: {m.get('id')}] ({m.get('type')}) {m.get('content')}" for m in memories]
        return ToolResult(success=True, output="\n".join(output_lines), metadata={"results": memories})


class DeleteMemoryTool(BaseTool):
    """Delete a memory by ID."""
    
    name = "delete_memory"
    description = "Delete a specific memory by its ID. Use this if a user tells you to forget something."
    is_dangerous = True
    requires_approval = True
    
    parameters = {
        "memory_id": ToolParameter(
            name="memory_id",
            type="string",
            description="The ID of the memory to delete (obtained from search_memory).",
            required=True
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        memory_id = kwargs.get("memory_id")
        
        try:
            memory = db.session.query(AgentMemory).filter_by(id=memory_id).first()
            if not memory:
                return ToolResult(
                    success=False,
                    error=f"Memory with ID '{memory_id}' not found."
                )
                
            content = memory.content
            before = memory.to_dict()
            db.session.delete(memory)
            db.session.add(AgentMemoryAudit(
                memory_id=memory_id,
                action="delete",
                actor="agent",
                before=before,
            ))
            db.session.commit()
            
            logger.info(f"Agent deleted memory {memory_id}")
            return ToolResult(
                success=True,
                output=f"Successfully deleted memory: '{content}'",
                metadata={"deleted_id": memory_id}
            )
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to delete memory: {e}")
            return ToolResult(success=False, error=f"Database error: {str(e)}")
