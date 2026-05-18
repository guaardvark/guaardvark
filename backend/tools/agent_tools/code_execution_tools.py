#!/usr/bin/env python3
"""
Code Execution Tools
Wraps existing code_execution_api.py capabilities as agent tools
"""

import logging
import tempfile
import os
from pathlib import Path
from typing import Dict, Any

from backend.services.agent_tools import BaseTool, ToolResult, ToolParameter

logger = logging.getLogger(__name__)


class ExecutePythonTool(BaseTool):
    """Execute Python code safely (wraps existing code_execution_api)"""
    
    name = "execute_python"
    description = "Execute Python code safely in an isolated environment and return the output"
    is_dangerous = True
    requires_approval = True
    parameters = {
        "code": ToolParameter(
            name="code",
            type="string",
            required=True,
            description="Python code to execute"
        ),
        "timeout": ToolParameter(
            name="timeout",
            type="int",
            required=False,
            description="Timeout in seconds (default: 30)",
            default=30
        ),
        "input_data": ToolParameter(
            name="input_data",
            type="string",
            required=False,
            description="Input data to pass to stdin (optional)",
            default=""
        )
    }
    
    def execute(self, code: str, timeout: int = 30, input_data: str = "") -> ToolResult:
        """
        Execute Python code using existing code_execution_api infrastructure
        
        Args:
            code: Python code to execute
            timeout: Execution timeout in seconds
            input_data: Optional input data for stdin
            
        Returns:
            ToolResult with execution output
        """
        try:
            # Import the existing code execution functions
            from backend.api.code_execution_api import (
                execute_command, 
                execute_command_with_stdin,
                create_temp_file,
                cleanup_temp_file,
                MAX_EXECUTION_TIME
            )
            
            # Cap timeout at maximum allowed
            timeout = min(timeout, MAX_EXECUTION_TIME)
            
            # Create temporary file with code
            temp_file = create_temp_file(code, '.py')
            
            try:
                # Execute with or without stdin
                if input_data:
                    command = ['python3', temp_file]
                    result = execute_command_with_stdin(command, timeout, input_data)
                else:
                    command = ['python3', temp_file]
                    result = execute_command(command, timeout)
                
                # Cleanup temp file
                cleanup_temp_file(temp_file)
                
                # Format result
                return ToolResult(
                    success=result['success'],
                    output=result['output'],
                    error=result['stderr'] if not result['success'] else None,
                    metadata={
                        'exit_code': result['exitCode'],
                        'execution_time': result['executionTime'],
                        'stdout': result['stdout'],
                        'stderr': result['stderr']
                    }
                )
                
            except Exception as e:
                # Ensure cleanup even on error
                cleanup_temp_file(temp_file)
                raise e
                
        except ImportError as e:
            logger.error(f"Failed to import code_execution_api: {e}")
            return ToolResult(
                success=False,
                error=f"Code execution infrastructure not available: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Python code execution failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Execution failed: {str(e)}"
            )


class ExecuteJavaScriptTool(BaseTool):
    """Execute JavaScript code safely (wraps existing code_execution_api)"""
    
    name = "execute_javascript"
    description = "Execute JavaScript/Node.js code safely and return the output"
    is_dangerous = True
    requires_approval = True
    parameters = {
        "code": ToolParameter(
            name="code",
            type="string",
            required=True,
            description="JavaScript code to execute"
        ),
        "timeout": ToolParameter(
            name="timeout",
            type="int",
            required=False,
            description="Timeout in seconds (default: 30)",
            default=30
        )
    }
    
    def execute(self, code: str, timeout: int = 30) -> ToolResult:
        """Execute JavaScript code"""
        try:
            from backend.api.code_execution_api import (
                execute_command,
                create_temp_file,
                cleanup_temp_file,
                MAX_EXECUTION_TIME
            )
            
            timeout = min(timeout, MAX_EXECUTION_TIME)
            temp_file = create_temp_file(code, '.js')
            
            try:
                command = ['node', temp_file]
                result = execute_command(command, timeout)
                cleanup_temp_file(temp_file)
                
                return ToolResult(
                    success=result['success'],
                    output=result['output'],
                    error=result['stderr'] if not result['success'] else None,
                    metadata={
                        'exit_code': result['exitCode'],
                        'execution_time': result['executionTime']
                    }
                )
            except Exception as e:
                cleanup_temp_file(temp_file)
                raise e
                
        except Exception as e:
            logger.error(f"JavaScript execution failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Execution failed: {str(e)}"
            )


class ExecuteShellTool(BaseTool):
    """Execute shell commands safely (wraps existing code_execution_api)"""
    
    name = "execute_shell"
    description = "Execute shell commands safely with security restrictions"
    is_dangerous = True
    requires_approval = True
    parameters = {
        "command": ToolParameter(
            name="command",
            type="string",
            required=True,
            description="Shell command to execute"
        ),
        "timeout": ToolParameter(
            name="timeout",
            type="int",
            required=False,
            description="Timeout in seconds (default: 30)",
            default=30
        ),
        "working_directory": ToolParameter(
            name="working_directory",
            type="string",
            required=False,
            description="Working directory for command execution",
            default="/tmp"
        )
    }
    
    def execute(self, command: str, timeout: int = 30, working_directory: str = "/tmp") -> ToolResult:
        """Execute shell command with security checks"""
        try:
            from backend.api.code_execution_api import execute_command, MAX_EXECUTION_TIME
            
            # Security check - block dangerous commands
            dangerous_commands = [
                'rm -rf', 'sudo', 'su', 'chmod 777', 'dd if=', 'mkfs', 
                'fdisk', 'format', '>', '>>', '|', '&', ';', '$(', '`'
            ]
            
            if any(dangerous in command.lower() for dangerous in dangerous_commands):
                logger.warning(f"Blocked dangerous shell command: {command}")
                return ToolResult(
                    success=False,
                    error="Command not allowed for security reasons"
                )
            
            timeout = min(timeout, MAX_EXECUTION_TIME)
            
            # Execute with shell=True (inherently dangerous, but with security checks)
            result = execute_command(command, timeout, working_directory, use_shell=True)
            
            return ToolResult(
                success=result['success'],
                output=result['output'],
                error=result['stderr'] if not result['success'] else None,
                metadata={
                    'exit_code': result['exitCode'],
                    'execution_time': result['executionTime'],
                    'working_directory': working_directory
                }
            )
            
        except Exception as e:
            logger.error(f"Shell command execution failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Execution failed: {str(e)}"
            )

