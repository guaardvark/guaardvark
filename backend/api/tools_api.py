#!/usr/bin/env python3
"""
Tools API
Exposes the tool registry for listing, inspecting, and executing tools.
This API enables the frontend ToolsPage and agent-based chat routing.
"""

import logging
import re
import os
import base64
import time
from flask import Blueprint, request, jsonify, current_app, send_from_directory, abort
from typing import Dict, Any, Optional
from backend.utils.path_guard import PathEscapesRoot, contained, contained_path
from backend.utils.screenshot_urls import screenshot_url

logger = logging.getLogger(__name__)

# Create blueprint
tools_bp = Blueprint("tools", __name__, url_prefix="/api/tools")

# Saved and shown when a routed result carries no answer text. The result
# itself is a Python dict and is never a reply.
_NO_REPLY_TEXT = "No reply came back for this request."


def _extract_and_save_screenshots(result):
    """Extract screenshot base64 data from agent result and save to files.
    Returns list of URL paths for the saved screenshots."""
    screenshot_urls = []
    try:
        steps = result.get("steps", [])
        for step in steps:
            observations = step.get("observations", [])
            for obs in observations:
                obs_result = obs.get("result", {})
                metadata = obs_result.get("metadata", {})
                image_b64 = metadata.get("image_base64")
                if not image_b64:
                    continue

                fmt = metadata.get("format", "png")
                screenshots_dir = os.path.join(
                    current_app.config.get("OUTPUT_DIR", "data/outputs"), "screenshots"
                )
                os.makedirs(screenshots_dir, exist_ok=True)

                filename = f"screenshot_{int(time.time() * 1000)}_{len(screenshot_urls)}.{fmt}"
                filepath = os.path.join(screenshots_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(image_b64))

                url = screenshot_url(filename)
                screenshot_urls.append(url)
                logger.info(f"Saved screenshot: {filepath} -> {url}")
    except Exception as e:
        logger.warning(f"Failed to extract screenshots: {e}")
    return screenshot_urls


def _tool_summary(tool) -> Dict[str, Any]:
    """The fields the Tools page shows for one tool.

    requires_approval is the gate chat honours with an approval card; the
    page shows it because POST /execute runs such a tool without that card.
    """
    return {
        "name": tool.name,
        "description": tool.description,
        "requires_approval": bool(getattr(tool, "requires_approval", False)),
        "parameters": {
            name: {
                "type": param.type,
                "required": param.required,
                "description": param.description,
                "default": param.default
            }
            for name, param in (tool.parameters or {}).items()
        },
    }


def _registry_categories() -> Dict[str, str]:
    """The category each tool was registered under (tool name -> category).

    Read without triggering registration, so a stand-in registry in tests
    sees an empty map and falls back to the name guess.
    """
    try:
        from backend.tools.tool_registry_init import _tool_categories
        return dict(_tool_categories)
    except (ImportError, AttributeError):
        return {}


def _guess_category(tool_name: str) -> str:
    """Fallback for a tool registered without a category."""
    if "wordpress" in tool_name or "content" in tool_name:
        return "content"
    if "csv" in tool_name or "file" in tool_name or "bulk" in tool_name:
        return "generation"
    if "code" in tool_name or "analyze" in tool_name:
        return "code"
    return "other"


def _get_tool_registry():
    """Get the tool registry from app or initialize"""
    # Try to get from app instance first
    if hasattr(current_app, 'tool_registry') and current_app.tool_registry:
        return current_app.tool_registry

    # Fallback: initialize directly
    try:
        from backend.tools import initialize_all_tools
        return initialize_all_tools()
    except Exception as e:
        logger.error(f"Failed to get tool registry: {e}")
        return None


@tools_bp.route("", methods=["GET"])
@tools_bp.route("/", methods=["GET"])
def list_tools():
    """
    List all registered tools with their schemas.

    Response:
    {
        "success": true,
        "tools": [
            {
                "name": "generate_wordpress_content",
                "description": "Generate WordPress CSV content...",
                "requires_approval": false,
                "parameters": {...}
            }
        ],
        "count": 7
    }
    """
    try:
        registry = _get_tool_registry()
        if not registry:
            return jsonify({
                "success": False,
                "error": "Tool registry not initialized"
            }), 500

        tools = []
        for tool_name in registry.list_tools():
            tool = registry.get_tool(tool_name)
            if tool:
                tools.append(_tool_summary(tool))

        return jsonify({
            "success": True,
            "tools": tools,
            "count": len(tools)
        })

    except Exception as e:
        logger.error(f"Failed to list tools: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@tools_bp.route("/<tool_name>", methods=["GET"])
def get_tool_schema(tool_name: str):
    """
    Get detailed schema for a specific tool.

    Response:
    {
        "success": true,
        "tool": {
            "name": "generate_wordpress_content",
            "description": "...",
            "parameters": {...},
            "xml_schema": "<tool>...</tool>",
            "json_schema": {...}
        }
    }
    """
    try:
        registry = _get_tool_registry()
        if not registry:
            return jsonify({
                "success": False,
                "error": "Tool registry not initialized"
            }), 500

        tool = registry.get_tool(tool_name)
        if not tool:
            return jsonify({
                "success": False,
                "error": f"Tool '{tool_name}' not found"
            }), 404

        return jsonify({
            "success": True,
            "tool": {
                **_tool_summary(tool),
                "xml_schema": tool.get_schema(),
                "json_schema": tool.get_json_schema()
            }
        })

    except Exception as e:
        logger.error(f"Failed to get tool schema: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@tools_bp.route("/execute", methods=["POST"])
def execute_tool():
    """
    Execute a tool with given parameters.

    Request:
    {
        "tool_name": "generate_wordpress_content",
        "parameters": {
            "client": "Acme Corp",
            "topic": "SEO Best Practices",
            "row_id": 1
        }
    }

    Response:
    {
        "success": true,
        "result": {
            "success": true,
            "output": "...",
            "metadata": {...}
        }
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "Request body required"
            }), 400

        tool_name = data.get("tool_name")
        parameters = data.get("parameters", {})

        if not tool_name:
            return jsonify({
                "success": False,
                "error": "tool_name is required"
            }), 400

        registry = _get_tool_registry()
        if not registry:
            return jsonify({
                "success": False,
                "error": "Tool registry not initialized"
            }), 500

        # Check if tool exists
        tool = registry.get_tool(tool_name)
        if not tool:
            return jsonify({
                "success": False,
                "error": f"Tool '{tool_name}' not found"
            }), 404

        # Validate required parameters
        missing_params = []
        for param_name, param in tool.parameters.items():
            if param.required and param_name not in parameters:
                missing_params.append(param_name)

        if missing_params:
            return jsonify({
                "success": False,
                "error": f"Missing required parameters: {', '.join(missing_params)}"
            }), 400

        # Execute tool
        logger.info(f"Executing tool '{tool_name}' with parameters: {list(parameters.keys())}")
        # A person invoking a tool directly (Tools page / API, behind auth_guard)
        # is the approval, so confirmation-gated tools run without a prompt.
        from backend.services.tool_confirmation import trusted_caller
        from backend.utils.backend_http import CALLER_TRANSPORT_FIELD, calls_for_mcp_client

        # The MCP server forwards some tools here (run_tool_in_backend); they
        # keep the input rules they have for MCP clients.
        for_mcp = data.get(CALLER_TRANSPORT_FIELD) == "mcp"
        with trusted_caller("rest:tools_api"), calls_for_mcp_client(for_mcp):
            result = registry.execute_tool(tool_name, **parameters)

        return jsonify({
            "success": True,
            "result": result.to_dict()
        })

    except Exception as e:
        logger.error(f"Failed to execute tool: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@tools_bp.route("/jobs/<job_id>", methods=["GET"])
def tool_job_status(job_id: str):
    """A tool job that an MCP call started (backend/services/tool_jobs.py).

    ``?wait_s=N`` holds the answer until the job finishes or N seconds pass,
    capped at tool_jobs.MAX_WAIT_S. An unknown id answers 404 with ``reason``
    ``restarted`` (the job belonged to an earlier run of the backend) or
    ``unknown``.
    """
    from backend.services import tool_jobs

    try:
        wait_s = float(request.args.get("wait_s") or 0)
    except ValueError:
        wait_s = 0.0
    snapshot = tool_jobs.wait(job_id, wait_s) if wait_s > 0 else tool_jobs.get(job_id)
    if snapshot is None:
        reason, message = tool_jobs.missing(job_id)
        return jsonify({"success": False, "error": message, "reason": reason, "job_id": job_id}), 404
    return jsonify({"success": True, "data": snapshot})


@tools_bp.route("/schemas", methods=["GET"])
def get_all_schemas():
    """
    Get all tool schemas in a specific format for LLM prompts.

    Query params:
    - format: 'xml' or 'json' (default: 'xml')

    Response:
    {
        "success": true,
        "format": "xml",
        "schemas": "<tool name='...'>....</tool>..."
    }
    """
    try:
        format_type = request.args.get("format", "xml")
        if format_type not in ["xml", "json"]:
            return jsonify({
                "success": False,
                "error": "Format must be 'xml' or 'json'"
            }), 400

        registry = _get_tool_registry()
        if not registry:
            return jsonify({
                "success": False,
                "error": "Tool registry not initialized"
            }), 500

        schemas = registry.get_tool_schemas(format=format_type)

        return jsonify({
            "success": True,
            "format": format_type,
            "schemas": schemas,
            "tool_count": len(registry)
        })

    except Exception as e:
        logger.error(f"Failed to get schemas: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@tools_bp.route("/route", methods=["POST"])
def route_message():
    """
    Route a message to determine appropriate tool/handling.
    Used by ChatPage to decide how to process user input.

    Request:
    {
        "message": "Generate 50 WordPress pages about SEO",
        "context": {
            "client": "Acme Corp",
            "project_id": 1
        }
    }

    Response:
    {
        "success": true,
        "route": {
            "route_type": "tool_direct",
            "tool_name": "generate_bulk_csv",
            "confidence": 0.8,
            "reasoning": "Matched pattern: bulk_csv"
        }
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "Request body required"
            }), 400

        message = data.get("message", "")
        context = data.get("context", {})

        if not message:
            return jsonify({
                "success": False,
                "error": "message is required"
            }), 400

        # Prefer AgentBrain (sole canonical per PHASE2 + built arch) for awareness (memory/lessons/facts/budget/STA).
        # Fall back to legacy agent_router only as bridge for /tools/route compat (will be removed).
        # See: agent_brain.py:AgentBrain.process, brain_state.py, memory_contract, useAgentRouter hook.
        route = None
        try:
            from backend.services.brain_state import BrainState
            from backend.services.agent_brain import AgentBrain
            bs = BrainState.get_instance() if hasattr(BrainState, 'get_instance') else None
            if bs and getattr(bs, 'is_ready', False):
                # Thin decision preview leveraging brain logic (screen flags, vision patterns, tiers) without full execution cost.
                # Full routing + awareness happens in unified_chat_api → brain.process (with budget/memory/lessons).
                brain = AgentBrain(state=bs)
                screen_active = bool(context.get("agent_screen_active") or context.get("session_mode") == "agent")
                # Reuse brain's classification where possible (avoids duplicating hard regex here).
                is_vision = getattr(brain, '_is_vision_task', lambda m, i=None: False)(message, None) if hasattr(brain, '_is_vision_task') else False
                # Whole words only: "Agents" (a workspace) and "screenshot" are
                # not requests to drive the desktop. Substring matching sent a
                # plain file request to the screen-agent loop (2026-09-05).
                asks_for_agent = bool(re.search(r"\b(?:agent|screen)\b", message, re.I))
                if screen_active or is_vision or asks_for_agent:
                    # execute_via "unified": this preview is a hint for unified
                    # chat (AgentBrain), not a decision /route-and-execute can
                    # act on. That endpoint routes again through the legacy
                    # router, which treats "what is an agent?" as plain chat.
                    route = type('obj', (object,), {
                        'route_type': type('rt', (object,), {'value': 'agent_loop'})(),
                        'tool_name': 'agent_task_execute' if 'execute' in message.lower() or 'do' in message.lower() else None,
                        'tool_params': {},
                        'confidence': 0.75,
                        'reasoning': 'Routed via AgentBrain (screen_active or vision/STA path; lean on memory/lessons/budget)',
                        'suggested_mode': 'agent',
                        'execute_via': 'unified',
                    })()
        except Exception:
            pass  # fallthrough to legacy bridge

        if route is None:
            from backend.services.agent_router import (
                RouteType,
                is_explicit_agent_request,
                route_message as do_route,
            )
            decision = do_route(message, context)
            # Only an explicit agent request keeps the legacy loop; an
            # agent-loop match on an ordinary question goes to unified chat.
            execute_via = None
            if (decision.route_type == RouteType.AGENT_LOOP
                    and not is_explicit_agent_request(message, decision)):
                execute_via = 'unified'
            route = type('obj', (object,), {
                'route_type': type('rt', (object,), {'value': decision.route_type.value})(),
                'tool_name': decision.tool_name,
                'tool_params': decision.tool_params,
                'confidence': decision.confidence,
                'reasoning': decision.reasoning + ' (legacy bridge; migrate to AgentBrain)',
                'suggested_mode': getattr(decision, 'suggested_mode', None),
                'execute_via': execute_via,
            })()

        return jsonify({
            "success": True,
            "route": {
                "route_type": route.route_type.value,
                "tool_name": route.tool_name,
                "tool_params": route.tool_params,
                "confidence": route.confidence,
                "reasoning": route.reasoning,
                "suggested_mode": route.suggested_mode,
                "execute_via": route.execute_via,
            }
        })

    except Exception as e:
        logger.error(f"Failed to route message: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@tools_bp.route("/route-and-execute", methods=["POST"])
def route_and_execute():
    """
    Route a message and execute the appropriate action.
    One-shot endpoint for intelligent message handling.

    Request:
    {
        "message": "Generate 50 WordPress pages about SEO",
        "context": {
            "client": "Acme Corp",
            "project_id": 1
        }
    }

    Response varies based on route type.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "Request body required"
            }), 400

        message = data.get("message", "")
        context = data.get("context", {})

        if not message:
            return jsonify({
                "success": False,
                "error": "message is required"
            }), 400

        from backend.services.plan_mode import plan_mode_refusal
        planning = plan_mode_refusal((context or {}).get("session_id"), None)
        if planning:
            return jsonify({"success": False, "error": planning, "plan_mode": True}), 409

        # Bridge to legacy for compat. Prefer full AgentBrain/unified_chat paths (they carry budget/memory/lessons/STA).
        # See approved plan: route to brain.process for awareness instead of direct execute_routed.
        import warnings
        warnings.warn("/tools/route-and-execute uses legacy agent_router bridge; use unifiedChatService + AgentBrain for memory/lessons/STA-aware execution", DeprecationWarning, stacklevel=2)
        from backend.services.agent_router import execute_routed_message
        result = execute_routed_message(message, context)

        # Nothing to run: the legacy router wants a model reply. Hand the turn
        # back so the client sends it through unified chat, which saves both
        # sides of the exchange itself; saving here would put the routing
        # dict in the history as the answer.
        if result.get("type") == "chat" and result.get("requires_llm"):
            return jsonify({
                "success": True,
                "fallback_to_chat": True,
                "result": result,
            })

        # Extract screenshots from agent result and save to files
        screenshot_urls = _extract_and_save_screenshots(result)
        if screenshot_urls:
            result["screenshot_urls"] = screenshot_urls

        # Build display content: final answer + screenshot markdown
        final_answer = result.get("final_answer", "") or result.get("response", "") or ""
        display_content = final_answer
        for url in screenshot_urls:
            display_content += f"\n\n![Screenshot]({url})"
        if not display_content.strip():
            display_content = _NO_REPLY_TEXT

        # Save messages to DB for chat history persistence
        session_id = context.get("session_id")
        if session_id:
            try:
                from backend.models import db, LLMMessage, LLMSession
                from datetime import datetime

                # Ensure session exists
                session = db.session.get(LLMSession, session_id)
                if not session:
                    session = LLMSession(id=session_id, user="default")
                    db.session.add(session)

                # Save user message
                user_msg = LLMMessage(
                    session_id=session_id, role="user",
                    content=message, timestamp=datetime.now()
                )
                db.session.add(user_msg)

                # Save assistant response with screenshot markdown. An agent
                # loop's facts check rides along so the note survives reload.
                verified = result.get("verified") if result.get("type") == "agent_result" else None
                assistant_msg = LLMMessage(
                    session_id=session_id, role="assistant",
                    content=display_content, timestamp=datetime.now(),
                    extra_data={"verified": verified} if verified is not None else None,
                )
                db.session.add(assistant_msg)

                db.session.commit()
                logger.info(f"Persisted agent messages for session {session_id}")
            except Exception as e:
                logger.warning(f"Failed to persist agent messages: {e}")
                try:
                    db.session.rollback()
                except Exception:
                    pass

        # Propagate error status from agent/tool execution
        is_error = result.get("type") == "error"
        if not is_error and result.get("type") == "agent_result" and result.get("success") is False:
            is_error = True
        agent_error = result.get("error") if result.get("type") == "agent_result" else None
        # display_content is what was just persisted as the assistant turn;
        # the client shows it first so a tool_result or file_generation shape
        # (no final_answer field) is not reported as "no response".
        return jsonify({
            "success": not is_error,
            "result": result,
            "display_content": display_content,
            **({"error": result.get("error") or agent_error} if is_error else {})
        }), 500 if is_error else 200

    except Exception as e:
        logger.error(f"Failed to route and execute: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@tools_bp.route("/screenshots/<path:filename>", methods=["GET"])
def serve_screenshot(filename):
    """Serve saved screenshot images inline (no download dialog)."""
    screenshots_dir = os.path.join(
        current_app.config.get("OUTPUT_DIR", "data/outputs"), "screenshots"
    )
    try:
        safe_path = contained_path(screenshots_dir, filename)
    except PathEscapesRoot:
        abort(403)
    if not os.path.isfile(safe_path):
        abort(404)
    return send_from_directory(screenshots_dir, filename)


@tools_bp.route("/categories", methods=["GET"])
def get_tool_categories():
    """
    Get tools organized by the category each was registered under
    (backend/tools/tool_registry_init.py), e.g. "media" or "outreach".

    Response:
    {
        "success": true,
        "categories": {
            "content": ["generate_wordpress_content", ...],
            "generation": ["generate_bulk_csv", ...],
            "code": ["codegen", ...],
            "media": ["media_play", ...]
        }
    }
    """
    try:
        registry = _get_tool_registry()
        if not registry:
            return jsonify({
                "success": False,
                "error": "Tool registry not initialized"
            }), 500

        # The category each tool was registered under; a name guess only for
        # a tool that registered without one.
        registered = _registry_categories()
        categories: Dict[str, list] = {}
        for tool_name in registry.list_tools():
            category = registered.get(tool_name) or _guess_category(tool_name)
            categories.setdefault(category, []).append(tool_name)

        return jsonify({
            "success": True,
            "categories": categories
        })

    except Exception as e:
        logger.error(f"Failed to get tool categories: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
