#!/usr/bin/env python3
"""
Code Execution Tools
Wraps existing code_execution_api.py capabilities as agent tools
"""

import logging

from backend.services.agent_tools import BaseTool, ToolResult, ToolParameter

logger = logging.getLogger(__name__)


class ExecutePythonTool(BaseTool):
    """Execute Python code (python3 subprocess with a timeout; no network unless allowed)"""
    
    name = "execute_python"
    read_only = False
    description = ("Run Python code on this machine and return the output (30s timeout; the user is asked "
                   "to approve each run). It has no internet or network access unless the person allowed "
                   "it in Settings, so do not fetch URLs or call APIs from it.")
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
                # Without the network unless the person allowed it (code_sandbox).
                from backend.services.code_sandbox import wrap
                command, refused = wrap(['python3', temp_file])
                if refused:
                    cleanup_temp_file(temp_file)
                    return ToolResult(success=False, error=refused, metadata={"blocked_by": "no_network_sandbox"})
                if input_data:
                    result = execute_command_with_stdin(command, timeout, input_data)
                else:
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
    read_only = False
    description = ("Execute JavaScript/Node.js code and return the output. It has no internet or network "
                   "access unless the person allowed it in Settings.")
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
                # Without the network unless the person allowed it (code_sandbox).
                from backend.services.code_sandbox import wrap
                command, refused = wrap(['node', temp_file])
                if refused:
                    cleanup_temp_file(temp_file)
                    return ToolResult(success=False, error=refused, metadata={"blocked_by": "no_network_sandbox"})
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


# NOTE: ExecuteShellTool was removed 2026-05-31. It ran `use_shell=True` behind
# a substring blocklist that is trivially bypassable (e.g. quoting, env-var
# expansion, alternate command spellings), so it offered the appearance of
# safety without the substance. Its removal was already flagged in CLAUDE.md.
# For shell execution use the guarded /api/code-execution/shell endpoint, which
# runs list-form commands without a shell.
#
# Repository-scoped command execution is intentionally deferred. A future
# implementation must use a real sandbox or a deliberately named native runner
# with list-form subprocess calls, containment checks, env/output limits, and
# negative tests for traversal, symlinks, and shell metacharacters.
#
# NOTE: these tools are registered by backend/tools/tool_registry_init.py
# (register_test_execution_tools), which imports ExecutePythonTool /
# ExecuteJavaScriptTool directly. A second module-level registration helper
# previously lived here but had no callers and risked drifting out of sync with
# the real path, so it was removed 2026-05-31.

