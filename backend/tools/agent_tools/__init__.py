"""Agent Tools - Tool implementations for agent capabilities"""

from backend.services.agent_tools import (
    BaseTool,
    ToolRegistry,
    ToolResult,
    ToolParameter,
    get_tool_registry,
)

# Import code manipulation tools
from backend.tools.agent_tools.code_manipulation_tools import (
    ReadCodeTool,
    SearchCodeTool,
    EditCodeTool,
    ListCodeFilesTool,
    VerifyChangeTool,
    CODE_MANIPULATION_TOOLS,
    register_code_manipulation_tools,
)

__all__ = [
    "BaseTool",
    "ToolRegistry",
    "ToolResult",
    "ToolParameter",
    "get_tool_registry",
    # Code manipulation tools
    "ReadCodeTool",
    "SearchCodeTool",
    "EditCodeTool",
    "ListCodeFilesTool",
    "VerifyChangeTool",
    "CODE_MANIPULATION_TOOLS",
    "register_code_manipulation_tools",
]


def initialize_all_tools():
    """Initialize and register all agent tools"""
    register_code_manipulation_tools()


# Auto-register tools on module import
try:
    initialize_all_tools()
except Exception as e:
    import logging

    logging.getLogger(__name__).warning(f"Failed to auto-register tools: {e}")
