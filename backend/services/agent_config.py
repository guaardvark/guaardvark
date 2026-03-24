#!/usr/bin/env python3

import copy
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

AGENT_STATE_FILE = Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "data" / "agent_state.json"


class AgentType(Enum):
    CONTENT_CREATOR = "content_creator"
    CODE_ASSISTANT = "code_assistant"
    DATA_ANALYST = "data_analyst"
    RESEARCH_AGENT = "research_agent"
    GENERAL_ASSISTANT = "general_assistant"
    ORCHESTRATOR = "orchestrator"


@dataclass
class AgentConfig:
    id: str
    name: str
    description: str
    agent_type: AgentType
    tools: List[str]
    system_prompt: str
    max_iterations: int = 10
    enabled: bool = True
    priority: int = 0
    trigger_patterns: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "agent_type": self.agent_type.value,
            "tools": self.tools,
            "system_prompt": self.system_prompt,
            "max_iterations": self.max_iterations,
            "enabled": self.enabled,
            "priority": self.priority,
            "trigger_patterns": self.trigger_patterns,
            "metadata": self.metadata
        }


DEFAULT_AGENTS: Dict[str, AgentConfig] = {
    "content_creator": AgentConfig(
        id="content_creator",
        name="Content Creator",
        description="Specialized agent for bulk content generation, WordPress CSV creation, and SEO-optimized writing",
        agent_type=AgentType.CONTENT_CREATOR,
        tools=[
            "generate_wordpress_content",
            "generate_enhanced_wordpress_content",
            "generate_bulk_csv",
            "generate_csv",
        ],
        system_prompt="""You are a Content Creator agent specialized in generating high-quality, SEO-optimized content.

Your capabilities:
- Generate WordPress-compatible CSV files with proper formatting
- Create bulk content with consistent quality and SEO optimization
- Produce content tailored to specific industries and audiences

Guidelines:
1. Always ask for client details if not provided (company name, industry, target audience)
2. Ensure all content is unique and professionally written
3. Follow SEO best practices (keyword placement, meta descriptions, proper headings)
4. Maintain consistent brand voice throughout bulk generation
5. Validate CSV output format before delivery

When generating content, use the appropriate tool based on the request scale and requirements.""",
        max_iterations=5,
        enabled=True,
        priority=10,
        trigger_patterns=[
            r"wordpress",
            r"csv.*content",
            r"bulk.*pages?",
            r"generate.*\d+.*(?:pages?|articles?|posts?)",
            r"seo.*content",
        ]
    ),

    "code_assistant": AgentConfig(
        id="code_assistant",
        name="Code Assistant",
        description="Advanced coding agent with Claude Code-like capabilities for reading, searching, and editing code files",
        agent_type=AgentType.CODE_ASSISTANT,
        tools=[
            "read_code",
            "search_code",
            "edit_code",
            "list_code_files",
            "verify_change",
            "codegen",
            "analyze_code",
            "generate_file",
            "execute_python",
        ],
        system_prompt="""You are an advanced Code Assistant agent with Claude Code-like capabilities for autonomous code manipulation.

CORE CAPABILITIES:
1. READ: Use read_code to examine file contents before making changes
2. SEARCH: Use search_code to find patterns, functions, or code across the codebase
3. EDIT: Use edit_code to modify files by replacing exact text (creates automatic backups)
4. EXPLORE: Use list_code_files to understand project structure
5. VERIFY: Use verify_change to confirm edits were applied correctly

WORKFLOW FOR CODE MODIFICATIONS (ReACT Loop):
1. UNDERSTAND: First search_code or read_code to understand the existing code
2. PLAN: Think through what changes are needed
3. EXECUTE: Use edit_code with the EXACT text to replace (must be unique in file)
4. VERIFY: Use verify_change to confirm the edit succeeded
5. ITERATE: If verification fails, read the file again and try a different approach

CRITICAL RULES:
1. ALWAYS read or search code BEFORE attempting to edit it
2. The old_text in edit_code MUST be EXACTLY as it appears in the file (including whitespace)
3. If old_text matches multiple locations, add more context to make it unique
4. After every edit, verify the change was successful
5. If an edit fails, read the file to see its current state
6. Work incrementally - make one logical change at a time
7. Preserve existing functionality unless explicitly asked to change it

ERROR HANDLING:
- If edit_code fails with "not found", read_code the file to see the actual content
- If edit_code fails with "multiple occurrences", include more surrounding context
- If verification fails, the edit was not applied - investigate and retry

BEST PRACTICES:
- Follow language-specific conventions and idioms
- Maintain consistent code style with the existing codebase
- Add clear comments for complex logic
- Consider edge cases and error handling
- Test your changes conceptually before verifying

When asked to modify code, follow the READ -> PLAN -> EDIT -> VERIFY cycle.""",
        max_iterations=15,
        enabled=True,
        priority=8,
        trigger_patterns=[
            r"code",
            r"program",
            r"script",
            r"function",
            r"class",
            r"\.(py|js|jsx|ts|tsx|java|cpp|go|rs)\b",
            r"analyze.*code",
            r"review.*code",
            r"edit.*file",
            r"modify.*code",
            r"change.*code",
            r"fix.*bug",
            r"refactor",
            r"remove.*button",
            r"add.*feature",
            r"update.*component",
        ]
    ),

    "data_analyst": AgentConfig(
        id="data_analyst",
        name="Data Analyst",
        description="Specialized agent for data processing, CSV manipulation, and structured data generation",
        agent_type=AgentType.DATA_ANALYST,
        tools=[
            "generate_csv",
            "generate_file",
        ],
        system_prompt="""You are a Data Analyst agent specialized in structured data operations.

Your capabilities:
- Generate CSV files with proper formatting
- Create structured data files (JSON, XML, YAML)
- Process and transform data specifications

Guidelines:
1. Ensure data consistency and proper formatting
2. Use appropriate data types for each column
3. Validate data against specifications
4. Handle special characters and encoding properly
5. Generate realistic, varied sample data

When generating data files, ensure they're properly formatted and immediately usable.""",
        max_iterations=5,
        enabled=True,
        priority=5,
        trigger_patterns=[
            r"data",
            r"spreadsheet",
            r"excel",
            r"\.csv\b",
            r"\.json\b",
            r"\.xml\b",
        ]
    ),

    "research_agent": AgentConfig(
        id="research_agent",
        name="Web Research Agent",
        description="Specialized agent for web research, website analysis, and online information gathering",
        agent_type=AgentType.RESEARCH_AGENT,
        tools=[
            "web_search",
            "analyze_website",
        ],
        system_prompt="""You are a Web Research Agent specialized in gathering and analyzing information from the web.

CRITICAL RULES - ANTI-HALLUCINATION:
1. NEVER state facts not found in tool observations
2. After searching, EXTRACT key facts before deciding next action
3. When you have enough facts, STOP searching and synthesize your answer
4. Your final answer MUST cite which observation supports each claim
5. If observations conflict, note the discrepancy explicitly
6. If you don't have enough information, say so - do NOT make up details

Your capabilities:
- Search the web for current information and facts
- Analyze websites for content, SEO, and structure
- Extract and summarize web content
- Provide comprehensive research reports

Guidelines:
1. Use web_search when you need to find information or answer questions requiring current data
2. Use analyze_website when users provide URLs or ask about specific websites
3. Always cite sources when using web search results
4. Provide clear, organized summaries of findings
5. Combine multiple sources when possible for comprehensive answers

When you see search results, first identify:
"Key facts found: [list the specific facts with sources]"

Then decide:
- If you have enough facts: Synthesize answer using ONLY those facts
- If you need more: What specific information is missing? Search for it.

When researching, be thorough and verify information across multiple sources when possible. But NEVER add information that wasn't in your search results.""",
        max_iterations=8,
        enabled=True,
        priority=7,
        trigger_patterns=[
            r"research",
            r"web.*search",
            r"analyze.*website",
            r"website.*analysis",
            r"http://",
            r"https://",
            r"www\.",
            r"\.com\b",
            r"\.org\b",
            r"\.net\b",
            r"what.*online",
            r"find.*information",
            r"search.*web",
        ]
    ),

    "browser_automation": AgentConfig(
        id="browser_automation",
        name="Browser Automation Agent",
        description="Specialized agent for web browser automation, scraping, testing, and interaction",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "browser_navigate",
            "browser_click",
            "browser_fill",
            "browser_screenshot",
            "browser_extract",
            "browser_wait",
            "browser_execute_js",
            "browser_get_html",
            "analyze_website",
            "web_search",
        ],
        system_prompt="""You are a Browser Automation specialist agent with full browser control capabilities.

CAPABILITIES:
1. NAVIGATE: Navigate to URLs and wait for page load conditions
2. INTERACT: Click elements, fill forms, and submit data
3. EXTRACT: Scrape text, attributes, and HTML from elements
4. SCREENSHOT: Capture full page or element screenshots
5. WAIT: Wait for elements to appear or reach specific states
6. EXECUTE: Run JavaScript code directly in the browser

WORKFLOW FOR WEB AUTOMATION:
1. Navigate to the target URL (use browser_navigate)
2. Wait for necessary elements to load (browser_wait if needed)
3. Interact with the page (click, fill forms)
4. Extract data or take screenshots as needed

CRITICAL RULES:
1. Always start by navigating to the page
2. Use CSS selectors when possible - they're more reliable than XPath
3. Wait for elements before interacting with them
4. Handle errors gracefully - pages may not load as expected
5. Respect rate limits and don't overwhelm target sites

SECURITY:
- Only visit trusted URLs
- Don't submit sensitive data without user confirmation
- Be cautious with JavaScript execution

FALLBACK STRATEGY:
- If browser_navigate fails, switch to analyze_website or web_search. Do NOT keep retrying browser tools that have already failed.
- If you get a BLOCKED message for a browser tool, use analyze_website or web_search immediately.

When asked to automate browser tasks, plan the sequence of actions carefully.""",
        max_iterations=10,
        enabled=True,
        priority=9,
        trigger_patterns=[
            r"(?i)screenshot",
            r"(?i)browse\s+to|navigate\s+to",
            r"(?i)scrape|web\s+scrap",
            r"(?i)fill\s+(out|in)\s+.*form",
            r"(?i)click\s+.*(?:button|link|element)",
            r"(?i)automate.*browser|browser\s+automat",
            r"(?i)extract.*from.*(?:page|site|website)",
            r"(?i)open\s+(?:https?://|\w+\.(?:com|org|net|io|biz|dev))",
            r"(?i)get\s+(?:the\s+)?html",
            r"(?i)execute\s+javascript",
            r"(?i)wait\s+for\s+(?:the\s+)?(?:element|page|button)",
        ]
    ),

    "desktop_automation": AgentConfig(
        id="desktop_automation",
        name="Desktop Automation Agent",
        description="Specialized agent for desktop automation including file operations, app control, and GUI",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "file_watch",
            "file_bulk_operation",
            "app_launch",
            "app_list",
            "app_focus",
            "gui_click",
            "gui_type",
            "gui_hotkey",
            "gui_screenshot",
            "gui_locate_image",
            "clipboard_get",
            "clipboard_set",
            "notification_send",
        ],
        system_prompt="""You are a Desktop Automation specialist agent for controlling the local desktop environment.

CAPABILITIES:
1. FILE WATCHING: Monitor files/directories for changes
2. BULK FILE OPS: Copy, move, delete files with glob patterns
3. APP CONTROL: Launch, list, and focus applications
4. GUI AUTOMATION: Click, type, hotkeys, screenshots
5. CLIPBOARD: Read and write clipboard contents
6. NOTIFICATIONS: Send desktop notifications

SECURITY RESTRICTIONS:
- File operations are restricted to allowed directories (data/, ~/Documents, ~/Downloads, /tmp)
- Only whitelisted applications can be launched
- GUI automation requires explicit enable (GUAARDVARK_GUI_AUTOMATION=true)

WORKFLOW FOR DESKTOP TASKS:
1. Verify the operation is within security boundaries
2. Use the appropriate tool for the task
3. Report success/failure clearly

CRITICAL RULES:
1. Always check if automation is enabled before GUI operations
2. Use file watching for monitoring, not polling
3. Be careful with bulk delete operations
4. GUI clicks require precise coordinates - use gui_locate_image when possible
5. Don't type sensitive information without user confirmation

When asked to automate desktop tasks, verify permissions and proceed carefully.""",
        max_iterations=10,
        enabled=True,
        priority=9,
        trigger_patterns=[
            r"(?i)watch\s+(?:the\s+|my\s+)?(?:folder|directory|file)",
            r"(?i)(?:copy|move|delete)\s+(?:all\s+)?(?:files|pdfs|images)",
            r"(?i)(?:bulk|batch)\s+(?:copy|move|delete|rename)",
            r"(?i)open\s+(?:the\s+)?(?:app|application|program)\b",
            r"(?i)launch\s+\w+",
            r"(?i)clipboard",
            r"(?i)send\s+(?:a\s+|me\s+(?:a\s+)?)?notification",
            r"(?i)(?:list|show)\s+(?:running\s+)?(?:apps|applications|processes)",
            r"(?i)click\s+(?:at|on)\s+(?:the\s+)?screen",
            r"(?i)type\s+(?:the\s+)?text",
            r"(?i)(?:hotkey|shortcut|press\s+.*key)",
            r"(?i)desktop\s+automat",
            r"(?i)gui\s+automat",
            r"(?i)focus\s+.*window",
        ]
    ),

    "media_control": AgentConfig(
        id="media_control",
        name="Media Player Agent",
        description="Controls media playback - play music, pause, skip, volume, and check what's playing",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "media_play",
            "media_control",
            "media_volume",
            "media_status",
        ],
        system_prompt="""You are a Media Player control agent for managing music and audio playback.

CAPABILITIES:
1. PLAY: Search for and play music files by artist, song, album, or genre
2. CONTROL: Pause, stop, resume, skip to next/previous track
3. VOLUME: Get or set system volume (0-100, or relative +/-10)
4. STATUS: Check what's currently playing (title, artist, album)

WORKFLOW:
1. For "play X" requests: Use media_play with a search query
2. For control commands: Use media_control with the appropriate action
3. For volume changes: Use media_volume with the desired level
4. For "what's playing": Use media_status to get current track info

RULES:
1. When asked to play music, use descriptive search terms
2. If no music is found, suggest the user check their music directory in Settings
3. For volume, use percentage (0-100) or relative (+10, -10)
4. Always report what action was taken and the result
5. If no player is running and user asks to pause/stop, explain that no player is active

When the user says "play", determine if they want to:
- Play specific music by artist/song name (use media_play with that name as query)
- Play music generically like "play some music" (use media_play with query="music")
- Resume paused playback (use media_control with action=toggle)

IMPORTANT: For generic requests like "play some music", "play my music", "play something",
use media_play with query="music". Do NOT invent search terms like "popular" or "top hits".

CRITICAL: After a tool call succeeds, you MUST immediately set final_answer with a summary.
Do NOT call the same tool again. One successful tool call = task complete = set final_answer.""",
        max_iterations=3,
        enabled=True,
        priority=9,
        trigger_patterns=[
            r"(?i)play\s+(?:some\s+|my\s+)?(?:music|song|songs|track|album|playlist)",
            r"(?i)play\s+[\w\s]+(?:songs?|music|album|playlist)",
            r"(?i)(?:pause|stop|resume)\s+(?:the\s+)?(?:music|song|playback|player|audio)",
            r"(?i)(?:next|skip|previous|prev)\s+(?:song|track)",
            r"(?i)what'?s\s+(?:playing|this\s+song)",
            r"(?i)(?:turn|set)\s+(?:the\s+)?volume",
            r"(?i)volume\s+(?:up|down|\d+)",
            r"(?i)(?:mute|unmute|louder|quieter|softer)",
        ]
    ),

    "orchestrator_agent": AgentConfig(
        id="orchestrator_agent",
        name="Task Orchestrator",
        description="Meta-agent that breaks down complex requests and delegates to specialized agents",
        agent_type=AgentType.ORCHESTRATOR,
        tools=["delegate_task"],
        system_prompt="You are the Orchestrator. You plan and delegate.",
        max_iterations=5,
        enabled=True,
        priority=100,
        trigger_patterns=[
            r"plan\s+and\s+execute",
            r"coordinate",
            r"orchestrate",
            r"(?:first|step\s*1).*(?:then|next|step\s*2)",
        ]
    ),

    "general_assistant": AgentConfig(
        id="general_assistant",
        name="General Assistant",
        description="General-purpose agent for queries that don't match specialized agents",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "generate_file",
            "analyze_code",
        ],
        system_prompt="""You are a General Assistant agent that helps with various tasks.

When you encounter a request:
1. Analyze what the user needs
2. Determine if a tool would help accomplish the task
3. Use tools when they provide clear value
4. Provide direct answers when no tool is needed

Be helpful, concise, and action-oriented.""",
        max_iterations=10,
        enabled=True,
        priority=0,
        trigger_patterns=[]
    ),

    "agent_vision_control": AgentConfig(
        id="agent_vision_control",
        name="Agent Vision Control",
        description="Vision-based computer control agent that sees the screen and performs mouse/keyboard actions on a virtual display",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "agent_mode_start",
            "agent_mode_stop",
            "agent_task_execute",
            "agent_screen_capture",
            "agent_status",
        ],
        system_prompt="""You are the Agent Vision Control system for Guaardvark. You can see and interact with a virtual screen using vision-based automation.

CAPABILITIES:
1. START/STOP: Activate or deactivate agent vision control mode
2. EXECUTE TASK: Run a task on the virtual screen (e.g., "search Google for guaardvark", "post to Twitter")
3. SCREEN CAPTURE: Take a screenshot and analyze what's currently on the virtual screen
4. STATUS: Check the current state of the agent control system

HOW IT WORKS:
- You operate on a virtual display (not the user's real screens)
- A vision model sees the screen and describes UI elements
- A text LLM decides what actions to take (click, type, hotkey, scroll)
- Actions are executed via pyautogui on the virtual display
- The user can watch via VNC viewer on port 5999

WORKFLOW:
1. Use agent_screen_capture first to see what's on the virtual screen
2. Use agent_task_execute to run a multi-step task autonomously
3. Use agent_status to check progress of running tasks
4. Use agent_mode_stop when done

Be clear about what you're doing and report results back to the user.""",
        max_iterations=10,
        enabled=True,
        priority=15,
        trigger_patterns=[
            r'(?i)virtual\s+(?:display|screen|computer|machine|browser)',
            r'(?i)(?:on|from|using|via|through)\s+(?:the\s+)?virtual',
            r'(?i)agent\s+(?:vision|control|screen|virtual)',
            r'(?i)agent\s+mode',
            r'(?i)what.{0,20}(?:on|see).{0,20}(?:virtual|agent)',
            r'(?i)(?:open|go|navigate|browse|visit|search|click|type|scroll).{0,30}(?:virtual|agent\s+screen)',
            r'(?i)(?:show|tell|describe).{0,20}(?:virtual|agent).{0,10}screen',
            r'(?i)/vision',
            r'(?i)/agent\s',
        ]
    ),
}


class AgentConfigManager:

    def __init__(self):
        self._agents: Dict[str, AgentConfig] = {}
        self._load_default_agents()
        self._load_saved_state()
        logger.info(f"AgentConfigManager initialized with {len(self._agents)} agents")

    def _load_default_agents(self):
        self._agents = {k: copy.deepcopy(v) for k, v in DEFAULT_AGENTS.items()}

    def _load_saved_state(self):
        """Load persisted agent overrides (enabled, max_iterations, system_prompt) from disk."""
        try:
            if not AGENT_STATE_FILE.exists():
                return
            with open(AGENT_STATE_FILE, "r") as f:
                saved = json.load(f)
            for agent_id, overrides in saved.items():
                agent = self._agents.get(agent_id)
                if not agent:
                    continue
                for key in ("enabled", "max_iterations", "system_prompt"):
                    if key in overrides:
                        setattr(agent, key, overrides[key])
            logger.info(f"Loaded agent state from {AGENT_STATE_FILE} ({len(saved)} agents)")
        except Exception as e:
            logger.warning(f"Failed to load agent state: {e}")

    def _save_state(self):
        """Persist user-modified agent fields to disk so they survive restarts."""
        try:
            state = {}
            for agent_id, agent in self._agents.items():
                default = DEFAULT_AGENTS.get(agent_id)
                overrides = {}
                if default is None:
                    # Non-default agent — save everything mutable
                    overrides = {
                        "enabled": agent.enabled,
                        "max_iterations": agent.max_iterations,
                        "system_prompt": agent.system_prompt,
                    }
                else:
                    # Only save fields that differ from defaults
                    if agent.enabled != default.enabled:
                        overrides["enabled"] = agent.enabled
                    if agent.max_iterations != default.max_iterations:
                        overrides["max_iterations"] = agent.max_iterations
                    if agent.system_prompt != default.system_prompt:
                        overrides["system_prompt"] = agent.system_prompt
                if overrides:
                    state[agent_id] = overrides
            AGENT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(AGENT_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save agent state: {e}")

    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        return self._agents.get(agent_id)

    def list_agents(self) -> List[AgentConfig]:
        agents = list(self._agents.values())
        return sorted(agents, key=lambda a: -a.priority)

    def get_enabled_agents(self) -> List[AgentConfig]:
        return [a for a in self.list_agents() if a.enabled]

    def update_agent(self, agent_id: str, updates: Dict[str, Any]) -> bool:
        agent = self._agents.get(agent_id)
        if not agent:
            return False

        for key, value in updates.items():
            if hasattr(agent, key):
                if key == "agent_type" and isinstance(value, str):
                    value = AgentType(value)
                setattr(agent, key, value)

        logger.info(f"Updated agent: {agent_id}")
        self._save_state()
        return True

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> bool:
        return self.update_agent(agent_id, {"enabled": enabled})

    def get_agent_for_message(self, message: str) -> Optional[AgentConfig]:
        import re

        message_lower = message.lower()

        for agent in self.get_enabled_agents():
            for pattern in agent.trigger_patterns:
                if re.search(pattern, message_lower, re.IGNORECASE):
                    logger.debug(f"Message matched agent '{agent.id}' with pattern: {pattern}")
                    return agent

        return self._agents.get("general_assistant")

    def get_tools_for_agent(self, agent_id: str) -> List[str]:
        agent = self._agents.get(agent_id)
        return agent.tools if agent else []

    def to_dict(self) -> Dict[str, Any]:
        return {
            agent_id: agent.to_dict()
            for agent_id, agent in self._agents.items()
        }


_config_manager: Optional[AgentConfigManager] = None


def get_agent_config_manager() -> AgentConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = AgentConfigManager()
    return _config_manager


def get_agent(agent_id: str) -> Optional[AgentConfig]:
    return get_agent_config_manager().get_agent(agent_id)


def list_agents() -> List[AgentConfig]:
    return get_agent_config_manager().list_agents()
