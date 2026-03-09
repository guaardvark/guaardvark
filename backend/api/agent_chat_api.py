#!/usr/bin/env python3
"""
Agent Chat API
Provides agent-powered chat capabilities with tool calling
"""

import logging
from flask import Blueprint, request, jsonify, current_app
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Create blueprint
agent_chat_bp = Blueprint("agent_chat", __name__, url_prefix="/api/agent")

# Global registry and executor (lazy initialization)
_tool_registry = None
_agent_executor = None


def _initialize_agent_system():
    """Initialize agent system on first use"""
    global _tool_registry, _agent_executor

    if _tool_registry is not None and _agent_executor is not None:
        return True

    try:
        from backend.tools.tool_registry_init import initialize_all_tools
        from backend.services.agent_executor import AgentExecutor

        # Get LLM
        llm = current_app.config.get("LLAMA_INDEX_LLM")
        if not llm:
            logger.error("LLM not configured")
            return False

        # Use global tool registry with ALL tools (browser, desktop, MCP, etc.)
        _tool_registry = initialize_all_tools()

        logger.info(f"Registered {len(_tool_registry)} tools for agent")

        # Initialize executor
        _agent_executor = AgentExecutor(_tool_registry, llm, max_iterations=10)
        logger.info("Agent system initialized successfully")

        return True

    except Exception as e:
        logger.error(f"Failed to initialize agent system: {e}", exc_info=True)
        return False


@agent_chat_bp.route("/chat", methods=["POST"])
def agent_chat():
    """
    Agent-powered chat endpoint with tool calling
    
    Request:
    {
        "message": "Read app.py and tell me about the blueprint registration",
        "session_id": "sess_123",
        "max_iterations": 10,  # optional
        "context": "additional context"  # optional
    }
    
    Response:
    {
        "success": true,
        "final_answer": "The answer...",
        "steps": [...],  # Agent execution steps
        "iterations": 3,
        "tool_calls": 5
    }
    """
    try:
        # Validate request
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        
        data = request.get_json()
        message = data.get('message')
        session_id = data.get('session_id')
        max_iterations = data.get('max_iterations', 10)
        context = data.get('context', '')
        
        if not message:
            return jsonify({"error": "Missing 'message' parameter"}), 400
        
        if not session_id:
            return jsonify({"error": "Missing 'session_id' parameter"}), 400
        
        # Initialize agent system
        if not _initialize_agent_system():
            return jsonify({"error": "Agent system initialization failed"}), 500
        
        # Use system coordinator if available
        try:
            from backend.utils.system_coordinator import get_system_coordinator, ProcessType
            coordinator = get_system_coordinator()
            
            with coordinator.managed_operation("agent_chat", ProcessType.CHAT_SESSION) as process_id:
                # Validate security
                if not coordinator.validate_security("llm_prompt", prompt=message):
                    return jsonify({"error": "Security validation failed"}), 403
                
                # Execute agent
                result = _agent_executor.execute(
                    user_query=message,
                    session_context=context,
                    process_id=process_id
                )
                
        except ImportError:
            # Run without coordinator
            logger.warning("System coordinator not available, running without it")
            result = _agent_executor.execute(
                user_query=message,
                session_context=context
            )
        
        # Format response
        if not result.success:
            return jsonify({
                "success": False,
                "error": result.error,
                "steps": [step.__dict__ for step in result.steps],
                "iterations": result.iterations
            }), 500
        
        # Count total tool calls
        total_tool_calls = sum(len(step.tool_calls) for step in result.steps)
        
        return jsonify({
            "success": True,
            "final_answer": result.final_answer,
            "steps": [
                {
                    'iteration': step.iteration,
                    'thoughts': step.thoughts,
                    'tool_calls': step.tool_calls,
                    'observations': step.observations,
                    'timestamp': step.timestamp
                }
                for step in result.steps
            ],
            "iterations": result.iterations,
            "tool_calls": total_tool_calls,
            "session_id": session_id
        }), 200
        
    except Exception as e:
        logger.error(f"Agent chat error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@agent_chat_bp.route("/tools", methods=["GET"])
def list_tools():
    """
    List available tools
    
    Response:
    {
        "tools": [
            {
                "name": "execute_python",
                "description": "...",
                "parameters": {...}
            },
            ...
        ]
    }
    """
    try:
        # Initialize agent system
        if not _initialize_agent_system():
            return jsonify({"error": "Agent system initialization failed"}), 500
        
        # Get tool information
        tools = []
        for tool_name in _tool_registry.list_tools():
            tool = _tool_registry.get_tool(tool_name)
            tools.append(tool.get_json_schema())
        
        return jsonify({
            "tools": tools,
            "count": len(tools)
        }), 200
        
    except Exception as e:
        logger.error(f"Error listing tools: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@agent_chat_bp.route("/health", methods=["GET"])
def agent_health():
    """Health check for agent system"""
    try:
        # Try to initialize
        initialized = _initialize_agent_system()
        
        if initialized:
            return jsonify({
                "status": "healthy",
                "tools_registered": len(_tool_registry) if _tool_registry else 0,
                "agent_ready": _agent_executor is not None
            }), 200
        else:
            return jsonify({
                "status": "unhealthy",
                "error": "Agent system failed to initialize"
            }), 503
            
    except Exception as e:
        logger.error(f"Agent health check failed: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

