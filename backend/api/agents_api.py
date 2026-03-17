#!/usr/bin/env python3
"""
Agents API
Exposes agent configuration and execution endpoints.
"""

import logging
from flask import Blueprint, request, jsonify
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Create blueprint
agents_bp = Blueprint("agents", __name__, url_prefix="/api/agents")


def _get_config_manager():
    """Get the agent config manager"""
    from backend.services.agent_config import get_agent_config_manager

    return get_agent_config_manager()


def _get_tool_registry():
    """Get the tool registry"""
    try:
        from backend.tools import initialize_all_tools

        return initialize_all_tools()
    except Exception as e:
        logger.error(f"Failed to get tool registry: {e}")
        return None


@agents_bp.route("", methods=["GET"])
@agents_bp.route("/", methods=["GET"])
def list_agents():
    """
    List all configured agents.

    Response:
    {
        "success": true,
        "agents": [
            {
                "id": "content_creator",
                "name": "Content Creator",
                "description": "...",
                "tools": [...],
                "enabled": true
            }
        ]
    }
    """
    try:
        manager = _get_config_manager()
        agents = [agent.to_dict() for agent in manager.list_agents()]

        return jsonify({"success": True, "agents": agents, "count": len(agents)})

    except Exception as e:
        logger.error(f"Failed to list agents: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@agents_bp.route("/<agent_id>", methods=["GET"])
def get_agent(agent_id: str):
    """
    Get a specific agent's configuration.
    """
    try:
        manager = _get_config_manager()
        agent = manager.get_agent(agent_id)

        if not agent:
            return (
                jsonify({"success": False, "error": f"Agent '{agent_id}' not found"}),
                404,
            )

        # Get tool details
        tool_registry = _get_tool_registry()
        tools_detail = []
        if tool_registry:
            for tool_name in agent.tools:
                tool = tool_registry.get_tool(tool_name)
                if tool:
                    tools_detail.append(
                        {"name": tool.name, "description": tool.description}
                    )

        agent_dict = agent.to_dict()
        agent_dict["tools_detail"] = tools_detail

        return jsonify({"success": True, "agent": agent_dict})

    except Exception as e:
        logger.error(f"Failed to get agent: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@agents_bp.route("/<agent_id>", methods=["PATCH"])
def update_agent(agent_id: str):
    """
    Update an agent's configuration.

    Request:
    {
        "enabled": true,
        "max_iterations": 15,
        "system_prompt": "..."
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        manager = _get_config_manager()

        # Check agent exists
        if not manager.get_agent(agent_id):
            return (
                jsonify({"success": False, "error": f"Agent '{agent_id}' not found"}),
                404,
            )

        # Update agent
        success = manager.update_agent(agent_id, data)

        if success:
            return jsonify(
                {
                    "success": True,
                    "message": f"Agent '{agent_id}' updated",
                    "agent": manager.get_agent(agent_id).to_dict(),
                }
            )
        else:
            return jsonify({"success": False, "error": "Update failed"}), 500

    except Exception as e:
        logger.error(f"Failed to update agent: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@agents_bp.route("/<agent_id>/toggle", methods=["POST"])
def toggle_agent(agent_id: str):
    """
    Toggle an agent's enabled status.
    """
    try:
        manager = _get_config_manager()
        agent = manager.get_agent(agent_id)

        if not agent:
            return (
                jsonify({"success": False, "error": f"Agent '{agent_id}' not found"}),
                404,
            )

        new_status = not agent.enabled
        manager.set_agent_enabled(agent_id, new_status)

        return jsonify({"success": True, "agent_id": agent_id, "enabled": new_status})

    except Exception as e:
        logger.error(f"Failed to toggle agent: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@agents_bp.route("/match", methods=["POST"])
def match_agent():
    """
    Find the best agent for a given message.

    Request:
    {
        "message": "Generate 50 WordPress pages about SEO"
    }

    Response:
    {
        "success": true,
        "agent": {...},
        "matched_pattern": "wordpress"
    }
    """
    try:
        data = request.get_json()
        if not data or "message" not in data:
            return jsonify({"success": False, "error": "message is required"}), 400

        message = data["message"]
        manager = _get_config_manager()
        agent = manager.get_agent_for_message(message)

        if agent:
            return jsonify(
                {"success": True, "agent": agent.to_dict(), "agent_id": agent.id}
            )
        else:
            return jsonify(
                {"success": True, "agent": None, "message": "No matching agent found"}
            )

    except Exception as e:
        logger.error(f"Failed to match agent: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@agents_bp.route("/execute", methods=["POST"])
def execute_agent():
    """
    Execute an agent with a user query.

    Request:
    {
        "agent_id": "content_creator",
        "message": "Generate 10 WordPress pages about digital marketing",
        "context": {}
    }

    Response:
    {
        "success": true,
        "result": {...}
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        agent_id = data.get("agent_id")
        message = data.get("message")
        context = data.get("context", {})

        if not message:
            return jsonify({"success": False, "error": "message is required"}), 400

        manager = _get_config_manager()

        # Get agent (or match one if not specified)
        if agent_id:
            agent = manager.get_agent(agent_id)
            if not agent:
                return (
                    jsonify(
                        {"success": False, "error": f"Agent '{agent_id}' not found"}
                    ),
                    404,
                )
        else:
            agent = manager.get_agent_for_message(message)
            if not agent:
                return (
                    jsonify({"success": False, "error": "No matching agent found"}),
                    404,
                )

        if not agent.enabled:
            return (
                jsonify({"success": False, "error": f"Agent '{agent.id}' is disabled"}),
                400,
            )

        # Get tool registry
        tool_registry = _get_tool_registry()
        if not tool_registry:
            return (
                jsonify({"success": False, "error": "Tool registry not available"}),
                500,
            )

        # Filter tool registry to only agent's assigned tools
        from backend.services.agent_tools import ToolRegistry

        agent_tool_registry = ToolRegistry()
        for tool_name in agent.tools:
            tool = tool_registry.get_tool(tool_name)
            if tool:
                agent_tool_registry.register(tool)
            else:
                logger.warning(
                    f"Agent '{agent.id}' references tool '{tool_name}' which is not available"
                )

        if len(agent_tool_registry) == 0:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Agent '{agent.id}' has no available tools",
                    }
                ),
                400,
            )

        # Execute using agent executor
        try:
            from backend.services.agent_executor import AgentExecutor
            from backend.utils.llm_service import get_default_llm

            llm = get_default_llm()
            executor = AgentExecutor(
                agent_tool_registry, llm, max_iterations=agent.max_iterations
            )

            # Build session context with agent's system prompt
            session_context = f"""Agent: {agent.name}
System: {agent.system_prompt}

User Context: {str(context)}"""

            result = executor.execute(message, session_context=session_context)

            return jsonify(
                {
                    "success": True,
                    "agent_used": agent.id,
                    "result": {
                        "final_answer": result.final_answer,
                        "iterations": result.iterations,
                        "success": result.success,
                        "error": result.error,
                        "steps": [
                            {
                                "iteration": s.iteration,
                                "thoughts": s.thoughts,
                                "tool_calls": (
                                    s.tool_calls
                                    if isinstance(s.tool_calls, list)
                                    else (
                                        [
                                            (
                                                tc
                                                if isinstance(tc, dict)
                                                else (
                                                    tc.dict()
                                                    if hasattr(tc, "dict")
                                                    else str(tc)
                                                )
                                            )
                                            for tc in s.tool_calls
                                        ]
                                        if s.tool_calls
                                        else []
                                    )
                                ),
                                "observations": (
                                    s.observations if hasattr(s, "observations") else []
                                ),
                            }
                            for s in result.steps
                        ],
                    },
                }
            )

        except Exception as exec_error:
            logger.error(f"Agent execution failed: {exec_error}", exc_info=True)
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Agent execution failed: {str(exec_error)}",
                    }
                ),
                500,
            )

    except Exception as e:
        logger.error(f"Failed to execute agent: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@agents_bp.route("/settings", methods=["GET"])
def get_agent_settings():
    """
    Get global agent system settings.
    """
    try:
        # Read settings from various sources
        import os

        settings = {
            "agent_routing_enabled": os.getenv("AGENT_ROUTING_ENABLED", "false").lower()
            == "true",
            "default_max_iterations": 10,
            "fallback_to_chat": True,
            "log_agent_actions": os.getenv("LOG_AGENT_ACTIONS", "true").lower()
            == "true",
        }

        return jsonify({"success": True, "settings": settings})

    except Exception as e:
        logger.error(f"Failed to get settings: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@agents_bp.route("/settings", methods=["PATCH"])
def update_agent_settings():
    """
    Update global agent system settings.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        # For now, settings are stored in environment/memory
        # In production, these would be persisted to database
        import os

        if "agent_routing_enabled" in data:
            os.environ["AGENT_ROUTING_ENABLED"] = str(
                data["agent_routing_enabled"]
            ).lower()

        if "log_agent_actions" in data:
            os.environ["LOG_AGENT_ACTIONS"] = str(data["log_agent_actions"]).lower()

        return jsonify({"success": True, "message": "Settings updated"})

    except Exception as e:
        logger.error(f"Failed to update settings: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
