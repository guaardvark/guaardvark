# backend/rule_utils.py
# Version: Added get_active_command_rule function.
# Based on version with get_active_system_prompt.

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import List, Optional, Tuple

from sqlalchemy import case, func

from backend.utils import prompt_utils

try:
    from backend.models import Rule, db
except ImportError:
    logging.critical("CRITICAL Failed to import db/Rule model in rule_utils.")
    db = None
    Rule = None

logger = logging.getLogger(__name__)

# Simple cache utilities for frequently accessed rule lookups.
_formatted_rules_cache_size = 64

_system_prompt_cache_size = 32
_qa_template_cache_size = 32


def _model_matches(model_name: Optional[str], target_models: list[str]) -> bool:
    """Return True if model_name matches target list (case-insensitive, substring)."""
    if not target_models:
        return False
    targets = [t.lower() for t in target_models]
    if "__all__" in targets:
        return True
    if not model_name:
        return False
    lower = model_name.lower()
    for t in targets:
        if t == lower or t in lower or lower in t:
            return True
    return False


def _rules_cache_key(
    levels: Tuple[str, ...], model_name: Optional[str], reference_id: Optional[str]
) -> Tuple:
    """Return a cache key including the newest updated_at timestamp for the queried rules."""
    if not db or not Rule:
        return (levels, model_name, reference_id, None)

    query = db.session.query(func.max(Rule.updated_at)).filter(
        Rule.is_active == True,
        Rule.level.in_(levels),
    )
    if reference_id:
        query = query.filter(Rule.reference_id == reference_id)
    # model filter handled after fetching

    latest = query.scalar()
    return (levels, model_name, reference_id, latest.isoformat() if latest else None)


@lru_cache(maxsize=_formatted_rules_cache_size)
def _cached_formatted_rules(key: Tuple) -> str:
    """Internal cached implementation for ``get_formatted_rules``."""
    levels, model_name, reference_id, _ = key
    if not db or not Rule:
        return ""
    query = db.session.query(Rule).filter(
        Rule.is_active == True,
        Rule.level.in_(levels),
    )
    if reference_id:
        query = query.filter(Rule.reference_id == reference_id)
    level_priority = case(
        (Rule.level == "system", 0), (Rule.level == "learned", 1), else_=99
    )
    fetched_rules = query.order_by(level_priority, Rule.created_at).all()
    if model_name:
        fetched_rules = [
            r for r in fetched_rules if _model_matches(model_name, r.target_models)
        ]
    if not fetched_rules:
        return ""
    formatted_rules_list = ["\n--- Applicable Rules & Guidelines ---"]
    current_level_header = None
    for rule_item in fetched_rules:
        if rule_item.level != current_level_header:
            formatted_rules_list.append(f"\n## {rule_item.level.capitalize()} Rules:")
            current_level_header = rule_item.level
        formatted_rules_list.append(f"- {rule_item.rule_text.strip()}")
    return "\n".join(formatted_rules_list) + "\n"


def _system_prompt_cache_key(prompt_rule_name: str, model_name: Optional[str]) -> Tuple:
    if not db or not Rule:
        return (prompt_rule_name, model_name, None)
    query = db.session.query(func.max(Rule.updated_at)).filter(
        Rule.name == prompt_rule_name,
        Rule.level == "SYSTEM",
        Rule.type.in_(["PROMPT_TEMPLATE", "SYSTEM_PROMPT"]),
        Rule.is_active == True,
    )
    # model-specific filtering done after query
    latest = query.scalar()
    return (prompt_rule_name, model_name, latest.isoformat() if latest is not None else None)


@lru_cache(maxsize=_system_prompt_cache_size)
def _cached_active_system_prompt(key: Tuple) -> Tuple[Optional[str], Optional[int]]:
    if not Rule or not db:
        return None, None
    prompt_rule_name, model_name, _ = key
    query = db.session.query(Rule).filter(
        Rule.name == prompt_rule_name,
        Rule.level == "SYSTEM",
        Rule.type.in_(["PROMPT_TEMPLATE", "SYSTEM_PROMPT"]),
        Rule.is_active == True,
    )
    rules = query.order_by(Rule.updated_at.desc()).all()

    if model_name:
        lower = model_name.lower()
        for rule in rules:
            if any(t.lower() == lower for t in rule.target_models):
                return rule.rule_text, rule.id

    for rule in rules:
        if any(t.lower() == "__all__" for t in rule.target_models):
            return rule.rule_text, rule.id

    return None, None


def _qa_template_cache_key(model_name: Optional[str]) -> Tuple:
    if not db or not Rule:
        return (model_name, None)
    query = db.session.query(func.max(Rule.updated_at)).filter(
        Rule.name == "qa_default",
        Rule.level == "SYSTEM",
        Rule.type == "QA_TEMPLATE",
        Rule.is_active == True,
    )
    # model-specific filtering handled after query
    latest = query.scalar()
    return (model_name, latest.isoformat() if latest is not None else None)


@lru_cache(maxsize=_qa_template_cache_size)
def _cached_active_qa_template(
    key: Tuple, fallback_text: str
) -> Tuple[str, Optional[int]]:
    if not Rule or not db:
        return fallback_text, None
    model_name, _ = key
    query = db.session.query(Rule).filter(
        Rule.name == "qa_default",
        Rule.level == "SYSTEM",
        Rule.type == "QA_TEMPLATE",
        Rule.is_active == True,
    )
    rules = query.order_by(Rule.updated_at.desc()).all()

    if model_name:
        lower = model_name.lower()
        for rule in rules:
            if any(t.lower() == lower for t in rule.target_models):
                return rule.rule_text, rule.id

    for rule in rules:
        if any(t.lower() == "__all__" for t in rule.target_models):
            return rule.rule_text, rule.id

    return fallback_text, None


def get_formatted_rules(
    levels: List[str] = ["system", "learned"],
    model_name: Optional[str] = None,
    reference_id: Optional[str] = None,
) -> str:
    """Return formatted active rules with basic caching based on update timestamp."""
    if not db or not Rule:
        logger.error("DB or Rule model unavailable in get_formatted_rules.")
        return ""

    levels_tuple: Tuple[str, ...] = tuple(levels)
    key = _rules_cache_key(levels_tuple, model_name, reference_id)
    try:
        result = _cached_formatted_rules(key)
        logger.info(
            "Formatted %d rules for prompt (cached=%s).",
            len(result.splitlines()) - 1,
            key[-1] is not None,
        )
        return result
    except Exception as e:
        logger.error("Database error fetching formatted rules: %s", e, exc_info=True)
        return "[Error fetching rules]\n"


def get_active_system_prompt(
    prompt_rule_name: str, db_session, model_name: Optional[str] = None
) -> tuple[Optional[str], Optional[int]]:
    """
    Get system prompt from database rules (RulesPage) - the single source of truth.
    Integrates web search capability status for rule processing.
    """
    
    if not Rule:
        logger.error("Rule model unavailable in get_active_system_prompt.")
        return None, None
    if not db_session:
        logger.error("Database session not provided.")
        return None, None
        
    logger.debug(f"Fetching system prompt from database: Name='{prompt_rule_name}', Model='{model_name}'")
    
    try:
        # Get web search setting for context (but don't override rules)
        web_search_enabled = False
        try:
            from backend.utils.settings_utils import get_web_access
            web_search_enabled = get_web_access()
            logger.debug(f"Web search setting: {web_search_enabled}")
        except Exception as e:
            logger.warning(f"Failed to get web search setting: {e}")
        
        # Fetch prompt from database rules (RulesPage system)
        key = _system_prompt_cache_key(prompt_rule_name, model_name)
        text, rule_id = _cached_active_system_prompt(key)
        
        if text:
            logger.info(f"Using database rule '{prompt_rule_name}' (ID: {rule_id}) for model '{model_name}'")
            
            # If the rule contains web search placeholders, provide the setting
            if "{web_search_enabled}" in text:
                text = text.replace("{web_search_enabled}", str(web_search_enabled))
            if "{web_access_status}" in text:
                status = "ENABLED" if web_search_enabled else "DISABLED"
                text = text.replace("{web_access_status}", status)
                
            return text, rule_id
        else:
            # Expected state when the global rules toggle is off or when a
            # caller probes optimistically for an optional rule. Callers
            # handle the None fallback — keep this quiet.
            logger.debug(f"System prompt rule '{prompt_rule_name}' not found or not active.")
            return None, None
            
    except Exception as e:
        logger.error(f"DB error fetching system prompt rule '{prompt_rule_name}': {e}", exc_info=True)
        return None, None


def get_active_qa_default_template(
    db_session, model_name: Optional[str] = None
) -> tuple[str, Optional[int]]:
    """
    Get QA template from database rules (RulesPage) - the single source of truth.
    Integrates web search capability status for rule processing.
    """
    
    # Fallback QA template if no database rule found
    fallback_text = getattr(prompt_utils, "FALLBACK_QA_PROMPT_TEXT", "{context_str}\n\n{query_str}")

    if not Rule:
        logger.error("Rule model unavailable in get_active_qa_default_template.")
        return fallback_text, None
    if not db_session:
        logger.error("Database session not provided to get_active_qa_default_template.")
        return fallback_text, None

    logger.debug(f"Fetching qa_default QA_TEMPLATE from database for model '{model_name}'.")
    
    try:
        # Get web search setting for context (but don't override rules)
        web_search_enabled = False
        try:
            from backend.utils.settings_utils import get_web_access
            web_search_enabled = get_web_access()
            logger.debug(f"Web search setting for QA template: {web_search_enabled}")
        except Exception as e:
            logger.warning(f"Failed to get web search setting for QA template: {e}")
        
        # Fetch QA template from database rules
        key = _qa_template_cache_key(model_name)
        text, rule_id = _cached_active_qa_template(key, fallback_text)
        
        if rule_id is not None:
            logger.info(f"Using database qa_default rule ID {rule_id} for model '{model_name or '__ALL__'}'.")
            
            # If the rule contains web search placeholders, provide the setting
            if "{web_search_enabled}" in text:
                text = text.replace("{web_search_enabled}", str(web_search_enabled))
            if "{web_access_status}" in text:
                status = "ENABLED" if web_search_enabled else "DISABLED"
                text = text.replace("{web_access_status}", status)
                
        else:
            logger.warning("No active qa_default rule found; using fallback template.")
            
        return text, rule_id
        
    except Exception as e:
        logger.error(f"DB error fetching qa_default QA_TEMPLATE: {e}", exc_info=True)
        return fallback_text, None


def get_active_command_rule(
    command_label: str, db_session, model_name: Optional[str] = None
) -> Optional[Rule]:
    """
    Fetches a single, active command rule by its command_label.

    It looks for a rule with:
    - The specified `command_label`.
    - `type` = 'COMMAND_RULE'.
    - `is_active` = True.
    - Matches `target_models` if `model_name` is provided.

    Args:
        command_label: The exact command label (e.g., "/createfile") of the rule.
        db_session: The SQLAlchemy session to use for the query.
        model_name: The name of the currently active LLM to filter by target_models.

    Returns:
        The full Rule object if found, or None.
    """
    if not Rule:
        logger.error("Rule model unavailable in get_active_command_rule.")
        return None
    if not db_session:
        logger.error("Database session not provided to get_active_command_rule.")
        return None

    logger.debug(
        f"Attempting to fetch command rule: Label='{command_label}', Type='COMMAND_RULE', Model='{model_name}'"
    )

    try:
        query = db_session.query(Rule).filter(
            Rule.command_label == command_label,
            Rule.type == "COMMAND_RULE",
            Rule.is_active == True,
        )
        rules = query.order_by(Rule.updated_at.desc()).all()

        if model_name:
            lower = model_name.lower()
            for rule in rules:
                if any(t.lower() == lower for t in rule.target_models):
                    logger.info(
                        f"Found active command rule with label: '{command_label}' (ID: {rule.id}) for model '{model_name}'."
                    )
                    return rule

        for rule in rules:
            if any(t.lower() == "__all__" for t in rule.target_models):
                logger.info(
                    f"Using global command rule '{command_label}' (ID: {rule.id})."
                )
                return rule

        logger.warning(
            f"No active command rule found for Label='{command_label}', Type='COMMAND_RULE', Model='{model_name or 'any'}'."
        )
        return None

    except Exception as e:
        logger.error(
            f"Database error fetching command rule for label '{command_label}': {e}",
            exc_info=True,
        )
        return None
