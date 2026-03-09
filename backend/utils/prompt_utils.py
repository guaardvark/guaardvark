# backend/utils/prompt_utils.py
# Version 6.0: SIMPLIFIED - RulesPage is the single source of truth
# Removed hardcoded prompts in favor of database rules system

import logging
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Fallback QA prompt template (minimal)
FALLBACK_QA_PROMPT_TEXT = "{context_str}\n\n{query_str}"

_TIME_QUERY_HINTS = (
    "what time",
    "current time",
    "time is it",
    "timestamp",
    "utc",
    "timezone",
    "time zone",
    "what day",
    "day of week",
    "what date",
    "today's date",
    "todays date",
)

_TIME_QUERY_NEGATIVE_HINTS = (
    "time complexity",
    "runtime complexity",
    "space complexity",
    "big o",
    "big-o",
    "compile time",
    "build time",
)


def should_include_time_context(user_message: str) -> bool:
    """
    Only include system time context when the user is actually asking about time/date/day.

    Prevents greetings like \"hello\" from getting a time block injected.
    """
    if not user_message:
        return False

    msg = user_message.strip().lower()
    if not msg:
        return False

    # Avoid polluting prompts for programming questions mentioning \"time\" in a technical sense.
    if any(hint in msg for hint in _TIME_QUERY_NEGATIVE_HINTS):
        return False

    # Strong signals.
    if any(hint in msg for hint in _TIME_QUERY_HINTS):
        return True

    # Weaker signals: require question framing.
    if "time" in msg and any(q in msg for q in ("?", "what", "current", "now", "please")):
        return True

    # Date/day requests.
    if any(word in msg for word in ("date", "day", "weekday")) and "today" in msg:
        return True

    return False


def get_system_time_context():
    """Get current system time information for LLM context."""
    now = datetime.now().astimezone()
    now_utc = datetime.now(timezone.utc)
    
    time_context = f"""[CURRENT SYSTEM TIME]
Local Time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({str(now.astimezone().tzinfo)})
UTC Time: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}
Day: {now.strftime('%A')}
Timestamp: {int(now.timestamp())}

"""
    return time_context


def enhance_message_with_time(user_message):
    """Conditionally add current time context to user message."""
    if should_include_time_context(user_message):
        return get_system_time_context() + user_message
    return user_message


# Legacy compatibility functions (redirect to RulesPage system)
def get_prompt_template_text(
    prompt_name: str,
    project_id: Optional[int] = None,
    model_name: Optional[str] = None,
    web_search_enabled: Optional[bool] = None,
) -> str:
    """Legacy function - RulesPage should be used instead."""
    logger.warning(f"get_prompt_template_text() called for '{prompt_name}' - consider using RulesPage instead")
    return "{rules_str}"


def get_prompt_text_by_name(
    name: str, 
    project_id: Optional[int] = None, 
    model_name: Optional[str] = None,
    web_search_enabled: Optional[bool] = None,
) -> Optional[str]:
    """Legacy function - RulesPage should be used instead."""
    logger.warning(f"get_prompt_text_by_name() called for '{name}' - consider using RulesPage instead")
    return "{rules_str}"
