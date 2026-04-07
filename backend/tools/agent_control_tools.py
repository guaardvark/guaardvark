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
            from backend.services.agent_control_service import get_agent_control_service
            service = get_agent_control_service()
            if service.is_active:
                return ToolResult(success=False, error="Agent mode already active with a running task")
            service.start()
            return ToolResult(success=True, output="Agent mode activated. Use agent_task_execute to run a task.")
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
        # LLMs sometimes use the wrong parameter name (args, param_name, etc.)
        # Accept anything that looks like it contains the task description.
        task = kwargs.get("task", "") or kwargs.get("args", "") or kwargs.get("param_name", "")
        if not task:
            # Last resort: grab the first string value from whatever was passed
            for v in kwargs.values():
                if isinstance(v, str) and v.strip():
                    task = v
                    break
        if not task:
            return ToolResult(success=False, error="Task description is required")

        try:
            _ensure_agent_display()
            from backend.services.agent_control_service import get_agent_control_service
            from backend.services.local_screen_backend import LocalScreenBackend
            from backend.utils.vision_analyzer import VisionAnalyzer

            service = get_agent_control_service()
            screen = LocalScreenBackend()
            result = service.execute_task(task, screen)

            # Post-task analysis: quick snapshot of what the screen shows now.
            # Keep it fast — users shouldn't wait 5s after the task is already done.
            post_analysis = ""
            if result.success:
                try:
                    import time as _time
                    import numpy as np
                    _time.sleep(0.5)  # Brief settle — enough for UI repaints
                    analyzer = VisionAnalyzer()
                    screenshot, _ = screen.capture()
                    if np.array(screenshot).mean() < 10:
                        post_analysis = "Screen appears black — display may need attention."
                    else:
                        analysis = analyzer.analyze(
                            screenshot,
                            prompt=f"The task was: {task}\n\nBriefly describe what the screen shows now.",
                            num_predict=128,
                            temperature=0.1,
                        )
                        if analysis.success:
                            post_analysis = analysis.description
                except Exception as e:
                    logger.warning(f"Post-task analysis failed: {e}")

            output_parts = []
            if result.success:
                output_parts.append(f"Task completed successfully in {len(result.steps)} steps ({round(result.total_time_seconds, 1)}s).")
            else:
                output_parts.append(f"Task failed: {result.reason}")
            if post_analysis:
                output_parts.append(f"\nWhat I see on screen now:\n{post_analysis}")

            return ToolResult(
                success=result.success,
                output="\n".join(output_parts),
                metadata={
                    "steps": len(result.steps),
                    "time_seconds": round(result.total_time_seconds, 1),
                    "screen_analysis": post_analysis[:500] if post_analysis else None,
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
        prompt = (kwargs.get("prompt", "") or kwargs.get("args", "") or kwargs.get("param_name", "")
                  or "Describe what is currently on the screen.")

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
