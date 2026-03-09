#!/usr/bin/env python3
"""
System Tools
Safe, read-only system operations for agents.
"""

import logging
import subprocess
import shlex
from typing import List, Dict, Any

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

class SystemCommandTool(BaseTool):
    """
    Executes safe, read-only system commands.
    """
    
    name = "system_command"
    description = "Inspect local project files and directories (ls, grep, cat, find). Only for filesystem operations, NOT for looking up general information — use web_search for that."
    is_dangerous = False # Explicitly safe because of whitelist
    
    # Whitelist of allowed commands
    ALLOWED_COMMANDS = [
        "ls", "grep", "cat", "find", "wc", "head", "tail", "pwd", "whoami", "date", "echo"
    ]
    
    parameters = {
        "command": ToolParameter(
            name="command",
            type="string",
            required=True,
            description="The command to execute (e.g., 'ls -la', 'grep pattern file.txt')"
        ),
        "cwd": ToolParameter(
            name="cwd",
            type="string",
            required=False,
            description="Current working directory",
            default=None
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        """Execute the system command"""
        command_str = kwargs.get("command", "")
        cwd = kwargs.get("cwd")
        
        if not command_str:
            return ToolResult(success=False, error="Command is required")
            
        # Security check: Parse command and check against whitelist
        try:
            parts = shlex.split(command_str)
            if not parts:
                return ToolResult(success=False, error="Empty command")
                
            base_cmd = parts[0]
            if base_cmd not in self.ALLOWED_COMMANDS:
                return ToolResult(
                    success=False, 
                    error=f"Command '{base_cmd}' is not allowed. Allowed: {', '.join(self.ALLOWED_COMMANDS)}"
                )
                
            # Prevent chaining or piping which might bypass checks (simple check)
            if any(c in command_str for c in [";", "|", "&", "`", "$("]):
                 return ToolResult(
                    success=False, 
                    error="Command chaining/piping/substitution is not allowed for security reasons."
                )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to parse command: {e}")
            
        try:
            # Use project root as default CWD if available in context
            if not cwd and self._context:
                # Try to get project path from context if available
                # This is a placeholder for where we'd use the injected context
                pass
                
            # Execute
            result = subprocess.run(
                parts, 
                capture_output=True, 
                text=True, 
                cwd=cwd,
                timeout=10
            )
            
            return ToolResult(
                success=result.returncode == 0,
                output=result.stdout + result.stderr,
                metadata={
                    "returncode": result.returncode,
                    "command": command_str
                }
            )
            
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="Command timed out")
        except Exception as e:
            logger.error(f"System command execution failed: {e}", exc_info=True)
            return ToolResult(success=False, error=str(e))
