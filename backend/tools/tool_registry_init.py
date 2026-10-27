#!/usr/bin/env python3
"""
Tool Registry Initialization
Registers all available tools with the global tool registry.
This module should be imported during application startup.
"""

import logging
import os
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
        logger.debug("Registered: WordPressContentTool")

        register_tool(EnhancedWordPressContentTool())
        registered.append("generate_enhanced_wordpress_content")
        _tool_categories["generate_enhanced_wordpress_content"] = category
        logger.debug("Registered: EnhancedWordPressContentTool")

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
        logger.debug("Registered: BulkCSVGeneratorTool")

        register_tool(FileGeneratorTool())
        registered.append("generate_file")
        _tool_categories["generate_file"] = category
        logger.debug("Registered: FileGeneratorTool")

        register_tool(CSVGeneratorTool())
        registered.append("generate_csv")
        _tool_categories["generate_csv"] = category
        logger.debug("Registered: CSVGeneratorTool")

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
        logger.debug("Registered: CodeGeneratorTool")

        register_tool(CodeAnalysisTool())
        registered.append("analyze_code")
        _tool_categories["analyze_code"] = category
        logger.debug("Registered: CodeAnalysisTool")

        from backend.tools.code_search_tools import SearchCodebaseTool

        register_tool(SearchCodebaseTool())
        registered.append("search_codebase")
        _tool_categories["search_codebase"] = category
        logger.debug("Registered: SearchCodebaseTool")

        from backend.tools.inbound_guard_tools import CheckInboundChangeTool

        register_tool(CheckInboundChangeTool())
        registered.append("check_inbound_change")
        _tool_categories["check_inbound_change"] = category
        logger.debug("Registered: CheckInboundChangeTool")

        from backend.tools.agent_tools.code_manipulation_tools import CODE_MANIPULATION_TOOLS

        for tool in CODE_MANIPULATION_TOOLS:
            register_tool(tool)
            registered.append(tool.name)
            _tool_categories[tool.name] = category
            logger.debug(f"Registered: {tool.__class__.__name__}")

    except ImportError as e:
        logger.error(f"Failed to import code tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register code tools: {e}")

    return registered


def register_file_operation_tools() -> List[str]:
    """Register the document-processing file tool.

    Only ProcessFileTool is registered: it extracts text from binary documents
    (PDF/DOCX/Excel/images/CSV) via EnhancedFileProcessor — a capability no
    other registered tool provides. ReadFileTool/ListFilesTool in the same
    module are intentionally left UNregistered: they take raw filesystem paths
    and bypass guarded_code_service's repo/lock/protected-file checks. Use the
    guarded read_code/list_code_files tools for source instead.
    """
    global _tool_categories
    registered = []
    category = "files"

    try:
        from backend.tools.agent_tools.file_operation_tools import ProcessFileTool

        register_tool(ProcessFileTool())
        registered.append("process_file")
        _tool_categories["process_file"] = category
        logger.debug("Registered: ProcessFileTool")

    except ImportError as e:
        logger.error(f"Failed to import file operation tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register file operation tools: {e}")

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
        logger.debug("Registered: FetchUrlTool")

        register_tool(WebAnalysisTool())
        registered.append("analyze_website")
        _tool_categories["analyze_website"] = category
        logger.debug("Registered: WebAnalysisTool")

        register_tool(WebSearchTool())
        registered.append("web_search")
        _tool_categories["web_search"] = category
        logger.debug("Registered: WebSearchTool")

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
        logger.debug("Registered: BrowserNavigateTool")

        register_tool(BrowserClickTool())
        registered.append("browser_click")
        _tool_categories["browser_click"] = category
        logger.debug("Registered: BrowserClickTool")

        register_tool(BrowserFillTool())
        registered.append("browser_fill")
        _tool_categories["browser_fill"] = category
        logger.debug("Registered: BrowserFillTool")

        register_tool(BrowserScreenshotTool())
        registered.append("browser_screenshot")
        _tool_categories["browser_screenshot"] = category
        logger.debug("Registered: BrowserScreenshotTool")

        register_tool(BrowserExtractTool())
        registered.append("browser_extract")
        _tool_categories["browser_extract"] = category
        logger.debug("Registered: BrowserExtractTool")

        register_tool(BrowserWaitTool())
        registered.append("browser_wait")
        _tool_categories["browser_wait"] = category
        logger.debug("Registered: BrowserWaitTool")

        register_tool(BrowserExecuteJSTool())
        registered.append("browser_execute_js")
        _tool_categories["browser_execute_js"] = category
        logger.debug("Registered: BrowserExecuteJSTool")

        register_tool(BrowserGetHTMLTool())
        registered.append("browser_get_html")
        _tool_categories["browser_get_html"] = category
        logger.debug("Registered: BrowserGetHTMLTool")

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
        logger.debug("Registered: FileWatchTool")

        register_tool(FileBulkOperationTool())
        registered.append("file_bulk_operation")
        _tool_categories["file_bulk_operation"] = category
        logger.debug("Registered: FileBulkOperationTool")

        register_tool(AppLaunchTool())
        registered.append("app_launch")
        _tool_categories["app_launch"] = category
        logger.debug("Registered: AppLaunchTool")

        register_tool(AppListTool())
        registered.append("app_list")
        _tool_categories["app_list"] = category
        logger.debug("Registered: AppListTool")

        register_tool(AppFocusTool())
        registered.append("app_focus")
        _tool_categories["app_focus"] = category
        logger.debug("Registered: AppFocusTool")

        register_tool(GUIClickTool())
        registered.append("gui_click")
        _tool_categories["gui_click"] = category
        logger.debug("Registered: GUIClickTool")

        register_tool(GUITypeTool())
        registered.append("gui_type")
        _tool_categories["gui_type"] = category
        logger.debug("Registered: GUITypeTool")

        register_tool(GUIHotkeyTool())
        registered.append("gui_hotkey")
        _tool_categories["gui_hotkey"] = category
        logger.debug("Registered: GUIHotkeyTool")

        register_tool(GUIScreenshotTool())
        registered.append("gui_screenshot")
        _tool_categories["gui_screenshot"] = category
        logger.debug("Registered: GUIScreenshotTool")

        register_tool(GUILocateImageTool())
        registered.append("gui_locate_image")
        _tool_categories["gui_locate_image"] = category
        logger.debug("Registered: GUILocateImageTool")

        register_tool(ClipboardGetTool())
        registered.append("clipboard_get")
        _tool_categories["clipboard_get"] = category
        logger.debug("Registered: ClipboardGetTool")

        register_tool(ClipboardSetTool())
        registered.append("clipboard_set")
        _tool_categories["clipboard_set"] = category
        logger.debug("Registered: ClipboardSetTool")

        register_tool(NotificationSendTool())
        registered.append("notification_send")
        _tool_categories["notification_send"] = category
        logger.debug("Registered: NotificationSendTool")

    except ImportError as e:
        logger.error(f"Failed to import desktop tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register desktop tools: {e}")

    return registered


def register_mcp_tools() -> List[str]:
    """Register MCP (Model Context Protocol) meta-tools.

    Per-server tools (mcp__<server>__<tool>) are registered dynamically by
    backend.tools.mcp_tools.install_proxy_sync when servers connect.
    """
    global _tool_categories
    registered = []
    category = "mcp"

    try:
        from backend.tools.mcp_tools import META_TOOL_CLASSES

        for cls in META_TOOL_CLASSES:
            tool = cls()
            register_tool(tool)
            registered.append(tool.name)
            _tool_categories[tool.name] = category
        logger.info(f"Registered MCP meta-tools: {', '.join(registered)}")
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
        from backend.tools.system_tools import FindFilesTool, SystemCommandTool

        register_tool(SystemCommandTool())
        registered.append("system_command")
        _tool_categories["system_command"] = category
        logger.debug("Registered: SystemCommandTool")

        register_tool(FindFilesTool())
        registered.append("find_files")
        _tool_categories["find_files"] = category
        logger.debug("Registered: FindFilesTool")

    except ImportError as e:
        logger.error(f"Failed to import system tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register system tools: {e}")

    return registered


def register_records_tools() -> List[str]:
    """Register the lookup over Guaardvark's own records (projects, clients, ...)"""
    global _tool_categories
    registered = []
    category = "records"

    try:
        from backend.tools.records_tools import FindRecordsTool

        register_tool(FindRecordsTool())
        registered.append("find_records")
        _tool_categories["find_records"] = category
        logger.debug("Registered: FindRecordsTool")

    except ImportError as e:
        logger.error(f"Failed to import records tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register records tools: {e}")

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
            UpdateMemoryTool,
            DeleteMemoryTool
        )

        register_tool(SaveMemoryTool())
        registered.append("save_memory")
        _tool_categories["save_memory"] = category
        logger.debug("Registered: SaveMemoryTool")

        register_tool(SearchMemoryTool())
        registered.append("search_memory")
        _tool_categories["search_memory"] = category
        logger.debug("Registered: SearchMemoryTool")
        
        register_tool(UpdateMemoryTool())
        registered.append("update_memory")
        _tool_categories["update_memory"] = category
        logger.debug("Registered: UpdateMemoryTool")

        register_tool(DeleteMemoryTool())
        registered.append("delete_memory")
        _tool_categories["delete_memory"] = category
        logger.debug("Registered: DeleteMemoryTool")

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
        from backend.tools.rag_tools import IndexDocumentTool, KnowledgeSearchTool

        register_tool(KnowledgeSearchTool())
        registered.append("search_knowledge_base")
        _tool_categories["search_knowledge_base"] = category
        logger.debug("Registered: KnowledgeSearchTool")

        register_tool(IndexDocumentTool())
        registered.append("index_document")
        _tool_categories["index_document"] = category
        logger.debug("Registered: IndexDocumentTool")

        # Navigation surface. Search alone is a lookup primitive: without these there
        # is no way to ask what the corpus contains, what a document is made of, or
        # what the collection covers overall. Same category as search, so the MCP
        # policy gate treats them identically.
        from backend.tools.knowledge_tools import KNOWLEDGE_NAV_TOOLS

        for tool_cls in KNOWLEDGE_NAV_TOOLS:
            tool = tool_cls()
            register_tool(tool)
            registered.append(tool.name)
            _tool_categories[tool.name] = category
            logger.debug(f"Registered: {tool_cls.__name__}")

    except ImportError as e:
        logger.error(f"Failed to import RAG tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register RAG tools: {e}")

    return registered


def register_media_tools() -> List[str]:
    """Register media player control tools.

    Registered on Linux only: they use D-Bus MPRIS2, amixer and a Linux VLC, so
    elsewhere chat and MCP clients would see four tools that can only refuse.
    """
    global _tool_categories
    registered = []
    category = "media"

    from backend.utils.platform import media_player_available, os_name
    if not media_player_available():
        logger.info("Media player tools not registered: they need Linux (D-Bus MPRIS2, amixer, "
                    "VLC) and this machine runs %s", os_name())
        return registered

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
        logger.debug("Registered: MediaPlayTool")

        register_tool(MediaControlTool())
        registered.append("media_control")
        _tool_categories["media_control"] = category
        logger.debug("Registered: MediaControlTool")

        register_tool(MediaVolumeTool())
        registered.append("media_volume")
        _tool_categories["media_volume"] = category
        logger.debug("Registered: MediaVolumeTool")

        register_tool(MediaStatusTool())
        registered.append("media_status")
        _tool_categories["media_status"] = category
        logger.debug("Registered: MediaStatusTool")

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
        _tool_categories["generate_image"] = "image"
        logger.debug("Registered: ImageGeneratorTool")
    except Exception as e:
        logger.warning(f"Failed to register image tools: {e}")

    try:
        from backend.tools.image_tools import GenerationStatusTool
        register_tool(GenerationStatusTool())
        registered.append("get_generation_status")
        _tool_categories["get_generation_status"] = "image"
        logger.debug("Registered: GenerationStatusTool")
    except Exception as e:
        logger.warning(f"Failed to register generation status tool: {e}")

    try:
        from backend.tools.image_tools import AnimationGeneratorTool
        register_tool(AnimationGeneratorTool())
        registered.append("generate_animation")
        _tool_categories["generate_animation"] = "image"
        logger.debug("Registered: AnimationGeneratorTool")
    except Exception as e:
        logger.warning(f"Failed to register animation tools: {e}")

    try:
        from backend.tools.image_tools import VideoGeneratorTool
        register_tool(VideoGeneratorTool())
        registered.append("generate_video")
        _tool_categories["generate_video"] = "image"
        logger.debug("Registered: VideoGeneratorTool")
    except Exception as e:
        logger.warning(f"Failed to register video tool: {e}")

    try:
        from backend.tools.video_pipeline_tools import MusicVideoTool
        register_tool(MusicVideoTool())
        registered.append("generate_music_video")
        _tool_categories["generate_music_video"] = "image"
        logger.debug("Registered: MusicVideoTool")
    except Exception as e:
        logger.warning(f"Failed to register music-video tool: {e}")

    try:
        from backend.tools.video_pipeline_tools import FilmCrewTool
        register_tool(FilmCrewTool())
        registered.append("start_film_crew")
        _tool_categories["start_film_crew"] = "image"
        logger.debug("Registered: FilmCrewTool")
    except Exception as e:
        logger.warning(f"Failed to register film-crew tool: {e}")

    try:
        from backend.tools.image_tools import EditImageTool
        register_tool(EditImageTool())
        registered.append("edit_image")
        _tool_categories["edit_image"] = "image"
        logger.debug("Registered: EditImageTool")
    except Exception as e:
        logger.warning(f"Failed to register edit_image tool: {e}")

    # generate_identity is consent-gated in the tool itself (a recorded consent
    # for the reference image, asked for on a chat card); its likeness was
    # verified on 2026-09-19 once the PuLID node patch landed.
    _photo_tools = [
        ("RemoveBackgroundTool", "remove_background"),
        ("InpaintImageTool", "inpaint_image"),
        ("OutpaintImageTool", "outpaint_image"),
        ("GenerateIdentityTool", "generate_identity"),
    ]
    for _cls_name, _tool_name in _photo_tools:
        try:
            from backend.tools import image_tools as _image_tools
            register_tool(getattr(_image_tools, _cls_name)())
            registered.append(_tool_name)
            _tool_categories[_tool_name] = "image"
            logger.debug("Registered: %s", _cls_name)
        except Exception as e:
            logger.warning("Failed to register %s: %s", _tool_name, e)

    return registered


def register_audio_tools() -> List[str]:
    """Register speech everywhere and music for MCP clients.

    Chat offers speech only when a message asks to hear something
    (unified_chat_engine.user_wants_speech). Music is registered only in the MCP
    server process, so chat does not make songs; agents connected over MCP get
    the Audio Foundry that the Studio's Audio page uses.
    """
    from backend.utils.backend_http import in_mcp_process

    registered = []
    tools = [("GenerateSpeechTool", "generate_speech")]
    if in_mcp_process():
        tools.insert(0, ("GenerateMusicTool", "generate_music"))
    for _cls_name, _tool_name in tools:
        try:
            from backend.tools import audio_tools as _audio_tools
            register_tool(getattr(_audio_tools, _cls_name)())
            registered.append(_tool_name)
            _tool_categories[_tool_name] = "audio"
            logger.debug("Registered: %s", _cls_name)
        except Exception as e:
            logger.warning("Failed to register %s: %s", _tool_name, e)
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
        logger.debug("Registered sandboxed: ExecutePythonTool")
    except ImportError as e:
        logger.warning(f"Could not import code execution tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register test execution tools: {e}")

    # JavaScript/Node executor (is_dangerous + requires_approval gate it).
    try:
        from backend.tools.agent_tools.code_execution_tools import ExecuteJavaScriptTool
        register_tool(ExecuteJavaScriptTool())
        registered.append("execute_javascript")
        _tool_categories["execute_javascript"] = category
        logger.debug("Registered: ExecuteJavaScriptTool")
    except ImportError as e:
        logger.warning(f"Could not import JavaScript execution tool: {e}")
    except Exception as e:
        logger.error(f"Failed to register JavaScript execution tool: {e}")
    return registered


def register_outreach_tools() -> List[str]:
    """Register social outreach tools (chat-callable wrappers around the
    /api/social-outreach/* endpoints)."""
    global _tool_categories
    registered = []
    category = "outreach"

    try:
        from backend.tools.outreach_tools import (
            OutreachStatusTool,
            OutreachListQueueTool,
            OutreachDraftPostTool,
            OutreachApproveDraftTool,
            OutreachRejectDraftTool,
            OutreachRunPassTool,
            OutreachExecuteIntentTool,
        )

        for cls in (
            OutreachStatusTool,
            OutreachListQueueTool,
            OutreachDraftPostTool,
            OutreachApproveDraftTool,
            OutreachRejectDraftTool,
            OutreachRunPassTool,
            OutreachExecuteIntentTool,
        ):
            tool = cls()
            register_tool(tool)
            registered.append(tool.name)
            _tool_categories[tool.name] = category
            logger.debug(f"Registered: {cls.__name__}")

    except ImportError as e:
        logger.error(f"Failed to import outreach tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register outreach tools: {e}")

    return registered


def register_connection_tools() -> List[str]:
    """Register the agent handle on the Connections publish queue."""
    global _tool_categories
    registered = []
    category = "connections"

    try:
        from backend.tools.connection_tools import RequestPublishTool

        tool = RequestPublishTool()
        register_tool(tool)
        registered.append(tool.name)
        _tool_categories[tool.name] = category
        logger.debug("Registered: RequestPublishTool")
    except ImportError as e:
        logger.error(f"Failed to import connection tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register connection tools: {e}")

    return registered


def register_workstation_tools() -> List[str]:
    """Register chat tools that wrap mapper / GPU / logs / swarm / self-improvement."""
    global _tool_categories
    registered = []
    category = "workstation"
    try:
        from backend.tools.workstation_tools import WORKSTATION_TOOLS

        for tool in WORKSTATION_TOOLS:
            register_tool(tool)
            registered.append(tool.name)
            _tool_categories[tool.name] = category
            logger.debug(f"Registered: {tool.__class__.__name__}")
    except ImportError as e:
        logger.error(f"Failed to import workstation tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register workstation tools: {e}")
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
            AgentReadTextFromElementTool,
            AgentStatusTool,
        )

        register_tool(AgentModeStartTool())
        registered.append("agent_mode_start")
        _tool_categories["agent_mode_start"] = category
        logger.debug("Registered: AgentModeStartTool")

        register_tool(AgentModeStopTool())
        registered.append("agent_mode_stop")
        _tool_categories["agent_mode_stop"] = category
        logger.debug("Registered: AgentModeStopTool")

        register_tool(AgentTaskExecuteTool())
        registered.append("agent_task_execute")
        _tool_categories["agent_task_execute"] = category
        logger.debug("Registered: AgentTaskExecuteTool")

        register_tool(AgentScreenCaptureTool())
        registered.append("agent_screen_capture")
        _tool_categories["agent_screen_capture"] = category
        logger.debug("Registered: AgentScreenCaptureTool")

        register_tool(AgentReadTextFromElementTool())
        registered.append("agent_read_text_from_element")
        _tool_categories["agent_read_text_from_element"] = category
        logger.debug("Registered: AgentReadTextFromElementTool")

        register_tool(AgentStatusTool())
        registered.append("agent_status")
        _tool_categories["agent_status"] = category
        logger.debug("Registered: AgentStatusTool")

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

    logger.debug("Initializing tool registry")

    # Register all tool categories
    _registered_tools.extend(register_content_tools())
    _registered_tools.extend(register_generation_tools())
    _registered_tools.extend(register_code_tools())
    _registered_tools.extend(register_file_operation_tools())
    _registered_tools.extend(register_web_tools())
    _registered_tools.extend(register_browser_tools())
    _registered_tools.extend(register_desktop_tools())
    _registered_tools.extend(register_mcp_tools())
    _registered_tools.extend(register_system_tools())
    _registered_tools.extend(register_memory_tools())
    _registered_tools.extend(register_records_tools())
    _registered_tools.extend(register_rag_tools())
    _registered_tools.extend(register_media_tools())
    _registered_tools.extend(register_image_tools())
    _registered_tools.extend(register_audio_tools())
    _registered_tools.extend(register_test_execution_tools())
    _registered_tools.extend(register_agent_control_tools())
    _registered_tools.extend(register_outreach_tools())
    _registered_tools.extend(register_connection_tools())
    _registered_tools.extend(register_workstation_tools())

    # Get the registry for status reporting
    registry = get_tool_registry()

    logger.info(f"TOOL REGISTRY INITIALIZED: {len(registry)} tools registered")
    logger.debug(f"Registered tools: {', '.join(_registered_tools)}")

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
