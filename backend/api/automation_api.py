#!/usr/bin/env python3
"""
Automation API
REST endpoints for managing browser, desktop, and MCP automation services.
"""

import asyncio
import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

automation_bp = Blueprint("automation", __name__, url_prefix="/api/automation")


def _run_async(coro):
    """Helper to run async code from sync Flask context"""
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=60)
    except RuntimeError:
        return asyncio.run(coro)


# ==================== Status Endpoints ====================

@automation_bp.route("/status", methods=["GET"])
def get_automation_status():
    """Get status of all automation services"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        from backend.services.desktop_automation_service import get_desktop_service
        from backend.services.mcp_client_service import get_mcp_service
        
        browser_state = get_browser_service().get_state()
        desktop_state = get_desktop_service().get_state()
        mcp_state = get_mcp_service().get_state()
        
        return jsonify({
            "success": True,
            "services": {
                "browser": browser_state,
                "desktop": desktop_state,
                "mcp": mcp_state
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting automation status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Browser Endpoints ====================

@automation_bp.route("/browser/status", methods=["GET"])
def get_browser_status():
    """Get browser automation service status"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        state = get_browser_service().get_state()
        return jsonify({"success": True, **state})
    except Exception as e:
        logger.error(f"Error getting browser status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/browser/start", methods=["POST"])
def start_browser():
    """Start the browser automation service"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        
        service = get_browser_service()
        result = _run_async(service._start_browser())
        
        if result:
            return jsonify({
                "success": True,
                "message": "Browser started",
                "state": service.get_state()
            })
        else:
            return jsonify({
                "success": False,
                "error": "Failed to start browser",
                "state": service.get_state()
            }), 500
            
    except Exception as e:
        logger.error(f"Error starting browser: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/browser/stop", methods=["POST"])
def stop_browser():
    """Stop the browser automation service"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        
        service = get_browser_service()
        _run_async(service.shutdown())
        
        return jsonify({
            "success": True,
            "message": "Browser stopped",
            "state": service.get_state()
        })
        
    except Exception as e:
        logger.error(f"Error stopping browser: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/browser/navigate", methods=["POST"])
def browser_navigate():
    """Navigate browser to a URL"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        
        data = request.get_json() or {}
        url = data.get("url")
        wait_for = data.get("wait_for", "load")
        
        if not url:
            return jsonify({"success": False, "error": "URL is required"}), 400
        
        service = get_browser_service()
        result = _run_async(service.navigate(url, wait_for=wait_for))
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error navigating browser: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/browser/screenshot", methods=["POST"])
def browser_screenshot():
    """Take a browser screenshot"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        
        data = request.get_json() or {}
        url = data.get("url")
        full_page = data.get("full_page", False)
        selector = data.get("selector")
        format = data.get("format", "png")
        
        if not url:
            return jsonify({"success": False, "error": "URL is required"}), 400
        
        service = get_browser_service()
        result = _run_async(service.screenshot(url, full_page=full_page, selector=selector, format=format))
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error taking screenshot: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Desktop Endpoints ====================

@automation_bp.route("/desktop/status", methods=["GET"])
def get_desktop_status():
    """Get desktop automation service status"""
    try:
        from backend.services.desktop_automation_service import get_desktop_service
        state = get_desktop_service().get_state()
        return jsonify({"success": True, **state})
    except Exception as e:
        logger.error(f"Error getting desktop status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/allowed-paths", methods=["GET"])
def get_allowed_paths():
    """Get allowed file operation paths"""
    try:
        from backend.services.desktop_automation_service import ALLOWED_PATHS
        return jsonify({
            "success": True,
            "allowed_paths": ALLOWED_PATHS
        })
    except Exception as e:
        logger.error(f"Error getting allowed paths: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/allowed-apps", methods=["GET"])
def get_allowed_apps():
    """Get allowed applications"""
    try:
        from backend.services.desktop_automation_service import ALLOWED_APPS
        return jsonify({
            "success": True,
            "allowed_apps": ALLOWED_APPS
        })
    except Exception as e:
        logger.error(f"Error getting allowed apps: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/audit-log", methods=["GET"])
def get_desktop_audit_log():
    """Get desktop automation audit log"""
    try:
        from backend.services.desktop_automation_service import get_desktop_service
        
        limit = request.args.get("limit", 100, type=int)
        log = get_desktop_service().get_audit_log(limit)
        
        return jsonify({
            "success": True,
            "entries": log,
            "count": len(log)
        })
        
    except Exception as e:
        logger.error(f"Error getting audit log: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/file-watchers", methods=["GET"])
def get_file_watchers():
    """Get active file watchers"""
    try:
        from backend.services.desktop_automation_service import get_desktop_service
        
        service = get_desktop_service()
        watchers = []
        
        for watch_id, watcher in service._file_watchers.items():
            watchers.append({
                "watch_id": watch_id,
                "path": watcher.path,
                "events": watcher.events,
                "created_at": watcher.created_at.isoformat(),
                "event_count": watcher.event_count
            })
        
        return jsonify({
            "success": True,
            "watchers": watchers,
            "count": len(watchers)
        })
        
    except Exception as e:
        logger.error(f"Error getting file watchers: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/notification", methods=["POST"])
def send_notification():
    """Send a desktop notification"""
    try:
        from backend.services.desktop_automation_service import get_desktop_service
        
        data = request.get_json() or {}
        title = data.get("title")
        message = data.get("message")
        timeout = data.get("timeout", 10)
        
        if not title or not message:
            return jsonify({"success": False, "error": "title and message are required"}), 400
        
        service = get_desktop_service()
        result = service.notification_send(title, message, timeout=timeout)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== MCP Endpoints ====================

@automation_bp.route("/mcp/status", methods=["GET"])
def get_mcp_status():
    """Get MCP client service status"""
    try:
        from backend.services.mcp_client_service import get_mcp_service
        state = get_mcp_service().get_state()
        return jsonify({"success": True, **state})
    except Exception as e:
        logger.error(f"Error getting MCP status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/mcp/servers", methods=["GET"])
def list_mcp_servers():
    """List configured MCP servers"""
    try:
        from backend.services.mcp_client_service import get_mcp_service
        result = get_mcp_service().list_configured_servers()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error listing MCP servers: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/mcp/connect", methods=["POST"])
def connect_mcp_server():
    """Connect to an MCP server"""
    try:
        from backend.services.mcp_client_service import get_mcp_service
        
        data = request.get_json() or {}
        server_name = data.get("server")
        
        if not server_name:
            return jsonify({"success": False, "error": "server name is required"}), 400
        
        service = get_mcp_service()
        result = _run_async(service.connect_server(server_name))
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error connecting to MCP server: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/mcp/disconnect", methods=["POST"])
def disconnect_mcp_server():
    """Disconnect from an MCP server"""
    try:
        from backend.services.mcp_client_service import get_mcp_service
        
        data = request.get_json() or {}
        server_name = data.get("server")
        
        if not server_name:
            return jsonify({"success": False, "error": "server name is required"}), 400
        
        service = get_mcp_service()
        result = _run_async(service.disconnect_server(server_name))
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error disconnecting from MCP server: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/mcp/tools", methods=["GET"])
def list_mcp_tools():
    """List tools from MCP servers"""
    try:
        from backend.services.mcp_client_service import get_mcp_service
        
        server = request.args.get("server")
        service = get_mcp_service()
        result = _run_async(service.list_tools(server))
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error listing MCP tools: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/mcp/execute", methods=["POST"])
def execute_mcp_tool():
    """Execute a tool on an MCP server"""
    try:
        from backend.services.mcp_client_service import get_mcp_service
        
        data = request.get_json() or {}
        server = data.get("server")
        tool = data.get("tool")
        arguments = data.get("arguments", {})
        
        if not server or not tool:
            return jsonify({"success": False, "error": "server and tool are required"}), 400
        
        service = get_mcp_service()
        result = _run_async(service.call_tool(server, tool, arguments))
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error executing MCP tool: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
