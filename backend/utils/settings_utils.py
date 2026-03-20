import logging
import os

from flask import current_app, has_app_context

try:
    from backend.models import Setting, SystemSetting, db
except Exception:  # pragma: no cover - optional dependency
    db = None
    Setting = None
    SystemSetting = None

logger = logging.getLogger(__name__)


def get_web_access() -> bool:
    """Return True if allow_web_search setting is enabled."""
    if not db or not Setting:
        logger.warning("Database models unavailable for get_web_access")
        return False
    allow = False
    try:
        # Only try to access database if we have app context
        if has_app_context():
            setting = db.session.get(Setting, "allow_web_search")
            if setting and setting.value == "true":
                allow = True
        else:
            logger.warning("get_web_access called outside app context - returning False")
            return False
    except Exception as e:
        try:
            if has_app_context() and current_app:
                current_app.logger.error(f"Failed to read web access setting: {e}")
            else:
                logger.error(f"Failed to read web access setting: {e}")
        except RuntimeError:
            # No app context available
            logger.error(f"Failed to read web access setting (no app context): {e}")
    return allow


def get_llm_debug() -> bool:
    """Return True if LLM debug logging is enabled."""
    if not db or not Setting:
        return os.environ.get("GUAARDVARK_LLM_DEBUG", "").lower() == "true"
    try:
        if has_app_context():
            setting = db.session.get(Setting, "llm_debug")
            if setting:
                return setting.value == "true"
        return os.environ.get("GUAARDVARK_LLM_DEBUG", "").lower() == "true"
    except Exception as e:
        logger.error(f"Failed to read llm_debug setting: {e}")
        return os.environ.get("GUAARDVARK_LLM_DEBUG", "").lower() == "true"


# Keys that live in the system_settings table (Claude config).
# All other keys use the settings table.
SYSTEM_SETTING_KEYS = {
    "claude_escalation_mode",
    "claude_monthly_budget",
    "claude_model",
    "claude_token_usage",
}

# Maps DB keys to environment variable names for fallback.
ENV_VAR_MAP = {
    "enhanced_context_enabled": "GUAARDVARK_ENHANCED_CONTEXT",
    "advanced_rag_enabled": "GUAARDVARK_ADVANCED_RAG",
    "rag_debug_enabled": "GUAARDVARK_RAG_DEBUG",
    "claude_escalation_mode": "GUAARDVARK_CLAUDE_ESCALATION_MODE",
    "claude_monthly_budget": "GUAARDVARK_CLAUDE_TOKEN_BUDGET",
    "vision_pipeline_enabled": "GUAARDVARK_VISION_PIPELINE",
    "vision_pipeline_max_fps": "GUAARDVARK_VISION_MAX_FPS",
    "vision_pipeline_quality": "GUAARDVARK_VISION_QUALITY",
    "vision_pipeline_resolution": "GUAARDVARK_VISION_RESOLUTION",
    "vision_pipeline_monitor_model": "GUAARDVARK_VISION_MONITOR_MODEL",
    "vision_pipeline_escalation_model": "GUAARDVARK_VISION_ESCALATION_MODEL",
    "vision_pipeline_auto_select": "GUAARDVARK_VISION_AUTO_SELECT",
}

_BOOL_TRUTHY = {"true", "1", "yes"}


def _cast_value(value: str, cast):
    """Cast a string value to the desired type."""
    if cast is bool:
        return value.lower() in _BOOL_TRUTHY
    return cast(value)


def get_setting(key: str, default=None, cast=str):
    """Read a setting: DB > env var > default.

    Checks the correct table (settings or system_settings) based on key.
    Safe to call outside Flask app context — returns env var or default.
    """
    # 1. Try DB
    if db and has_app_context():
        try:
            model = SystemSetting if key in SYSTEM_SETTING_KEYS else Setting
            if model:
                row = db.session.get(model, key)
                if row and row.value is not None:
                    return _cast_value(row.value, cast) if cast != str else row.value
        except Exception as e:
            logger.warning(f"get_setting({key!r}) DB read failed: {e}")

    # 2. Try env var
    env_name = ENV_VAR_MAP.get(key)
    if env_name:
        env_val = os.environ.get(env_name)
        if env_val is not None:
            try:
                return _cast_value(env_val, cast) if cast != str else env_val
            except (ValueError, TypeError):
                pass

    # 3. Default
    return default


def save_setting(key: str, value: str):
    """Persist a setting to the correct DB table.

    Safe to call outside Flask app context — logs warning and returns.
    """
    if not db or not has_app_context():
        logger.warning(f"save_setting({key!r}) called outside app context — skipped")
        return

    try:
        model = SystemSetting if key in SYSTEM_SETTING_KEYS else Setting
        if not model:
            logger.warning(f"save_setting({key!r}): model not available")
            return
        row = db.session.get(model, key)
        if row:
            row.value = value
        else:
            row = model(key=key, value=value)
            db.session.add(row)
        db.session.commit()
    except Exception as e:
        logger.error(f"save_setting({key!r}) failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
