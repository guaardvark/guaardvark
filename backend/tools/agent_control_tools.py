#!/usr/bin/env python3
"""
Agent Control Tools — BaseTool implementations for Agent Vision Control.

These tools allow the agent system to start/stop agent mode and execute
vision-based automation tasks.
"""

import glob
import logging
import os
import time

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

AGENT_DISPLAY = os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99")
GUAARDVARK_ROOT = os.environ.get(
    "GUAARDVARK_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
SCREENSHOTS_DIR = os.path.join(GUAARDVARK_ROOT, "data", "outputs", "screenshots")
MAX_SCREENSHOTS = 200  # FIFO bloat guard


def _prune_old_screenshots(directory: str, max_keep: int = MAX_SCREENSHOTS):
    """Remove oldest screenshots if the directory exceeds max_keep files."""
    try:
        files = sorted(glob.glob(os.path.join(directory, "*.webp")), key=os.path.getmtime)
        excess = len(files) - max_keep
        if excess > 0:
            for f in files[:excess]:
                os.remove(f)
    except Exception:
        pass  # non-critical housekeeping


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

            # Auto-detect training mode — keeps the agent clicking instead of stopping after one hit
            import re as _re
            training_mode = bool(_re.search(r'\b(?:training|practice|keep clicking|vision trainer)\b', task, _re.IGNORECASE))
            result = service.execute_task(task, screen, training_mode=training_mode)

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
    description = "Take a screenshot of the local screen and analyze it with a vision model. Always pass a prompt that describes what you are looking for or verifying — the vision model will answer your specific question about the screen."
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            required=False,
            description="What to look for or verify on screen (e.g., 'Is the word guaardvark visible in the search bar?' or 'What page is currently loaded?'). Be specific — the vision model answers this question.",
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

            # Save screenshot so it appears in chat — the unified engine's
            # chat:image emission triggers automatically on metadata["image_url"]
            image_url = None
            try:
                os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
                filename = f"agent_capture_{int(time.time() * 1000)}.webp"
                filepath = os.path.join(SCREENSHOTS_DIR, filename)
                screenshot.save(filepath, format="WEBP", quality=80)
                image_url = f"/api/tools/screenshots/{filename}"
                _prune_old_screenshots(SCREENSHOTS_DIR)
            except Exception as e:
                logger.warning(f"Failed to save screenshot for chat: {e}")

            analyzer = VisionAnalyzer()
            result = analyzer.analyze(screenshot, prompt=prompt)

            if result.success:
                metadata = {
                    "cursor": cursor_pos,
                    "model": result.model_used,
                    "inference_ms": result.inference_ms,
                }
                if image_url:
                    metadata["image_url"] = image_url
                    # Use the actual vision analysis as the caption so the
                    # user sees the model's real observation under the screenshot
                    metadata["prompt"] = result.description[:200] if result.description else "Agent screen capture"
                return ToolResult(
                    success=True,
                    output=result.description,
                    metadata=metadata,
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
