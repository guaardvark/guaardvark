import logging
import os

from typing import Optional

from flask import current_app, has_app_context

try:
    from backend.models import Setting, SystemSetting, db
except Exception:  # pragma: no cover - optional dependency
    db = None
    Setting = None
    SystemSetting = None

logger = logging.getLogger(__name__)


def get_chat_image_model() -> str:
    """Chat image model for /imagine, generate_image, and edit_image (default: auto)."""
    return get_setting("chat_image_model", default="auto") or "auto"


def get_active_video_model() -> str:
    """Persisted global video model id, or empty to use hardware fallback."""
    return (get_setting("active_video_model", default="") or "").strip()


def get_active_video_model_overrides() -> dict:
    """Optional per-role / per-pipeline video model overrides (empty = inherit)."""
    return {
        "i2v": (get_setting("active_video_model_i2v", default="") or "").strip(),
        "music_video": (get_setting("active_video_model_music_video", default="") or "").strip(),
        "film_crew": (get_setting("active_video_model_film_crew", default="") or "").strip(),
    }


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


def web_access_block_reason(action: str) -> Optional[str]:
    """None when web access (Settings, allow_web_search; off by default) is on;
    otherwise the error to report, naming ``action``.

    Everything that reaches the internet on a person's or a model's behalf
    asks this: the web tools, research tasks, the outreach recon search. In
    the MCP server process, which has no Flask app, the backend is asked.
    """
    disabled = f"Web access is disabled. Enable it in Settings to {action}."
    try:
        if has_app_context():
            return None if get_web_access() else disabled
    except Exception:
        pass
    from backend.utils.backend_http import BackendError, in_mcp_process, request_json
    if in_mcp_process():
        try:
            data = request_json("GET", "/api/settings/web_access").data or {}
        except BackendError as e:
            return f"Could not check whether web access is enabled: {e}"
        return None if data.get("allow_web_search") else disabled
    return disabled


# Last llm_debug value read from the database. Worker threads without an app
# context (the agent brain, Tier 3) use it instead of reading the setting as off.
_llm_debug_seen: Optional[bool] = None


def get_llm_debug() -> bool:
    """Return True if LLM debug logging is enabled."""
    global _llm_debug_seen
    env_value = os.environ.get("GUAARDVARK_LLM_DEBUG", "").lower() == "true"
    if not db or not Setting:
        return env_value
    try:
        if has_app_context():
            setting = db.session.get(Setting, "llm_debug")
            _llm_debug_seen = setting.value == "true" if setting else None
            if setting:
                return _llm_debug_seen
            return env_value
        return _llm_debug_seen if _llm_debug_seen is not None else env_value
    except Exception as e:
        logger.error(f"Failed to read llm_debug setting: {e}")
        return env_value


def get_rules_enabled() -> bool:
    """Return True if the global chat-rules toggle (SettingsPage → A.I. Features
    → Rules) is enabled. When False, chat paths skip RulesPage lookups entirely
    and fall straight to the hardcoded default prompt — the way rules were
    "phased out" without deleting the feature.

    Default: False. Safe to call outside app context — returns False/env-var fallback.
    """
    if not db or not Setting:
        return os.environ.get("GUAARDVARK_RULES_ENABLED", "").lower() in _BOOL_TRUTHY
    try:
        if has_app_context():
            setting = db.session.get(Setting, "rules_enabled")
            if setting and setting.value is not None:
                return setting.value.lower() in _BOOL_TRUTHY
        return os.environ.get("GUAARDVARK_RULES_ENABLED", "").lower() in _BOOL_TRUTHY
    except Exception as e:
        logger.error(f"Failed to read rules_enabled setting: {e}")
        return False


# Keys that live in the system_settings table (Claude config).
# All other keys use the settings table.
SYSTEM_SETTING_KEYS = {
    "claude_escalation_mode",
    "claude_monthly_budget",
    "claude_model",
    "claude_scheduled_sends",
    "claude_token_usage",
}

# Maps DB keys to environment variable names for fallback.
ENV_VAR_MAP = {
    # "on_demand" (default): source files are stored and read by the code tools
    # in place, never embedded; "embed" also puts them in the knowledge index.
    "code_retrieval": "GUAARDVARK_CODE_RETRIEVAL",
    # Off by default: tell the user when chat could not search their documents
    # (unified_chat_engine, RAG_SEARCH_FAILED_NOTE).
    "chat_say_rag_failed": "GUAARDVARK_CHAT_SAY_RAG_FAILED",
    "enhanced_context_enabled": "GUAARDVARK_ENHANCED_CONTEXT",
    "advanced_rag_enabled": "GUAARDVARK_ADVANCED_RAG",
    "claude_escalation_mode": "GUAARDVARK_CLAUDE_ESCALATION_MODE",
    "claude_monthly_budget": "GUAARDVARK_CLAUDE_TOKEN_BUDGET",
    "claude_scheduled_sends": "GUAARDVARK_CLAUDE_SCHEDULED",
    "vision_pipeline_enabled": "GUAARDVARK_VISION_PIPELINE",
    "vision_pipeline_max_fps": "GUAARDVARK_VISION_MAX_FPS",
    "vision_pipeline_quality": "GUAARDVARK_VISION_QUALITY",
    "vision_pipeline_resolution": "GUAARDVARK_VISION_RESOLUTION",
    "vision_pipeline_monitor_model": "GUAARDVARK_VISION_MONITOR_MODEL",
    "vision_pipeline_escalation_model": "GUAARDVARK_VISION_ESCALATION_MODEL",
    "vision_pipeline_auto_select": "GUAARDVARK_VISION_AUTO_SELECT",
    "eye_ranking": "GUAARDVARK_EYE_RANKING",
    "servo_correction": "GUAARDVARK_SERVO_CORRECTION",
    "gpu_quality_tier": "GUAARDVARK_GPU_QUALITY_TIER",
    "gpu_eviction_grace": "GUAARDVARK_GPU_EVICTION_GRACE",
    "gpu_idle_timeout": "GUAARDVARK_GPU_IDLE_TIMEOUT",
    "agent_routing_enabled": "AGENT_ROUTING_ENABLED",
    "log_agent_actions": "LOG_AGENT_ACTIONS",
    # Media stack (stills / cast LoRA train base / max quality) — Ollama-selector style
    "media_stills_model": "GUAARDVARK_STILLS_MODEL",
    "media_cast_train_base": "GUAARDVARK_CAST_TRAIN_BASE",
    "media_max_quality_model": "GUAARDVARK_MAX_QUALITY_MODEL",
    "confine_tool_paths": "GUAARDVARK_CONFINE_TOOL_PATHS",
    # The same variable git hooks read, so one line in .env sets both sides.
    "inbound_guard_mode": "GUAARDVARK_INBOUND_GUARD",
    # Per-decision off/shadow/on overrides (decision_settings); the rows are
    # decision_mode.<id>, and this variable wins over them where it is set.
    "decision_modes": "GUAARDVARK_DECISION_MODES",
    # Job order (get_job_priority_enabled); off unless set.
    "job_priority_enabled": "GUAARDVARK_JOB_PRIORITY",
}

_BOOL_TRUTHY = {"true", "1", "yes"}

_SECRET_KEY_SUFFIXES = ("_key", "_token", "_secret")


def is_secret_key(key: str) -> bool:
    """True for setting keys whose value is a credential.

    Suffix-matched on purpose: claude_token_usage is a counter, not a token.
    """
    lowered = (key or "").lower()
    return lowered.endswith(_SECRET_KEY_SUFFIXES) or "password" in lowered


def redact_exception(exc: BaseException, *keys: str) -> str:
    """Exception text that is safe to log while handling *keys*.

    A failed DBAPI statement stringifies with its bound parameters attached
    ("[parameters: {'value': 'sk-live-...'}]"), so logging a write error for a
    credential key writes the credential to the log. For those keys only the
    exception type is reported; the type is what tells the operator whether the
    database was down or the statement was wrong.
    """
    if any(is_secret_key(key) for key in keys):
        return f"{type(exc).__name__} (details withheld: secret setting)"
    return str(exc)


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
            logger.warning("get_setting(%r) DB read failed: %s", key, redact_exception(e, key))

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
        logger.error("save_setting(%r) failed: %s", key, redact_exception(e, key))
        try:
            db.session.rollback()
        except Exception:
            pass


# Tools read this from worker threads that have no app context, so the value
# is kept for the process: loaded at startup, updated when the setting is saved.
_confine_tool_paths: Optional[bool] = None


def get_confine_tool_paths() -> bool:
    """True when file-reading tools (system_command, codegen) are limited to the
    project folder and GUAARDVARK_ALLOWED_PATHS. Off by default."""
    global _confine_tool_paths
    if has_app_context() or _confine_tool_paths is None:
        _confine_tool_paths = bool(get_setting("confine_tool_paths", default=False, cast=bool))
    return _confine_tool_paths


def set_confine_tool_paths(enabled: bool) -> None:
    global _confine_tool_paths
    save_setting("confine_tool_paths", "true" if enabled else "false")
    _confine_tool_paths = bool(enabled)


# Minutes an image model stays loaded after a batch, waiting for the next one.
# 0 (the default) unloads it as the batch ends. Read by the batch worker thread,
# which has no app context, so it is cached like confine_tool_paths.
IMAGE_KEEP_LOADED_MAX_MINUTES = 240
_image_keep_loaded_minutes: Optional[int] = None


def _clamp_keep_minutes(value) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(IMAGE_KEEP_LOADED_MAX_MINUTES, minutes))


def get_image_keep_loaded_minutes() -> int:
    global _image_keep_loaded_minutes
    if has_app_context() or _image_keep_loaded_minutes is None:
        _image_keep_loaded_minutes = _clamp_keep_minutes(
            get_setting("image_keep_loaded_minutes", default=0)
        )
    return _image_keep_loaded_minutes


def set_image_keep_loaded_minutes(minutes) -> int:
    global _image_keep_loaded_minutes
    minutes = _clamp_keep_minutes(minutes)
    save_setting("image_keep_loaded_minutes", str(minutes))
    _image_keep_loaded_minutes = minutes
    return minutes


# Job order: whether the job arbiter (services/job_arbiter.py) ranks waiting
# work by class, by the person's moves and by waiting time. Off (the default)
# keeps every queue's own order exactly. GPU jobs ask it from worker threads
# with no app context and from Celery processes, so each process keeps the
# value for JOB_PRIORITY_CACHE_S; a change made in Settings reaches the other
# processes within that time and this one at once.
JOB_PRIORITY_CACHE_S = 30.0
_job_priority_seen: dict = {"value": None, "at": 0.0}


def _read_job_priority() -> bool:
    if db and has_app_context():
        return bool(get_setting("job_priority_enabled", default=False, cast=bool))
    from backend.celery_beat_gates import _read_setting

    raw = _read_setting("job_priority_enabled", None)
    if raw is None:
        raw = os.environ.get(ENV_VAR_MAP["job_priority_enabled"], "")
    return raw.strip().lower() in _BOOL_TRUTHY


def get_job_priority_enabled() -> bool:
    import time

    now = time.monotonic()
    seen = _job_priority_seen
    if seen["value"] is not None and now - seen["at"] < JOB_PRIORITY_CACHE_S:
        return seen["value"]
    try:
        value = _read_job_priority()
    except Exception as e:  # an unreadable setting keeps today's order
        logger.warning("job_priority_enabled unreadable (%s); treating it as off", e)
        value = False
    seen.update(value=bool(value), at=now)
    return seen["value"]


def set_job_priority_enabled(enabled: bool) -> bool:
    """Store the job order setting. Needs an app context (a route)."""
    import time

    save_setting("job_priority_enabled", "true" if enabled else "false")
    # Read back: save_setting logs a failed write instead of raising.
    stored = bool(get_setting("job_priority_enabled", default=False, cast=bool))
    _job_priority_seen.update(value=stored, at=time.monotonic())
    return stored
