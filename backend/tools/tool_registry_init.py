#!/usr/bin/env python3
"""
Tool Registry Initialization
Registers all available tools with the global tool registry.
This module should be imported during application startup.
"""

import logging
from typing import List, Optional, Dict

from backend.services.agent_tools import (
    BaseTool,
    ToolRegistry,
    get_tool_registry,
    register_tool
)

logger = logging.getLogger(__name__)

# Track registered tools for debugging
_registered_tools: List[str] = []
_tool_categories: Dict[str, str] = {}  # tool_name -> category
_initialization_complete = False


def register_content_tools() -> List[str]:
    """Register content generation tools"""
    global _tool_categories
    registered = []
    category = "content"

    try:
        from backend.tools.content_tools import (
            WordPressContentTool,
            EnhancedWordPressContentTool
        )

        register_tool(WordPressContentTool())
        registered.append("generate_wordpress_content")
        _tool_categories["generate_wordpress_content"] = category
        logger.info("Registered: WordPressContentTool")

        register_tool(EnhancedWordPressContentTool())
        registered.append("generate_enhanced_wordpress_content")
        _tool_categories["generate_enhanced_wordpress_content"] = category
        logger.info("Registered: EnhancedWordPressContentTool")

    except ImportError as e:
        logger.error(f"Failed to import content tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register content tools: {e}")

    return registered


def register_generation_tools() -> List[str]:
    """Register file/CSV generation tools"""
    global _tool_categories
    registered = []
    category = "generation"

    try:
        from backend.tools.generation_tools import (
            BulkCSVGeneratorTool,
            FileGeneratorTool,
            CSVGeneratorTool
        )

        register_tool(BulkCSVGeneratorTool())
        registered.append("generate_bulk_csv")
        _tool_categories["generate_bulk_csv"] = category
        logger.info("Registered: BulkCSVGeneratorTool")

        register_tool(FileGeneratorTool())
        registered.append("generate_file")
        _tool_categories["generate_file"] = category
        logger.info("Registered: FileGeneratorTool")

        register_tool(CSVGeneratorTool())
        registered.append("generate_csv")
        _tool_categories["generate_csv"] = category
        logger.info("Registered: CSVGeneratorTool")

    except ImportError as e:
        logger.error(f"Failed to import generation tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register generation tools: {e}")

    return registered


def register_code_tools() -> List[str]:
    """Register code analysis/generation tools"""
    global _tool_categories
    registered = []
    category = "code"

    try:
        from backend.tools.code_tools import (
            CodeGeneratorTool,
            CodeAnalysisTool
        )

        register_tool(CodeGeneratorTool())
        registered.append("codegen")
        _tool_categories["codegen"] = category
        logger.info("Registered: CodeGeneratorTool")

        register_tool(CodeAnalysisTool())
        registered.append("analyze_code")
        _tool_categories["analyze_code"] = category
        logger.info("Registered: CodeAnalysisTool")

    except ImportError as e:
        logger.error(f"Failed to import code tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register code tools: {e}")

    return registered


def register_web_tools() -> List[str]:
    """Register web analysis and search tools"""
    global _tool_categories
    registered = []
    category = "web"

    try:
        from backend.tools.web_tools import (
            FetchUrlTool,
            WebAnalysisTool,
            WebSearchTool
        )

        register_tool(FetchUrlTool())
        registered.append("fetch_url")
        _tool_categories["fetch_url"] = category
        logger.info("Registered: FetchUrlTool")

        register_tool(WebAnalysisTool())
        registered.append("analyze_website")
        _tool_categories["analyze_website"] = category
        logger.info("Registered: WebAnalysisTool")

        register_tool(WebSearchTool())
        registered.append("web_search")
        _tool_categories["web_search"] = category
        logger.info("Registered: WebSearchTool")

    except ImportError as e:
        logger.error(f"Failed to import web tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register web tools: {e}")

    return registered


def register_browser_tools() -> List[str]:
    """Register browser automation tools"""
    global _tool_categories
    registered = []
    category = "browser"

    try:
        from backend.tools.browser_tools import (
            BrowserNavigateTool,
            BrowserClickTool,
            BrowserFillTool,
            BrowserScreenshotTool,
            BrowserExtractTool,
            BrowserWaitTool,
            BrowserExecuteJSTool,
            BrowserGetHTMLTool,
        )

        register_tool(BrowserNavigateTool())
        registered.append("browser_navigate")
        _tool_categories["browser_navigate"] = category
        logger.info("Registered: BrowserNavigateTool")

        register_tool(BrowserClickTool())
        registered.append("browser_click")
        _tool_categories["browser_click"] = category
        logger.info("Registered: BrowserClickTool")

        register_tool(BrowserFillTool())
        registered.append("browser_fill")
        _tool_categories["browser_fill"] = category
        logger.info("Registered: BrowserFillTool")

        register_tool(BrowserScreenshotTool())
        registered.append("browser_screenshot")
        _tool_categories["browser_screenshot"] = category
        logger.info("Registered: BrowserScreenshotTool")

        register_tool(BrowserExtractTool())
        registered.append("browser_extract")
        _tool_categories["browser_extract"] = category
        logger.info("Registered: BrowserExtractTool")

        register_tool(BrowserWaitTool())
        registered.append("browser_wait")
        _tool_categories["browser_wait"] = category
        logger.info("Registered: BrowserWaitTool")

        register_tool(BrowserExecuteJSTool())
        registered.append("browser_execute_js")
        _tool_categories["browser_execute_js"] = category
        logger.info("Registered: BrowserExecuteJSTool")

        register_tool(BrowserGetHTMLTool())
        registered.append("browser_get_html")
        _tool_categories["browser_get_html"] = category
        logger.info("Registered: BrowserGetHTMLTool")

    except ImportError as e:
        logger.error(f"Failed to import browser tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register browser tools: {e}")

    return registered


def register_desktop_tools() -> List[str]:
    """Register desktop automation tools"""
    global _tool_categories
    registered = []
    category = "desktop"

    try:
        from backend.tools.desktop_tools import (
            FileWatchTool,
            FileBulkOperationTool,
            AppLaunchTool,
            AppListTool,
            AppFocusTool,
            GUIClickTool,
            GUITypeTool,
            GUIHotkeyTool,
            GUIScreenshotTool,
            GUILocateImageTool,
            ClipboardGetTool,
            ClipboardSetTool,
            NotificationSendTool,
        )

        register_tool(FileWatchTool())
        registered.append("file_watch")
        _tool_categories["file_watch"] = category
        logger.info("Registered: FileWatchTool")

        register_tool(FileBulkOperationTool())
        registered.append("file_bulk_operation")
        _tool_categories["file_bulk_operation"] = category
        logger.info("Registered: FileBulkOperationTool")

        register_tool(AppLaunchTool())
        registered.append("app_launch")
        _tool_categories["app_launch"] = category
        logger.info("Registered: AppLaunchTool")

        register_tool(AppListTool())
        registered.append("app_list")
        _tool_categories["app_list"] = category
        logger.info("Registered: AppListTool")

        register_tool(AppFocusTool())
        registered.append("app_focus")
        _tool_categories["app_focus"] = category
        logger.info("Registered: AppFocusTool")

        register_tool(GUIClickTool())
        registered.append("gui_click")
        _tool_categories["gui_click"] = category
        logger.info("Registered: GUIClickTool")

        register_tool(GUITypeTool())
        registered.append("gui_type")
        _tool_categories["gui_type"] = category
        logger.info("Registered: GUITypeTool")

        register_tool(GUIHotkeyTool())
        registered.append("gui_hotkey")
        _tool_categories["gui_hotkey"] = category
        logger.info("Registered: GUIHotkeyTool")

        register_tool(GUIScreenshotTool())
        registered.append("gui_screenshot")
        _tool_categories["gui_screenshot"] = category
        logger.info("Registered: GUIScreenshotTool")

        register_tool(GUILocateImageTool())
        registered.append("gui_locate_image")
        _tool_categories["gui_locate_image"] = category
        logger.info("Registered: GUILocateImageTool")

        register_tool(ClipboardGetTool())
        registered.append("clipboard_get")
        _tool_categories["clipboard_get"] = category
        logger.info("Registered: ClipboardGetTool")

        register_tool(ClipboardSetTool())
        registered.append("clipboard_set")
        _tool_categories["clipboard_set"] = category
        logger.info("Registered: ClipboardSetTool")

        register_tool(NotificationSendTool())
        registered.append("notification_send")
        _tool_categories["notification_send"] = category
        logger.info("Registered: NotificationSendTool")

    except ImportError as e:
        logger.error(f"Failed to import desktop tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register desktop tools: {e}")

    return registered


def register_mcp_tools() -> List[str]:
    """Register MCP (Model Context Protocol) tools"""
    global _tool_categories
    registered = []
    category = "mcp"

    try:
        from backend.tools.mcp_tools import (
            MCPListServersTool,
            MCPConnectTool,
            MCPDisconnectTool,
            MCPListToolsTool,
            MCPExecuteTool,
            MCPGetStateTool,
        )

        register_tool(MCPListServersTool())
        registered.append("mcp_list_servers")
        _tool_categories["mcp_list_servers"] = category
        logger.info("Registered: MCPListServersTool")

        register_tool(MCPConnectTool())
        registered.append("mcp_connect")
        _tool_categories["mcp_connect"] = category
        logger.info("Registered: MCPConnectTool")

        register_tool(MCPDisconnectTool())
        registered.append("mcp_disconnect")
        _tool_categories["mcp_disconnect"] = category
        logger.info("Registered: MCPDisconnectTool")

        register_tool(MCPListToolsTool())
        registered.append("mcp_list_tools")
        _tool_categories["mcp_list_tools"] = category
        logger.info("Registered: MCPListToolsTool")

        register_tool(MCPExecuteTool())
        registered.append("mcp_execute")
        _tool_categories["mcp_execute"] = category
        logger.info("Registered: MCPExecuteTool")

        register_tool(MCPGetStateTool())
        registered.append("mcp_get_state")
        _tool_categories["mcp_get_state"] = category
        logger.info("Registered: MCPGetStateTool")

    except ImportError as e:
        logger.error(f"Failed to import MCP tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register MCP tools: {e}")

    return registered


def register_system_tools() -> List[str]:
    """Register system utility tools"""
    global _tool_categories
    registered = []
    category = "system"

    try:
        from backend.tools.system_tools import SystemCommandTool

        register_tool(SystemCommandTool())
        registered.append("system_command")
        _tool_categories["system_command"] = category
        logger.info("Registered: SystemCommandTool")

    except ImportError as e:
        logger.error(f"Failed to import system tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register system tools: {e}")

    return registered


def register_memory_tools() -> List[str]:
    """Register agent memory management tools"""
    global _tool_categories
    registered = []
    category = "memory"

    try:
        from backend.tools.memory_tools import (
            SaveMemoryTool,
            SearchMemoryTool,
            DeleteMemoryTool
        )

        register_tool(SaveMemoryTool())
        registered.append("save_memory")
        _tool_categories["save_memory"] = category
        logger.info("Registered: SaveMemoryTool")

        register_tool(SearchMemoryTool())
        registered.append("search_memory")
        _tool_categories["search_memory"] = category
        logger.info("Registered: SearchMemoryTool")
        
        register_tool(DeleteMemoryTool())
        registered.append("delete_memory")
        _tool_categories["delete_memory"] = category
        logger.info("Registered: DeleteMemoryTool")

    except ImportError as e:
        logger.error(f"Failed to import memory tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register memory tools: {e}")

    return registered


def register_rag_tools() -> List[str]:
    """Register RAG/Knowledge tools"""
    global _tool_categories
    registered = []
    category = "knowledge"

    try:
        from backend.tools.rag_tools import KnowledgeSearchTool

        register_tool(KnowledgeSearchTool())
        registered.append("search_knowledge_base")
        _tool_categories["search_knowledge_base"] = category
        logger.info("Registered: KnowledgeSearchTool")

    except ImportError as e:
        logger.error(f"Failed to import RAG tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register RAG tools: {e}")

    return registered


def register_media_tools() -> List[str]:
    """Register media player control tools"""
    global _tool_categories
    registered = []
    category = "media"

    try:
        from backend.tools.media_tools import (
            MediaPlayTool,
            MediaControlTool,
            MediaVolumeTool,
            MediaStatusTool,
        )

        register_tool(MediaPlayTool())
        registered.append("media_play")
        _tool_categories["media_play"] = category
        logger.info("Registered: MediaPlayTool")

        register_tool(MediaControlTool())
        registered.append("media_control")
        _tool_categories["media_control"] = category
        logger.info("Registered: MediaControlTool")

        register_tool(MediaVolumeTool())
        registered.append("media_volume")
        _tool_categories["media_volume"] = category
        logger.info("Registered: MediaVolumeTool")

        register_tool(MediaStatusTool())
        registered.append("media_status")
        _tool_categories["media_status"] = category
        logger.info("Registered: MediaStatusTool")

    except ImportError as e:
        logger.error(f"Failed to import media tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register media tools: {e}")

    return registered


def register_image_tools() -> List[str]:
    """Register image generation, animation, and vision tools."""
    registered = []
    try:
        from backend.tools.image_tools import ImageGeneratorTool
        register_tool(ImageGeneratorTool())
        registered.append("generate_image")
        logger.info("Registered: ImageGeneratorTool")
    except Exception as e:
        logger.warning(f"Failed to register image tools: {e}")

    try:
        from backend.tools.image_tools import AnimationGeneratorTool
        register_tool(AnimationGeneratorTool())
        registered.append("generate_animation")
        _tool_categories["generate_animation"] = "image"
        logger.info("Registered: AnimationGeneratorTool")
    except Exception as e:
        logger.warning(f"Failed to register animation tools: {e}")

    return registered


def register_test_execution_tools() -> List[str]:
    """Register sandboxed test execution for code_assistant agent."""
    global _tool_categories
    registered = []
    category = "test_execution"
    try:
        from backend.tools.agent_tools.code_execution_tools import ExecutePythonTool
        tool = ExecutePythonTool()
        tool._sandboxed = True  # Flag for sandbox enforcement
        register_tool(tool)
        registered.append("execute_python")
        _tool_categories["execute_python"] = category
        logger.info("Registered sandboxed: ExecutePythonTool")
    except ImportError as e:
        logger.warning(f"Could not import code execution tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register test execution tools: {e}")
    return registered


def register_agent_control_tools() -> List[str]:
    """Register agent vision control tools"""
    global _tool_categories
    registered = []
    category = "agent_control"

    try:
        from backend.tools.agent_control_tools import (
            AgentModeStartTool,
            AgentModeStopTool,
            AgentTaskExecuteTool,
            AgentScreenCaptureTool,
            AgentStatusTool,
        )

        register_tool(AgentModeStartTool())
        registered.append("agent_mode_start")
        _tool_categories["agent_mode_start"] = category
        logger.info("Registered: AgentModeStartTool")

        register_tool(AgentModeStopTool())
        registered.append("agent_mode_stop")
        _tool_categories["agent_mode_stop"] = category
        logger.info("Registered: AgentModeStopTool")

        register_tool(AgentTaskExecuteTool())
        registered.append("agent_task_execute")
        _tool_categories["agent_task_execute"] = category
        logger.info("Registered: AgentTaskExecuteTool")

        register_tool(AgentScreenCaptureTool())
        registered.append("agent_screen_capture")
        _tool_categories["agent_screen_capture"] = category
        logger.info("Registered: AgentScreenCaptureTool")

        register_tool(AgentStatusTool())
        registered.append("agent_status")
        _tool_categories["agent_status"] = category
        logger.info("Registered: AgentStatusTool")

    except ImportError as e:
        logger.error(f"Failed to import agent control tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register agent control tools: {e}")

    return registered


def initialize_all_tools() -> ToolRegistry:
    """
    Initialize and register all available tools.
    Call this during application startup.

    Returns:
        ToolRegistry: The global tool registry with all tools registered
    """
    global _registered_tools, _initialization_complete

    if _initialization_complete:
        logger.info("Tool registry already initialized, returning existing registry")
        return get_tool_registry()

    logger.info("=" * 60)
    logger.info("INITIALIZING TOOL REGISTRY")
    logger.info("=" * 60)

    # Register all tool categories
    _registered_tools.extend(register_content_tools())
    _registered_tools.extend(register_generation_tools())
    _registered_tools.extend(register_code_tools())
    _registered_tools.extend(register_web_tools())
    _registered_tools.extend(register_browser_tools())
    _registered_tools.extend(register_desktop_tools())
    _registered_tools.extend(register_mcp_tools())
    _registered_tools.extend(register_system_tools())
    _registered_tools.extend(register_memory_tools())
    _registered_tools.extend(register_rag_tools())
    _registered_tools.extend(register_media_tools())
    _registered_tools.extend(register_image_tools())
    _registered_tools.extend(register_test_execution_tools())
    _registered_tools.extend(register_agent_control_tools())

    # Get the registry for status reporting
    registry = get_tool_registry()

    logger.info("=" * 60)
    logger.info(f"TOOL REGISTRY INITIALIZED: {len(registry)} tools registered")
    logger.info(f"Registered tools: {', '.join(_registered_tools)}")
    logger.info("=" * 60)

    _initialization_complete = True
    return registry


def get_registered_tools() -> List[str]:
    """Get list of registered tool names"""
    if not _initialization_complete:
        initialize_all_tools()
    return _registered_tools.copy()


def get_tool_categories() -> Dict[str, str]:
    """Get mapping of tool names to categories"""
    if not _initialization_complete:
        initialize_all_tools()
    return _tool_categories.copy()


def get_tools_by_category() -> Dict[str, List[str]]:
    """Get tools organized by category"""
    if not _initialization_complete:
        initialize_all_tools()

    by_category: Dict[str, List[str]] = {}
    for tool_name, category in _tool_categories.items():
        if category not in by_category:
            by_category[category] = []
        by_category[category].append(tool_name)
    return by_category


def get_registered_tools_with_descriptions() -> List[Dict[str, str]]:
    """
    Get tools with descriptions for capability awareness.

    Returns:
        List of dicts with name, description, and category for each tool
    """
    if not _initialization_complete:
        initialize_all_tools()

    registry = get_tool_registry()
    result = []

    for tool_name in _registered_tools:
        tool = registry.get_tool(tool_name)
        if tool:
            result.append({
                "name": tool.name,
                "description": tool.description,
                "category": _tool_categories.get(tool_name, "other")
            })

    return result


def get_tool_schemas_for_prompt(format: str = 'xml') -> str:
    """
    Get tool schemas formatted for LLM prompts.

    Args:
        format: 'xml' or 'json'

    Returns:
        Formatted tool schemas string
    """
    registry = get_tool_registry()
    return registry.get_tool_schemas(format=format)


def execute_tool_by_name(tool_name: str, **kwargs) -> dict:
    """
    Execute a tool by name with given parameters.

    Args:
        tool_name: Name of the tool to execute
        **kwargs: Tool parameters

    Returns:
        dict: Tool result as dictionary
    """
    registry = get_tool_registry()
    result = registry.execute_tool(tool_name, **kwargs)
    return result.to_dict()


# Convenience exports
__all__ = [
    'initialize_all_tools',
    'get_registered_tools',
    'get_tool_categories',
    'get_tools_by_category',
    'get_registered_tools_with_descriptions',
    'get_tool_schemas_for_prompt',
    'execute_tool_by_name',
    'get_tool_registry',
]
