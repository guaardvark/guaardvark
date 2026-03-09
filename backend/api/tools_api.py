#!/usr/bin/env python3
"""
Tools API
Exposes the tool registry for listing, inspecting, and executing tools.
This API enables the frontend ToolsPage and agent-based chat routing.
"""

import logging
import os
import base64
import time
from flask import Blueprint, request, jsonify, current_app, send_from_directory, abort
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Create blueprint
tools_bp = Blueprint("tools", __name__, url_prefix="/api/tools")


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

                url = f"/api/tools/screenshots/{filename}"
                screenshot_urls.append(url)
                logger.info(f"Saved screenshot: {filepath} -> {url}")
    except Exception as e:
        logger.warning(f"Failed to extract screenshots: {e}")
    return screenshot_urls


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
                tools.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        name: {
                            "type": param.type,
                            "required": param.required,
                            "description": param.description,
                            "default": param.default
                        }
                        for name, param in tool.parameters.items()
                    }
                })

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
                "name": tool.name,
                "description": tool.description,
                "parameters": {
                    name: {
                        "type": param.type,
                        "required": param.required,
                        "description": param.description,
                        "default": param.default
                    }
                    for name, param in tool.parameters.items()
                },
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

        from backend.services.agent_router import route_message as do_route
        decision = do_route(message, context)

        return jsonify({
            "success": True,
            "route": {
                "route_type": decision.route_type.value,
                "tool_name": decision.tool_name,
                "tool_params": decision.tool_params,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "suggested_mode": decision.suggested_mode
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

        from backend.services.agent_router import execute_routed_message
        result = execute_routed_message(message, context)

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
            display_content = str(result)

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

                # Save assistant response with screenshot markdown
                assistant_msg = LLMMessage(
                    session_id=session_id, role="assistant",
                    content=display_content, timestamp=datetime.now()
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
        return jsonify({
            "success": not is_error,
            "result": result,
            **({"error": result.get("error")} if is_error else {})
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
    safe_path = os.path.normpath(os.path.abspath(os.path.join(screenshots_dir, filename)))
    if not safe_path.startswith(os.path.abspath(screenshots_dir) + os.sep):
        abort(403)
    if not os.path.isfile(safe_path):
        abort(404)
    return send_from_directory(screenshots_dir, filename)


@tools_bp.route("/categories", methods=["GET"])
def get_tool_categories():
    """
    Get tools organized by category.

    Response:
    {
        "success": true,
        "categories": {
            "content": ["generate_wordpress_content", ...],
            "generation": ["generate_bulk_csv", ...],
            "code": ["codegen", ...]
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

        # Categorize tools based on naming patterns
        categories = {
            "content": [],
            "generation": [],
            "code": [],
            "other": []
        }

        for tool_name in registry.list_tools():
            if "wordpress" in tool_name or "content" in tool_name:
                categories["content"].append(tool_name)
            elif "csv" in tool_name or "file" in tool_name or "bulk" in tool_name:
                categories["generation"].append(tool_name)
            elif "code" in tool_name or "analyze" in tool_name:
                categories["code"].append(tool_name)
            else:
                categories["other"].append(tool_name)

        # Remove empty categories
        categories = {k: v for k, v in categories.items() if v}

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
