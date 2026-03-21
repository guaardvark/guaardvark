#!/usr/bin/env python3
"""
Agent Control Tools — BaseTool implementations for Agent Vision Control.

These tools allow the agent system to start/stop agent mode and execute
vision-based automation tasks.
"""

import logging
import os
from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

# Virtual display for agent operations (set by start script or manually)
AGENT_DISPLAY = os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99")


def _ensure_agent_display():
    """Set DISPLAY to the agent's virtual display for pyautogui/mss operations."""
    os.environ["DISPLAY"] = AGENT_DISPLAY


class AgentModeStartTool(BaseTool):
    name = "agent_mode_start"
    description = "Start agent vision control mode on the local machine. Enables screen capture and mouse/keyboard control."
    parameters = {}

    def execute(self, **kwargs) -> ToolResult:
        try:
            _ensure_agent_display()
            from backend.services.agent_control_service import get_agent_control_service
            service = get_agent_control_service()
            if service.is_active:
                return ToolResult(success=False, error="Agent mode already active with a running task")
            service.start()
            return ToolResult(success=True, output=f"Agent mode activated on display {AGENT_DISPLAY}. Use agent_task_execute to run a task.")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class AgentModeStopTool(BaseTool):
    name = "agent_mode_stop"
    description = "Gracefully stop agent vision control mode. For emergency stop, use the kill switch."
    parameters = {}

    def execute(self, **kwargs) -> ToolResult:
        try:
            from backend.services.agent_control_service import get_agent_control_service
            service = get_agent_control_service()
            service.stop()
            return ToolResult(success=True, output="Agent mode stopped gracefully")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class AgentTaskExecuteTool(BaseTool):
    name = "agent_task_execute"
    description = "Execute a task using vision-based agent control. The agent will analyze the screen and perform mouse/keyboard actions to complete the task."
    is_dangerous = True
    requires_confirmation = True
    parameters = {
        "task": ToolParameter(
            name="task",
            type="string",
            required=True,
            description="Natural language description of the task to perform (e.g., 'Post hello to Twitter')"
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        task = kwargs.get("task", "")
        if not task:
            return ToolResult(success=False, error="Task description is required")

        try:
            _ensure_agent_display()
            from backend.services.agent_control_service import get_agent_control_service
            from backend.services.local_screen_backend import LocalScreenBackend

            service = get_agent_control_service()
            screen = LocalScreenBackend()
            result = service.execute_task(task, screen)

            return ToolResult(
                success=result.success,
                output=result.reason,
                metadata={
                    "steps": len(result.steps),
                    "time_seconds": round(result.total_time_seconds, 1),
                }
            )
        except Exception as e:
            logger.error(f"Agent task execution error: {e}", exc_info=True)
            return ToolResult(success=False, error=str(e))


class AgentScreenCaptureTool(BaseTool):
    name = "agent_screen_capture"
    description = "Take a screenshot of the local screen and analyze it with a vision model."
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            required=False,
            description="Custom prompt for vision analysis (default: describe the screen)",
            default="Describe what is currently on the screen."
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        prompt = kwargs.get("prompt", "Describe what is currently on the screen.")

        try:
            _ensure_agent_display()
            from backend.services.local_screen_backend import LocalScreenBackend
            from backend.utils.vision_analyzer import VisionAnalyzer

            screen = LocalScreenBackend()
            screenshot, cursor_pos = screen.capture()

            analyzer = VisionAnalyzer()
            result = analyzer.analyze(screenshot, prompt=prompt)

            if result.success:
                return ToolResult(
                    success=True,
                    output=result.description,
                    metadata={
                        "cursor": cursor_pos,
                        "model": result.model_used,
                        "inference_ms": result.inference_ms,
                    }
                )
            else:
                return ToolResult(success=False, error=result.error)

        except Exception as e:
            return ToolResult(success=False, error=str(e))


class AgentStatusTool(BaseTool):
    name = "agent_status"
    description = "Get the current status of the agent vision control system."
    parameters = {}

    def execute(self, **kwargs) -> ToolResult:
        try:
            from backend.services.agent_control_service import get_agent_control_service
            service = get_agent_control_service()
            return ToolResult(success=True, output=service.get_status())
        except Exception as e:
            return ToolResult(success=False, error=str(e))
