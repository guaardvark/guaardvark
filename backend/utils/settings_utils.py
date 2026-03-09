import logging
import os

from flask import current_app, has_app_context

try:
    from backend.models import Setting, db
except Exception:  # pragma: no cover - optional dependency
    db = None
    Setting = None

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
