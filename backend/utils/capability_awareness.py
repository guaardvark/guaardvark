# backend/utils/capability_awareness.py
# Version 1.1: Capability awareness and anti-hallucination framework for LLMs
# Makes LLMs aware of their capabilities and teaches honest uncertainty

import hashlib
import logging
import time
from typing import List, Tuple, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Cache for capability prompt sections (avoids regenerating identical prompts)
_capability_cache: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


def _get_cache_key(
    model_name: str, web_search_enabled: bool, rag_enabled: bool, tools: List[str]
) -> str:
    """Generate a cache key for capability prompt section"""
    tools_str = ",".join(sorted(tools)) if tools else ""
    key_str = f"{model_name}:{web_search_enabled}:{rag_enabled}:{tools_str}"
    return hashlib.md5(key_str.encode()).hexdigest()


def _get_cached(key: str) -> Optional[str]:
    """Get cached value if not expired"""
    if key in _capability_cache:
        entry = _capability_cache[key]
        if time.time() - entry["timestamp"] < _CACHE_TTL_SECONDS:
            return entry["value"]
        # Expired, remove from cache
        del _capability_cache[key]
    return None


def _set_cached(key: str, value: str) -> None:
    """Store value in cache with timestamp"""
    _capability_cache[key] = {"value": value, "timestamp": time.time()}


def classify_model_tier(model_name: str) -> str:
    """
    Classify model into capability tiers based on parameter count.

    Args:
        model_name: Name of the model (e.g., 'gemma3:4b', 'llama3.1:70b')

    Returns:
        'small', 'medium', or 'large'
    """
    if not model_name:
        return "medium"  # Default to medium if unknown

    name = model_name.lower()

    # Tier 1: Very small models (need most guidance)
    small_indicators = ["1b", "2b", "3b", "4b", "mini", "tiny", "small", "nano"]
    if any(x in name for x in small_indicators):
        return "small"

    # Tier 2: Medium models (good but need some guidance)
    medium_indicators = ["7b", "8b", "13b", "14b"]
    if any(x in name for x in medium_indicators):
        return "medium"

    # Tier 3: Large models (minimal guidance needed)
    # 30b+, 70b+, etc.
    return "large"


def get_capability_context(
    web_search_enabled: bool,
    rag_enabled: bool,
    tools: List[str] = None,
    tools_with_descriptions: List[dict] = None,
) -> str:
    """
    Generate minimal capability awareness context for LLM.

    This tells the model what it can and cannot do, preventing
    hallucination about capabilities it doesn't have.

    Args:
        web_search_enabled: Whether web search is available
        rag_enabled: Whether RAG/document search is available
        tools: List of available tool names (optional)
        tools_with_descriptions: List of dicts with name, description, category (optional)

    Returns:
        Concise capability context string
    """
    # Type validation
    if tools is not None and not isinstance(tools, list):
        logger.error(f"Expected tools to be a list, got {type(tools).__name__}")
        tools = []

    if tools_with_descriptions is not None and not isinstance(
        tools_with_descriptions, list
    ):
        logger.error(
            f"Expected tools_with_descriptions to be a list, got {type(tools_with_descriptions).__name__}"
        )
        tools_with_descriptions = []

    capabilities = []
    limitations = []

    if web_search_enabled:
        capabilities.append("search the web for current information")
    else:
        limitations.append("I cannot access the internet or real-time data")

    if rag_enabled:
        capabilities.append("search your indexed documents")

    # Use tools_with_descriptions if available for better categorization
    if tools_with_descriptions:
        # Group tools by category using registry data
        by_category = {}
        for tool_info in tools_with_descriptions:
            cat = tool_info.get("category", "other")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(tool_info)

        tool_descriptions = []
        category_labels = {
            "browser": "browser automation",
            "desktop": "desktop automation",
            "mcp": "MCP integration",
            "content": "content generation",
            "generation": "file generation",
            "code": "code tools",
            "web": "web tools",
        }

        for cat, cat_tools in by_category.items():
            label = category_labels.get(cat, cat)
            # Include brief descriptions for clarity
            tool_names = [t.get("name", "") for t in cat_tools[:3]]
            desc = f"{label} ({len(cat_tools)} tools: {', '.join(tool_names)}"
            if len(cat_tools) > 3:
                desc += f", +{len(cat_tools) - 3} more"
            desc += ")"
            tool_descriptions.append(desc)

        if tool_descriptions:
            capabilities.append(
                f"use powerful tools including: {'; '.join(tool_descriptions)}"
            )

    elif tools:
        # Fallback: categorize tools by name pattern
        browser_tools = [t for t in tools if "browser" in t.lower()]
        desktop_tools = [
            t
            for t in tools
            if any(
                x in t.lower()
                for x in ["file_", "app_", "gui_", "clipboard", "notification"]
            )
        ]
        mcp_tools = [t for t in tools if "mcp" in t.lower()]
        other_tools = [
            t for t in tools if t not in browser_tools + desktop_tools + mcp_tools
        ]

        tool_descriptions = []
        if browser_tools:
            tool_descriptions.append(
                f"browser automation ({len(browser_tools)} tools: navigate, screenshot, extract data, etc.)"
            )
        if desktop_tools:
            tool_descriptions.append(
                f"desktop automation ({len(desktop_tools)} tools: file operations, notifications, clipboard, etc.)"
            )
        if mcp_tools:
            tool_descriptions.append(f"MCP integration ({len(mcp_tools)} tools)")
        if other_tools:
            # Show first few other tools
            other_str = ", ".join(other_tools[:3])
            if len(other_tools) > 3:
                other_str += f" and {len(other_tools) - 3} more"
            tool_descriptions.append(other_str)

        if tool_descriptions:
            capabilities.append(
                f"use powerful tools including: {'; '.join(tool_descriptions)}"
            )

    # Core limitations all LLMs have
    limitations.extend(
        [
            "I cannot know lottery numbers, stock prices, sports scores, or weather without web search",
            "I cannot access your local files or systems unless you share them",
        ]
    )

    context_parts = []
    if capabilities:
        context_parts.append(f"I CAN: {'; '.join(capabilities)}")
    if limitations:
        context_parts.append(f"I CANNOT: {'; '.join(limitations)}")

    return ". ".join(context_parts) + "."


def get_honesty_framework(model_name: str) -> str:
    """
    Get anti-hallucination instructions sized for the model.

    Smaller models need simpler, more direct rules.
    Larger models can handle more nuanced guidance.

    Args:
        model_name: Name of the model

    Returns:
        Anti-hallucination instruction string
    """
    tier = classify_model_tier(model_name)

    if tier == "small":
        return """RULES:
- If you don't know, say "I don't know"
- NEVER make up facts or data
- Only reference search results if === WEB SEARCH RESULTS === appears in your context
- When search results exist: extract the answer and respond in 1-2 sentences. Do NOT paste raw results, URLs, or markers to the user
- Be concise. Give direct answers, not data dumps
- Stay on topic"""

    elif tier == "medium":
        return """RULES:
- Say "I don't know" when uncertain
- Only reference search if === WEB SEARCH RESULTS === is in your context
- When search results exist: synthesize into a concise answer. Do NOT paste raw results or URLs
- Be direct and concise"""

    else:
        return """Be accurate, honest, and concise. Synthesize search results into direct answers - never paste raw data or URLs."""


def get_tier_guidance(model_name: str) -> str:
    """
    Get appropriate guidance level for model tier.

    Args:
        model_name: Name of the model

    Returns:
        Tier-specific guidance string (may be empty for large models)
    """
    tier = classify_model_tier(model_name)

    if tier == "small":
        return """IMPORTANT: You are a smaller language model. Be extra careful to:
- Not guess when you don't know
- Ask for clarification if confused
- Keep responses focused and on-topic"""

    elif tier == "medium":
        return "Focus on accuracy over verbosity. Acknowledge uncertainty when present."

    else:
        return ""  # Large models need minimal guidance


def requires_realtime_data(message: str) -> Tuple[bool, str]:
    """
    Check if a query requires real-time data and explain why.

    This helps the system warn the LLM when a query is about
    something it cannot know without external access.

    Args:
        message: User's message

    Returns:
        Tuple of (requires_realtime: bool, reason: str)
    """
    realtime_patterns = {
        "lottery": "Lottery numbers change daily and require live lookup",
        "lotto": "Lotto numbers change daily and require live lookup",
        "powerball": "Powerball numbers require live lookup",
        "mega millions": "Mega Millions numbers require live lookup",
        "stock price": "Stock prices change in real-time",
        "share price": "Share prices change in real-time",
        "weather": "Weather conditions require current data",
        "temperature": "Temperature requires current weather data",
        "forecast": "Forecasts require current weather data",
        "score": "Sports scores require live updates",
        "game result": "Game results require live updates",
        "breaking news": "Breaking news requires real-time sources",
        "happening now": "Current events require real-time sources",
        "right now": "Current data requires real-time access",
        "tonight's": "Tonight's information requires live lookup",
        "today's": "Today's specific data requires current information",
        "this evening": "This evening's data requires live lookup",
        "current price": "Current prices require real-time data",
        "live": "Live information requires real-time access",
        "latest": "Latest information may require real-time lookup",
    }

    msg_lower = message.lower()

    for pattern, reason in realtime_patterns.items():
        if pattern in msg_lower:
            logger.info(f"Real-time data required: '{pattern}' detected - {reason}")
            return True, reason

    return False, ""


def get_realtime_warning(message: str) -> str:
    """
    Generate a warning if the query requires real-time data.

    Args:
        message: User's message

    Returns:
        Warning string if real-time data needed, empty string otherwise
    """
    requires_rt, reason = requires_realtime_data(message)

    if requires_rt:
        return f"NOTE: This query may require real-time data. {reason}. If web search is disabled, acknowledge that you cannot provide this information."

    return ""


def build_capability_prompt_section(
    model_name: str,
    web_search_enabled: bool,
    rag_enabled: bool,
    user_message: str = "",
    tools: List[str] = None,
    tools_with_descriptions: List[dict] = None,
) -> str:
    """
    Build complete capability awareness section for system prompt.

    This is the main function to call from enhanced_chat_api.py.
    It combines all the capability awareness elements into a single
    coherent section.

    Args:
        model_name: Name of the model
        web_search_enabled: Whether web search is available
        rag_enabled: Whether RAG is available
        user_message: User's message (for real-time detection)
        tools: List of available tool names
        tools_with_descriptions: List of dicts with name, description, category (optional)

    Returns:
        Complete capability awareness prompt section
    """
    # Check cache for non-user-message requests (static capability sections)
    # User message triggers real-time detection which is message-specific, so skip cache
    if not user_message:
        cache_key = _get_cache_key(model_name, web_search_enabled, rag_enabled, tools)
        cached = _get_cached(cache_key)
        if cached:
            logger.debug(f"Capability cache hit for model '{model_name}'")
            return cached

    parts = []

    # 1. Honesty framework (most important)
    honesty = get_honesty_framework(model_name)
    if honesty:
        parts.append(honesty)

    # 2. Capability context
    capabilities = get_capability_context(
        web_search_enabled, rag_enabled, tools, tools_with_descriptions
    )
    if capabilities:
        parts.append(capabilities)

    # 3. Model tier guidance (if applicable)
    tier_guidance = get_tier_guidance(model_name)
    if tier_guidance:
        parts.append(tier_guidance)

    # 4. Real-time warning (if applicable)
    if user_message:
        rt_warning = get_realtime_warning(user_message)
        if rt_warning:
            parts.append(rt_warning)
            logger.info(
                f"Capability awareness: Real-time query detected for model '{model_name}' - {rt_warning[:100]}"
            )

    # Log capability awareness application
    logger.info(
        f"Capability awareness applied for model '{model_name}': web_search={web_search_enabled}, rag={rag_enabled}, tools={len(tools) if tools else 0}"
    )

    result = "\n\n".join(parts)

    # Cache the result for non-user-message requests
    if not user_message:
        cache_key = _get_cache_key(model_name, web_search_enabled, rag_enabled, tools)
        _set_cached(cache_key, result)

    return result
