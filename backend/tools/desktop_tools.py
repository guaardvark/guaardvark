#!/usr/bin/env python3

import logging
from typing import Any, Dict, List, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.desktop_automation_service import (
    get_desktop_service,
    DESKTOP_AUTOMATION_ENABLED,
    GUI_AUTOMATION_ENABLED,
)

logger = logging.getLogger(__name__)


class FileWatchTool(BaseTool):

    name = "file_watch"
    description = "Watch a file or directory for changes (created, modified, deleted, moved). Returns a watch_id to stop watching later."
    parameters = {
        "path": ToolParameter(
            name="path",
            type="string",
            required=True,
            description="Path to file or directory to watch",
        ),
        "events": ToolParameter(
            name="events",
            type="list",
            required=False,
            description="Events to watch: 'created', 'modified', 'deleted', 'moved'. Default: all",
            default=None,
        ),
        "action": ToolParameter(
            name="action",
            type="string",
            required=False,
            description="Action: 'start' to begin watching, 'stop' to stop (requires watch_id)",
            default="start",
        ),
        "watch_id": ToolParameter(
            name="watch_id",
            type="string",
            required=False,
            description="Watch ID to stop (required when action='stop')",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not DESKTOP_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Desktop automation disabled. Set GUAARDVARK_DESKTOP_AUTOMATION=true",
            )

        action = kwargs.get("action", "start")

        if action == "stop":
            watch_id = kwargs.get("watch_id")
            if not watch_id:
                return ToolResult(
                    success=False, error="watch_id required to stop watching"
                )

            service = get_desktop_service()
            result = service.file_watch_stop(watch_id)

            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Stopped watching (events captured: {result.get('event_count', 0)})",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        path = kwargs.get("path")
        events = kwargs.get("events")

        if not path:
            return ToolResult(success=False, error="Path is required")

        try:
            service = get_desktop_service()
            result = service.file_watch_start(path, events=events)

            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Now watching '{result.get('path')}' for events: {result.get('events')}. Watch ID: {result.get('watch_id')}",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"File watch error: {e}")
            return ToolResult(success=False, error=str(e))


class FileBulkOperationTool(BaseTool):

    name = "file_bulk_operation"
    description = "Perform bulk file operations using glob patterns. Supports copy, move, and delete."
    parameters = {
        "operation": ToolParameter(
            name="operation",
            type="string",
            required=True,
            description="Operation: 'copy', 'move', or 'delete'",
        ),
        "source_patterns": ToolParameter(
            name="source_patterns",
            type="list",
            required=True,
            description="List of glob patterns for source files (e.g., ['~/Documents/*.pdf'])",
        ),
        "destination": ToolParameter(
            name="destination",
            type="string",
            required=False,
            description="Destination directory (required for copy/move)",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not DESKTOP_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Desktop automation disabled. Set GUAARDVARK_DESKTOP_AUTOMATION=true",
            )

        operation = kwargs.get("operation")
        source_patterns = kwargs.get("source_patterns")
        destination = kwargs.get("destination")

        if not operation or not source_patterns:
            return ToolResult(
                success=False, error="operation and source_patterns are required"
            )

        try:
            service = get_desktop_service()
            result = service.file_bulk_operation(
                operation, source_patterns, destination
            )

            if result.get("success"):
                msg = (
                    f"Bulk {operation}: {result.get('total_processed')} files processed"
                )
                if result.get("total_failed") > 0:
                    msg += f", {result.get('total_failed')} failed"

                return ToolResult(success=True, output=msg, metadata=result)
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"Bulk operation error: {e}")
            return ToolResult(success=False, error=str(e))


class AppLaunchTool(BaseTool):

    name = "app_launch"
    description = (
        "Launch an application by name. Only whitelisted apps are allowed for security."
    )
    parameters = {
        "app_name": ToolParameter(
            name="app_name",
            type="string",
            required=True,
            description="Application name (e.g., 'firefox', 'code', 'gnome-terminal')",
        ),
        "args": ToolParameter(
            name="args",
            type="list",
            required=False,
            description="Command line arguments",
        ),
        "wait": ToolParameter(
            name="wait",
            type="bool",
            required=False,
            description="Wait for application to complete (default: false)",
            default=False,
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not DESKTOP_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Desktop automation disabled. Set GUAARDVARK_DESKTOP_AUTOMATION=true",
            )

        app_name = kwargs.get("app_name")
        args = kwargs.get("args")
        wait = kwargs.get("wait", False)

        if not app_name:
            return ToolResult(success=False, error="app_name is required")

        try:
            service = get_desktop_service()
            result = service.app_launch(app_name, args=args, wait=wait)

            if result.get("success"):
                if wait:
                    output = f"App '{app_name}' completed with exit code {result.get('exit_code')}"
                else:
                    output = f"Launched '{app_name}' (PID: {result.get('pid')})"

                return ToolResult(success=True, output=output, metadata=result)
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"App launch error: {e}")
            return ToolResult(success=False, error=str(e))


class AppListTool(BaseTool):

    name = "app_list"
    description = "List running applications/processes with optional filtering."
    parameters = {
        "filter": ToolParameter(
            name="filter",
            type="string",
            required=False,
            description="Filter pattern (e.g., 'firefox*', '*chrome*')",
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        if not DESKTOP_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Desktop automation disabled. Set GUAARDVARK_DESKTOP_AUTOMATION=true",
            )

        filter_pattern = kwargs.get("filter")

        try:
            service = get_desktop_service()
            result = service.app_list(filter_pattern)

            if result.get("success"):
                processes = result.get("processes", [])
                output = f"Found {result.get('total', 0)} processes"
                if filter_pattern:
                    output += f" matching '{filter_pattern}'"

                return ToolResult(success=True, output=output, metadata=result)
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"App list error: {e}")
            return ToolResult(success=False, error=str(e))


class AppFocusTool(BaseTool):

    name = "app_focus"
    description = "Bring a window to the foreground by title or process name."
    parameters = {
        "window_title": ToolParameter(
            name="window_title",
            type="string",
            required=False,
            description="Window title pattern to focus",
        ),
        "process_name": ToolParameter(
            name="process_name",
            type="string",
            required=False,
            description="Process name pattern to focus",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not GUI_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="GUI automation disabled. Set GUAARDVARK_GUI_AUTOMATION=true",
            )

        window_title = kwargs.get("window_title")
        process_name = kwargs.get("process_name")

        if not window_title and not process_name:
            return ToolResult(
                success=False, error="window_title or process_name required"
            )

        try:
            service = get_desktop_service()
            result = service.app_focus(
                window_title=window_title, process_name=process_name
            )

            if result.get("success"):
                pattern = window_title or process_name
                return ToolResult(
                    success=True,
                    output=f"Focused window matching '{pattern}'",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"App focus error: {e}")
            return ToolResult(success=False, error=str(e))


class GUIClickTool(BaseTool):

    name = "gui_click"
    description = "Click the mouse at specific screen coordinates. Requires GUI automation to be enabled."
    parameters = {
        "x": ToolParameter(
            name="x", type="int", required=True, description="X coordinate on screen"
        ),
        "y": ToolParameter(
            name="y", type="int", required=True, description="Y coordinate on screen"
        ),
        "button": ToolParameter(
            name="button",
            type="string",
            required=False,
            description="Mouse button: 'left', 'right', 'middle'",
            default="left",
        ),
        "clicks": ToolParameter(
            name="clicks",
            type="int",
            required=False,
            description="Number of clicks (default: 1)",
            default=1,
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not GUI_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="GUI automation disabled. Set GUAARDVARK_GUI_AUTOMATION=true",
            )

        x = kwargs.get("x")
        y = kwargs.get("y")
        button = kwargs.get("button", "left")
        clicks = kwargs.get("clicks", 1)

        if x is None or y is None:
            return ToolResult(success=False, error="x and y coordinates required")

        try:
            service = get_desktop_service()
            result = service.gui_click(x, y, button=button, clicks=clicks)

            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Clicked {button} button at ({x}, {y})",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"GUI click error: {e}")
            return ToolResult(success=False, error=str(e))


class GUITypeTool(BaseTool):

    name = "gui_type"
    description = "Type text using the keyboard. Requires GUI automation to be enabled."
    parameters = {
        "text": ToolParameter(
            name="text", type="string", required=True, description="Text to type"
        ),
        "interval": ToolParameter(
            name="interval",
            type="float",
            required=False,
            description="Interval between keystrokes in seconds",
            default=0.05,
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not GUI_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="GUI automation disabled. Set GUAARDVARK_GUI_AUTOMATION=true",
            )

        text = kwargs.get("text")
        interval = kwargs.get("interval", 0.05)

        if not text:
            return ToolResult(success=False, error="text is required")

        try:
            service = get_desktop_service()
            result = service.gui_type(text, interval=interval)

            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Typed {result.get('text_length')} characters",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"GUI type error: {e}")
            return ToolResult(success=False, error=str(e))


class GUIHotkeyTool(BaseTool):

    name = "gui_hotkey"
    description = "Press a keyboard shortcut (e.g., Ctrl+C). Requires GUI automation to be enabled."
    parameters = {
        "keys": ToolParameter(
            name="keys",
            type="list",
            required=True,
            description="Keys to press (e.g., ['ctrl', 'c'] or ['alt', 'tab'])",
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        if not GUI_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="GUI automation disabled. Set GUAARDVARK_GUI_AUTOMATION=true",
            )

        keys = kwargs.get("keys")

        if not keys:
            return ToolResult(success=False, error="keys list is required")

        try:
            service = get_desktop_service()
            result = service.gui_hotkey(*keys)

            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Pressed hotkey: {'+'.join(keys)}",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"GUI hotkey error: {e}")
            return ToolResult(success=False, error=str(e))


class GUIScreenshotTool(BaseTool):

    name = "gui_screenshot"
    description = "Capture a screenshot of the screen or a specific region. Returns base64-encoded image."
    parameters = {
        "region": ToolParameter(
            name="region",
            type="list",
            required=False,
            description="Optional region as [x, y, width, height]",
        ),
        "format": ToolParameter(
            name="format",
            type="string",
            required=False,
            description="Image format: 'png' or 'jpeg'",
            default="png",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not GUI_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="GUI automation disabled. Set GUAARDVARK_GUI_AUTOMATION=true",
            )

        region = kwargs.get("region")
        format = kwargs.get("format", "png")

        if region and len(region) != 4:
            return ToolResult(
                success=False, error="region must be [x, y, width, height]"
            )

        try:
            service = get_desktop_service()
            region_tuple = tuple(region) if region else None
            result = service.gui_screenshot(region=region_tuple, format=format)

            if result.get("success"):
                size = result.get("size", {})
                return ToolResult(
                    success=True,
                    output=f"Screenshot captured ({size.get('width')}x{size.get('height')}, {format})",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"GUI screenshot error: {e}")
            return ToolResult(success=False, error=str(e))


class GUILocateImageTool(BaseTool):

    name = "gui_locate_image"
    description = (
        "Find the location of an image on the screen. Useful for GUI automation."
    )
    parameters = {
        "image_path": ToolParameter(
            name="image_path",
            type="string",
            required=True,
            description="Path to the image file to locate",
        ),
        "confidence": ToolParameter(
            name="confidence",
            type="float",
            required=False,
            description="Match confidence threshold (0-1)",
            default=0.9,
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not GUI_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="GUI automation disabled. Set GUAARDVARK_GUI_AUTOMATION=true",
            )

        image_path = kwargs.get("image_path")
        confidence = kwargs.get("confidence", 0.9)

        if not image_path:
            return ToolResult(success=False, error="image_path is required")

        try:
            service = get_desktop_service()
            result = service.gui_locate_image(image_path, confidence=confidence)

            if result.get("success"):
                if result.get("found"):
                    center = result.get("center", {})
                    return ToolResult(
                        success=True,
                        output=f"Image found at center ({center.get('x')}, {center.get('y')})",
                        metadata=result,
                    )
                else:
                    return ToolResult(
                        success=True,
                        output="Image not found on screen",
                        metadata=result,
                    )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"GUI locate image error: {e}")
            return ToolResult(success=False, error=str(e))


class ClipboardGetTool(BaseTool):

    name = "clipboard_get"
    description = "Get the current text contents of the system clipboard."
    parameters = {}

    def execute(self, **kwargs) -> ToolResult:
        if not DESKTOP_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Desktop automation disabled. Set GUAARDVARK_DESKTOP_AUTOMATION=true",
            )

        try:
            service = get_desktop_service()
            result = service.clipboard_get()

            if result.get("success"):
                content = result.get("content", "")
                preview = content[:200] + "..." if len(content) > 200 else content
                return ToolResult(
                    success=True,
                    output=f"Clipboard ({result.get('length')} chars): {preview}",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"Clipboard get error: {e}")
            return ToolResult(success=False, error=str(e))


class ClipboardSetTool(BaseTool):

    name = "clipboard_set"
    description = "Copy text to the system clipboard."
    parameters = {
        "content": ToolParameter(
            name="content",
            type="string",
            required=True,
            description="Text to copy to clipboard",
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        if not DESKTOP_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Desktop automation disabled. Set GUAARDVARK_DESKTOP_AUTOMATION=true",
            )

        content = kwargs.get("content")

        if content is None:
            return ToolResult(success=False, error="content is required")

        try:
            service = get_desktop_service()
            result = service.clipboard_set(content)

            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Copied {result.get('length')} chars to clipboard",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"Clipboard set error: {e}")
            return ToolResult(success=False, error=str(e))


class NotificationSendTool(BaseTool):

    name = "notification_send"
    description = "Display a desktop notification to the user."
    parameters = {
        "title": ToolParameter(
            name="title", type="string", required=True, description="Notification title"
        ),
        "message": ToolParameter(
            name="message",
            type="string",
            required=True,
            description="Notification message",
        ),
        "timeout": ToolParameter(
            name="timeout",
            type="int",
            required=False,
            description="Display duration in seconds",
            default=10,
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not DESKTOP_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Desktop automation disabled. Set GUAARDVARK_DESKTOP_AUTOMATION=true",
            )

        title = kwargs.get("title")
        message = kwargs.get("message")
        timeout = kwargs.get("timeout", 10)

        if not title or not message:
            return ToolResult(success=False, error="title and message are required")

        try:
            service = get_desktop_service()
            result = service.notification_send(title, message, timeout=timeout)

            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Sent notification: '{title}'",
                    metadata=result,
                )
            else:
                return ToolResult(success=False, error=result.get("error"))

        except Exception as e:
            logger.error(f"Notification send error: {e}")
            return ToolResult(success=False, error=str(e))


__all__ = [
    "FileWatchTool",
    "FileBulkOperationTool",
    "AppLaunchTool",
    "AppListTool",
    "AppFocusTool",
    "GUIClickTool",
    "GUITypeTool",
    "GUIHotkeyTool",
    "GUIScreenshotTool",
    "GUILocateImageTool",
    "ClipboardGetTool",
    "ClipboardSetTool",
    "NotificationSendTool",
]
