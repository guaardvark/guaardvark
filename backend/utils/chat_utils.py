# backend/utils/chat_utils.py
# Shared chat utilities - consolidated from chat_api.py
# Contains functions and constants used across multiple chat modules

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import time

logger = logging.getLogger(__name__)

# Constants
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
USER_BEHAVIOR_LOG_PATH = os.path.join(REPO_ROOT, "data", "user_behavior_log.jsonl")
GLOBAL_DEFAULT_SYSTEM_PROMPT_RULE_NAME = "global_default_chat_system_prompt"
QA_DEFAULT_RULE_NAME = "qa_default"

DEFAULT_FALLBACK_SYSTEM_PROMPT = """You are a helpful AI assistant.
Please respond to the user's query.
If you have access to relevant documents for the query, use them to inform your answer.
Otherwise, use web search to get current information."""

# Vision model detection patterns (for fallback when API unavailable)
VISION_MODEL_PATTERNS = [
    "vision",
    "llava",
    "gpt-4",
    "gpt4",
    "gpt-4o",
    "qwen.*vl",
    "minicpm-v",
    "moondream",
    "bakllava",
    "llama.*vision",
    "granite.*vision",
    "gemma.*vision",
    "cogvlm",
    "internvl",
    "phi.*vision",
    "deepseek.*vl",
    "pixtral",
    "molmo",
]

# Cache for dynamic model detection (5 minute cache)
_vision_models_cache = {"models": [], "last_updated": 0, "cache_ttl": 300}  # 5 minutes


def _get_available_ollama_models() -> List[Dict]:
    """Get available models from Ollama API with caching."""
    try:
        from backend.api.model_api import get_available_ollama_models

        return get_available_ollama_models()
    except ImportError:
        logger.warning("Could not import get_available_ollama_models, using fallback")
        return []


def _check_model_metadata_for_vision(model_name: str) -> bool:
    """Check Ollama model metadata for vision capability indicators."""
    try:
        import requests

        base_url = "http://localhost:11434"
        try:
            from backend.config import OLLAMA_BASE_URL

            base_url = OLLAMA_BASE_URL
        except (ImportError, AttributeError):
            pass
        resp = requests.post(
            f"{base_url}/api/show", json={"name": model_name}, timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            modelfile = data.get("modelfile", "").lower()
            # Check for vision-related architecture indicators
            if any(
                ind in modelfile
                for ind in ["projector", "clip", "vision_tower", "image_encoder"]
            ):
                return True
    except Exception:
        pass
    return False


def _update_vision_models_cache() -> None:
    """Update the cached list of vision models from Ollama API."""
    current_time = time.time()

    # Check if cache is still valid
    if (current_time - _vision_models_cache["last_updated"]) < _vision_models_cache[
        "cache_ttl"
    ]:
        return

    try:
        logger.debug("Updating vision models cache from Ollama API")
        models_data = _get_available_ollama_models()

        if isinstance(models_data, list):
            # Extract model names and detect vision capabilities
            vision_models = []
            for model in models_data:
                if isinstance(model, dict):
                    model_name = model.get("name", "")
                    if not model_name:
                        continue
                    # Pattern-based detection first (fast)
                    if _is_vision_capable_by_name(model_name):
                        vision_models.append(model_name)
                    else:
                        # Metadata-based detection for models not matching patterns
                        if _check_model_metadata_for_vision(model_name):
                            vision_models.append(model_name)
                            logger.info(
                                f"Detected vision model via metadata: {model_name}"
                            )

            _vision_models_cache["models"] = vision_models
            _vision_models_cache["last_updated"] = current_time
            logger.debug(
                f"Updated vision models cache with {len(vision_models)} models: {vision_models}"
            )
        else:
            logger.warning(
                "Failed to get models from Ollama API, keeping existing cache"
            )

    except Exception as e:
        logger.warning(f"Error updating vision models cache: {e}")


def _is_vision_capable_by_name(model_name: str) -> bool:
    """Check if a model supports vision based on its name patterns."""
    if not model_name:
        return False

    lower_name = model_name.lower()

    # Check against known vision model patterns
    for pattern in VISION_MODEL_PATTERNS:
        if re.search(pattern, lower_name):
            return True

    return False


def is_vision_model(model_name: str) -> bool:
    """Return True if the provided model name is vision capable.

    Uses dynamic detection from Ollama API with pattern-based fallback.
    """
    if not model_name:
        return False

    # Update cache if needed
    _update_vision_models_cache()

    # Check if model is in cached vision models list
    if _vision_models_cache["models"]:
        lower_name = model_name.lower()
        for vision_model in _vision_models_cache["models"]:
            if lower_name == vision_model.lower() or lower_name in vision_model.lower():
                logger.debug(
                    f"Model '{model_name}' detected as vision-capable from cache"
                )
                return True

    # Fallback to pattern-based detection
    result = _is_vision_capable_by_name(model_name)
    if result:
        logger.debug(
            f"Model '{model_name}' detected as vision-capable by pattern matching"
        )

    return result


def get_available_vision_models() -> List[str]:
    """Get list of currently available vision models from Ollama."""
    _update_vision_models_cache()
    return _vision_models_cache["models"].copy()


def clear_vision_models_cache() -> None:
    """Clear the vision models cache to force refresh."""
    _vision_models_cache["models"] = []
    _vision_models_cache["last_updated"] = 0
    logger.debug("Vision models cache cleared")


def contains_image_data(msg: object) -> bool:
    """Heuristically determine if the message includes image data."""
    if isinstance(msg, (bytes, bytearray)):
        return True
    if isinstance(msg, str):
        stripped = msg.strip()
        if stripped.startswith("data:image/"):
            return True
    if isinstance(msg, dict):
        for key, val in msg.items():
            if (
                key.lower().startswith("image")
                and isinstance(val, str)
                and val.strip().startswith("data:image/")
            ):
                return True
    return False


def append_user_behavior_log(
    session_id: str,
    message: str,
    role: str,
    system_reply: str | None = None,
    feedback: dict | None = None,
    detected_style: str | None = None,
    correction: str | None = None,
    persona_snapshot: dict | None = None,
) -> None:
    """Append a user interaction entry to the flat file log."""
    # Check if behavior learning is enabled
    try:
        from backend.models import Setting, db

        if db and Setting:
            setting = db.session.get(Setting, "behavior_learning_enabled")
            learning_enabled = setting.value == "true" if setting else False
        else:
            learning_enabled = False
    except Exception as e:
        logger.error(f"Failed to read behavior learning setting: {e}")
        learning_enabled = False

    if not learning_enabled:
        return  # Skip logging if behavior learning is disabled

    try:
        os.makedirs(os.path.dirname(USER_BEHAVIOR_LOG_PATH), exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "role": role,
            "message": message,
            "system_reply": system_reply,
            "feedback": feedback,
            "detected_style": detected_style,
            "correction": correction,
            "active_persona_snapshot": persona_snapshot,
        }
        with open(USER_BEHAVIOR_LOG_PATH, "a", encoding="utf-8") as log_f:
            log_f.write(json.dumps(entry) + "\n")
    except Exception as log_err:
        logger.error("Failed to log user behavior: %s", log_err)


def get_or_create_session(session_id: str):
    """Get or create a chat session."""
    try:
        from backend.models import LLMSession, db
        from sqlalchemy.exc import SQLAlchemyError

        if not db or not LLMSession:
            raise RuntimeError("DB/LLMSession unavailable.")

        session = db.session.get(LLMSession, session_id)
        if not session:
            logger.info(f"Creating new chat session: {session_id}")
            session = LLMSession(
                id=session_id, user="default"
            )  # Consider linking to an actual user ID if available
            try:
                db.session.add(session)
                db.session.commit()
                logger.info(f"Committed new session: {session_id}")
            except SQLAlchemyError as e:
                from backend.utils.db_utils import safe_db_rollback

                safe_db_rollback(f"session creation for {session_id}")
                logger.error(
                    f"DB error creating session {session_id}: {e}", exc_info=True
                )
                raise
        else:
            logger.debug(f"Found existing session: {session_id}")
        return session
    except ImportError as e:
        logger.error(f"Failed to import dependencies for session management: {e}")
        raise RuntimeError("Session management dependencies unavailable")


def cleanup_old_sessions(max_age_days: int = 30, max_sessions: int = 1000) -> dict:
    """Clean up old chat sessions and messages to prevent memory leaks.

    Args:
        max_age_days: Delete sessions older than this many days
        max_sessions: Maximum number of sessions to keep (keeps most recent)

    Returns:
        Dictionary with cleanup statistics
    """
    try:
        from backend.models import LLMSession, LLMMessage, db

        if not db or not LLMSession or not LLMMessage:
            logger.error("DB/Models unavailable for session cleanup")
            return {"error": "Database unavailable"}

        cutoff_date = datetime.now() - timedelta(days=max_age_days)

        # Count total sessions before cleanup
        total_sessions_before = db.session.query(LLMSession).count()
        total_messages_before = db.session.query(LLMMessage).count()

        # Find sessions to delete by age
        old_sessions = (
            db.session.query(LLMSession)
            .filter(LLMSession.created_at < cutoff_date)
            .all()
        )

        # Delete messages for old sessions
        old_session_ids = [s.id for s in old_sessions]
        if old_session_ids:
            messages_deleted = (
                db.session.query(LLMMessage)
                .filter(LLMMessage.session_id.in_(old_session_ids))
                .delete(synchronize_session=False)
            )
        else:
            messages_deleted = 0

        # Delete old sessions
        sessions_deleted_by_age = len(old_sessions)
        if old_sessions:
            db.session.query(LLMSession).filter(
                LLMSession.id.in_(old_session_ids)
            ).delete(synchronize_session=False)

        # If we still have too many sessions, delete the oldest ones
        remaining_sessions = total_sessions_before - sessions_deleted_by_age
        sessions_deleted_by_count = 0

        if remaining_sessions > max_sessions:
            excess_count = remaining_sessions - max_sessions
            oldest_sessions = (
                db.session.query(LLMSession)
                .order_by(LLMSession.created_at.asc())
                .limit(excess_count)
                .all()
            )

            if oldest_sessions:
                oldest_session_ids = [s.id for s in oldest_sessions]

                # Delete messages for excess sessions
                db.session.query(LLMMessage).filter(
                    LLMMessage.session_id.in_(oldest_session_ids)
                ).delete(synchronize_session=False)

                # Delete excess sessions
                db.session.query(LLMSession).filter(
                    LLMSession.id.in_(oldest_session_ids)
                ).delete(synchronize_session=False)

                sessions_deleted_by_count = len(oldest_sessions)

        db.session.commit()

        total_sessions_deleted = sessions_deleted_by_age + sessions_deleted_by_count
        total_messages_deleted = messages_deleted

        return {
            "sessions_deleted_by_age": sessions_deleted_by_age,
            "sessions_deleted_by_count": sessions_deleted_by_count,
            "total_sessions_deleted": total_sessions_deleted,
            "total_messages_deleted": total_messages_deleted,
            "remaining_sessions": total_sessions_before - total_sessions_deleted,
            "remaining_messages": total_messages_before - total_messages_deleted,
        }

    except Exception as e:
        try:
            from backend.utils.db_utils import safe_db_rollback

            safe_db_rollback("session cleanup")
        except ImportError:
            logger.warning("Could not import db_utils for rollback")
        logger.error(f"Error during session cleanup: {e}", exc_info=True)
        return {"error": str(e)}


def cleanup_user_behavior_log(max_size_mb: int = 100, max_age_days: int = 90) -> dict:
    """Clean up user behavior log file to prevent unlimited growth.

    Args:
        max_size_mb: Maximum log file size in MB
        max_age_days: Delete entries older than this many days

    Returns:
        Dictionary with cleanup statistics
    """
    if not os.path.exists(USER_BEHAVIOR_LOG_PATH):
        return {"message": "User behavior log file does not exist"}

    try:
        # Check file size
        file_size_mb = os.path.getsize(USER_BEHAVIOR_LOG_PATH) / (1024 * 1024)

        if file_size_mb <= max_size_mb:
            return {
                "message": f"Log file size ({file_size_mb:.2f} MB) is within limits"
            }

        # Read all entries
        cutoff_date = datetime.now() - timedelta(days=max_age_days)
        entries_kept = []
        entries_removed = 0

        with open(USER_BEHAVIOR_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    entry = json.loads(line)
                    timestamp_str = entry.get("timestamp", "")

                    # Parse timestamp
                    if timestamp_str:
                        entry_time = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )

                        # Keep entries newer than cutoff
                        if entry_time >= cutoff_date:
                            entries_kept.append(line)
                        else:
                            entries_removed += 1
                    else:
                        # Keep entries without timestamps (should not happen but be safe)
                        entries_kept.append(line)

                except json.JSONDecodeError:
                    # Skip malformed entries
                    entries_removed += 1
                    continue

        # Write back the kept entries
        with open(USER_BEHAVIOR_LOG_PATH, "w", encoding="utf-8") as f:
            for entry in entries_kept:
                f.write(entry + "\n")

        new_size_mb = os.path.getsize(USER_BEHAVIOR_LOG_PATH) / (1024 * 1024)

        return {
            "original_size_mb": round(file_size_mb, 2),
            "new_size_mb": round(new_size_mb, 2),
            "entries_kept": len(entries_kept),
            "entries_removed": entries_removed,
            "cleanup_reason": f"File size exceeded {max_size_mb} MB limit",
        }

    except Exception as e:
        logger.error(f"Error during user behavior log cleanup: {e}", exc_info=True)
        return {"error": str(e)}


def load_chat_history(
    session_id: str, msg_limit: int, max_tokens_for_history: int
) -> list:
    """Load chat history for a session with token limit."""
    try:
        from backend.models import LLMMessage, db
        from llama_index.core.llms import ChatMessage, MessageRole

        if not db or not LLMMessage:
            logger.error("DB/LLMMessage unavailable for load_chat_history.")
            return []

        # Get tokenizer function
        tokenizer_fn = _get_tokenizer_fn()

        # Load messages in chronological order
        db_msgs = (
            db.session.query(LLMMessage)
            .filter(LLMMessage.session_id == session_id)
            .order_by(LLMMessage.timestamp.asc())
            .limit(msg_limit)
            .all()
        )

        history: list = []
        valid_roles = {mr.value for mr in MessageRole}
        current_token_count = 0

        for db_msg in db_msgs:
            # Validate role
            role_str = db_msg.role.lower() if db_msg.role else "user"
            if role_str not in valid_roles:
                logger.debug(f"Skipping message with invalid role: {role_str}")
                continue

            # Convert to MessageRole enum
            if role_str == "user":
                role = MessageRole.USER
            elif role_str == "assistant":
                role = MessageRole.ASSISTANT
            elif role_str == "system":
                role = MessageRole.SYSTEM
            else:
                role = MessageRole.USER  # Default fallback

            content = db_msg.content or ""

            # Check token limit if tokenizer is available
            if tokenizer_fn:
                message_tokens = tokenizer_fn(content)
                if current_token_count + message_tokens > max_tokens_for_history:
                    logger.debug(
                        f"Token limit reached. Stopping history load at {current_token_count} tokens."
                    )
                    break
                current_token_count += message_tokens

            history.append(ChatMessage(role=role, content=content))

        logger.debug(
            f"Loaded {len(history)} messages for session {session_id}, ~{current_token_count} tokens"
        )
        return history

    except ImportError as e:
        logger.error(f"Failed to import dependencies for chat history: {e}")
        return []
    except Exception as e:
        logger.error(
            f"Error loading chat history for session {session_id}: {e}", exc_info=True
        )
        return []


def _get_tokenizer_fn():
    """Get tokenizer function for token counting."""
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        return lambda text: len(encoding.encode(text))
    except Exception:
        # Fallback: approximate tokens as words * 1.3
        return lambda text: int(len(text.split()) * 1.3)


def save_message_to_db(session_id: str, role: str, content: str) -> None:
    """Save a message to the database."""
    try:
        from backend.models import LLMMessage, db
        from sqlalchemy.exc import SQLAlchemyError

        if not db or not LLMMessage:
            logger.error("DB/LLMMessage unavailable for save_message_to_db.")
            return

        message = LLMMessage(
            session_id=session_id, role=role, content=content, timestamp=datetime.now()
        )

        db.session.add(message)
        db.session.commit()
        logger.debug(f"Saved {role} message to session {session_id}")

    except SQLAlchemyError as e:
        try:
            from backend.utils.db_utils import safe_db_rollback

            safe_db_rollback(f"message save for session {session_id}")
        except ImportError:
            logger.warning("Could not import db_utils for rollback")
        logger.error(f"Error saving message to DB: {e}", exc_info=True)
    except ImportError as e:
        logger.error(f"Failed to import dependencies for message saving: {e}")
    except Exception as e:
        logger.error(f"Unexpected error saving message: {e}", exc_info=True)
