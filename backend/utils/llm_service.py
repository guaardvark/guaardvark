# backend/utils/llm_service.py
# Version 2.0: Added structured output generation with Pydantic.

import logging
import os
import re
import textwrap
import time
import types
from typing import Optional

from pydantic import BaseModel

try:
    from llama_index.core.program import LLMCompletionProgram
except Exception:  # pragma: no cover - older versions may not have this class
    try:  # Fallback for older package versions
        from llama_index.core.program import \
            LLMTextCompletionProgram as LLMCompletionProgram
    except Exception:
        LLMCompletionProgram = None  # type: ignore

try:
    from flask import current_app
    from llama_index.core.base.llms.types import ChatResponse
    from llama_index.core.llms import LLM, ChatMessage, MessageRole
except ImportError:
    logging.critical(
        "Failed to import Flask or LlamaIndex components in llm_service.py."
    )
    # Common misconfiguration: running without LlamaIndex installed.
    # The application will still start but LLM features will be disabled.
    current_app = None
    ChatMessage = None  # type: ignore
    MessageRole = None  # type: ignore
    LLM = None  # type: ignore
    ChatResponse = None  # type: ignore

logger = logging.getLogger(__name__)


def _safe_content(message) -> Optional[str]:
    """Extract content from a LlamaIndex ChatMessage, handling multi-block (thinking) models."""
    if not message:
        return None
    try:
        return message.content
    except (ValueError, AttributeError):
        blocks = getattr(message, 'blocks', [])
        for block in blocks:
            text = getattr(block, 'text', str(block) if block else "")
            if text:
                return text
        if blocks:
            return str(blocks[0])
        thinking = getattr(message, 'thinking', None)
        if thinking:
            return str(thinking)
        return None


def get_llm_instance() -> Optional[LLM]:
    if not current_app:
        logger.error("Flask current_app context not available.")
        return None
    llm = current_app.config.get("LLAMA_INDEX_LLM")
    if not llm:
        logger.error("LLM instance not found in Flask app config ('LLAMA_INDEX_LLM').")
        return None
    return llm  # type: ignore


def generate_structured_output(prompt: str, output_cls: BaseModel, llm) -> BaseModel:
    """Generate structured output from an LLM call using a Pydantic model."""
    if not LLMCompletionProgram:
        raise RuntimeError("LLMCompletionProgram not available")

    program = LLMCompletionProgram.from_defaults(
        output_cls=output_cls,
        llm=llm,
        prompt_template_str=prompt,
    )
    output = program()
    return output


def extract_python_code(text: str) -> Optional[str]:
    if not text:
        logger.warning("extract_python_code received empty text.")
        return None
    text = text.strip()
    match_python = re.search(r"```python\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match_python:
        extracted_code = match_python.group(1).strip()
        logger.info(
            f"Extracted Python code using ```python ... ``` block (length: {len(extracted_code)})."
        )
        return textwrap.dedent(extracted_code)
    match_generic = re.search(r"```\s*([\s\S]*?)\s*```", text)
    if match_generic:
        extracted_code = match_generic.group(1).strip()
        if (
            "def " in extracted_code
            or "import " in extracted_code
            or "print(" in extracted_code
            or "=" in extracted_code
        ):
            logger.info(
                f"Extracted Python code using generic ``` ... ``` block (length: {len(extracted_code)})."
            )
            return textwrap.dedent(extracted_code)
    python_indicators = [
        "import ",
        "def ",
        "class ",
        "print(",
        " = ",
        " for ",
        " while ",
        " if ",
    ]
    starts_like_python = text.startswith(tuple(python_indicators + ["#"]))
    contains_python = any(indicator in text for indicator in python_indicators)
    if starts_like_python or contains_python:
        logger.warning(
            "No code blocks found, but the text resembles Python. Attempting to use the entire response as code."
        )
        logger.debug(
            f"Using entire response as code (length: {len(text)}):\n{text[:500]}{'...' if len(text) > 500 else ''}"
        )
        return text
    logger.error(
        "Failed to extract Python code. No ```python...```, ```...``` blocks found, and the text does not resemble Python code."
    )
    logger.debug(f"Full text received:\n{text}")
    return None


def run_llm_code_prompt(prompt: str) -> Optional[str]:
    if not ChatMessage or not MessageRole:
        logger.critical(
            "LlamaIndex ChatMessage/MessageRole classes not available. Cannot run LLM code prompt."
        )
        return None
    llm = get_llm_instance()
    if not llm:
        return None
    logger.info(
        f"LLM code prompt received (length: {len(prompt)}). Preview: {prompt[:100]}"
    )
    system_message = (
        "You are an AI assistant specialized in writing Python code snippets. "
        "Your task is to generate Python code based on the user's instructions, context, and available tools. "
        "Strictly adhere to the output format requirements specified in the user prompt (e.g., respond ONLY with the required Python code block enclosed in ```python ... ```)."
    )
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=system_message),  # type: ignore
        ChatMessage(role=MessageRole.USER, content=prompt),  # type: ignore
    ]
    try:
        response: ChatResponse = llm.chat(messages)  # type: ignore
        content = _safe_content(response.message)
        if content is None or not str(content).strip():
            logger.warning(
                "LLM response message content was None or empty for code prompt."
            )
            return None
        logger.info(
            f"LLM raw response received (length: {len(content)}). Preview: {content[:100]}"
        )
        logger.debug(
            f"LLM Response Snippet:\n{content[:1000]}{'...' if len(content) > 1000 else ''}"
        )
        return content
    except Exception as e:
        logger.error(f"Error during LLM chat interaction for code: {e}", exc_info=True)
        return None


def run_llm_chat_prompt(
    prompt: str,
    llm_instance: Optional[LLM] = None,
    messages: Optional[list[ChatMessage]] = None,
    debug_id: Optional[str] = None,
) -> str:
    """Send a simple chat prompt to the configured LLM and return the text response."""
    if not ChatMessage or not MessageRole:
        logger.critical(
            "LlamaIndex ChatMessage/MessageRole classes not available. Returning placeholder text."
        )
        return "[The model returned no response.]"

    llm = llm_instance or get_llm_instance()
    if not llm:
        logger.error("LLM instance not available for run_llm_chat_prompt.")
        return "[LLM unavailable]"

    if LLM and not isinstance(llm, LLM):
        logger.warning("Configured LLM does not inherit from expected base class LLM.")

    if messages is None:
        messages = [ChatMessage(role=MessageRole.USER, content=prompt)]

    preview = " | ".join(
        f"{getattr(m, 'role', '?')}: {str(getattr(m, 'content', ''))[:80]}"
        for m in messages
    )
    logger.info(f"LLM chat messages: {preview}")

    if not any(getattr(m, "role", None) == MessageRole.SYSTEM for m in messages):
        messages.insert(
            0,
            ChatMessage(
                role=MessageRole.SYSTEM, content="You are a helpful assistant."
            ),
        )

    try:
        logger.info(
            "Sending chat prompt to LLM (model: %s, debug_id=%s)...",
            getattr(llm, "model", "N/A"),
            debug_id,
        )
        response: ChatResponse = llm.chat(messages)  # type: ignore
        content = _safe_content(response.message)
    except Exception as e:
        logger.error(
            "Error during LLM direct chat interaction (debug_id=%s): %s",
            debug_id,
            e,
            exc_info=True,
        )
        return "[LLM error occurred.]"

    if content is None:
        logger.warning(
            "LLM direct chat response message content is None (debug_id=%s).",
            debug_id,
        )
        return "[The model returned no response.]"

    content = content.strip()
    if not content:
        logger.warning(
            "LLM direct chat response was an empty string (debug_id=%s).",
            debug_id,
        )
        return "[The model returned no response.]"

    logger.info(
        "LLM direct chat response received (debug_id=%s, length=%d).",
        debug_id,
        len(content),
    )
    return content


def generate_long_form_content(prompt: str, temperature: float = 0.7) -> str | None:
    if not ChatMessage or not MessageRole:
        logger.critical(
            "LlamaIndex ChatMessage/MessageRole classes not available. Cannot run LLM long-form prompt."
        )
        return None
    llm = get_llm_instance()
    if not llm:
        return None
    logger.info(
        f"Long-form prompt received (length: {len(prompt)}). Preview: {prompt[:100]}"
    )
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content="You are an AI assistant tasked with generating detailed content based on user specifications. Follow all formatting and length requirements precisely."),  # type: ignore
        ChatMessage(role=MessageRole.USER, content=prompt),  # type: ignore
    ]
    try:
        logger.info(
            f"Sending long-form prompt to LLM (model: {getattr(llm, 'model', 'N/A')}, temp: {temperature})..."
        )
        response: ChatResponse = llm.chat(messages)  # type: ignore
        content = _safe_content(response.message)
        if content is None or not str(content).strip():
            logger.warning("LLM long-form response message content was None or empty.")
            return None
        logger.info(
            "LLM long-form response received (length: %d). Preview: %s",
            len(content),
            content[:100],
        )
        logger.debug(
            f"LLM Long-form Response Snippet:\n{content[:500]}{'...' if len(content) > 500 else ''}"
        )
        return content
    except Exception as e:
        logger.error(f"Error during LLM long-form chat interaction: {e}", exc_info=True)
        return None


def generate_text_basic(llm=None, prompt=None, is_json_response: bool = False):
    """
    Minimal generic text generation function.
    Returns a string response from LLM.
    """
    logger.info(
        f"generate_text_basic called. Prompt length: {len(prompt) if prompt else 'N/A'}. is_json_response: {is_json_response}"
    )
    if prompt:
        logger.debug(f"generate_text_basic prompt preview: {prompt[:100]}")
    actual_llm = llm if llm else get_llm_instance()

    if actual_llm is None:
        logger.error("generate_text_basic: LLM instance not available.")
        return None
    if not prompt or not isinstance(prompt, str):
        logger.error("generate_text_basic: No valid prompt provided.")
        return None

    # --- Simplified System Prompt - Trust the LLM more ---
    system_prompt_content = "Generate the requested content."
    
    if is_json_response:
        system_prompt_content = "Generate valid JSON as requested."

    try:
        if not ChatMessage or not MessageRole:
            logger.critical(
                "generate_text_basic: LlamaIndex ChatMessage/MessageRole classes not available."
            )
            return None

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt_content),  # type: ignore
            ChatMessage(role=MessageRole.USER, content=prompt),  # type: ignore
        ]
        response: ChatResponse = actual_llm.chat(messages)  # type: ignore
        content = _safe_content(response.message)

        if not content:
            logger.warning(
                "generate_text_basic: LLM response message content is None or empty."
            )
            return ""  # Return empty string instead of None if content is None or empty

        logger.info(
            f"generate_text_basic: Raw LLM response received (length: {len(content)}). Preview: {content[:100]}"
        )

        # --- Post-processing to remove <think>...</think> blocks ---
        # Using re.DOTALL to make '.' match newlines, and re.IGNORECASE for the tags.
        # Non-greedy match .*? is important.
        cleaned_content = re.sub(
            r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE
        )

        if len(cleaned_content) < len(content):
            logger.info(
                f"generate_text_basic: Removed <think> blocks. Original length: {len(content)}, Cleaned length: {len(cleaned_content)}"
            )
        else:
            logger.info("generate_text_basic: No <think> blocks found or removed.")

        return cleaned_content.strip()
    except Exception as e:
        logger.error(
            f"generate_text_basic: Error during LLM chat interaction: {e}",
            exc_info=True,
        )
        return ""  # Return empty string on error to ensure a string is always returned if not None


# Use the real Ollama class from llama_index. Mocks are strictly disallowed.
try:
    from llama_index.llms.ollama import Ollama
except ImportError:
    try:
        from llama_index_llms_ollama import Ollama
    except ImportError:
        Ollama = None
        logger.warning("Ollama import failed - using fallback")

from backend.config import (ACTIVE_MODEL_FILE, DEFAULT_EMBEDDING_MODEL,
                            DEFAULT_LLM, OLLAMA_BASE_URL)


def get_default_llm() -> Ollama:
    """Instantiate and return the default Ollama LLM, preferring the saved active model."""
    from backend.config import LLM_REQUEST_TIMEOUT
    timeout_value = min(LLM_REQUEST_TIMEOUT, 180.0)

    model_name = DEFAULT_LLM
    try:
        saved_name = get_saved_active_model_name()
        if saved_name:
            model_name = saved_name
    except Exception as e:
        logger.warning("Failed to get saved active model, using default: %s", e)

    # Adaptive context window based on available resources
    try:
        from backend.utils.ollama_resource_manager import compute_optimal_num_ctx
        num_ctx = compute_optimal_num_ctx(model_name)
    except Exception as e:
        logger.warning("Failed to compute adaptive num_ctx, using 8192: %s", e)
        num_ctx = 8192

    return Ollama(
        model=model_name,
        base_url=OLLAMA_BASE_URL,
        request_timeout=timeout_value,
        temperature=0.4,
        context_window=num_ctx,
        keep_alive="24h",
        additional_kwargs={"num_ctx": num_ctx, "top_p": 0.8, "top_k": 30}
    )


def get_llm_for_startup() -> Ollama:
    """Return an Ollama instance using the last active model if available."""
    from backend.config import LLM_REQUEST_TIMEOUT

    model_name = DEFAULT_LLM
    try:
        # Use get_saved_active_model_name() which checks both DB and file
        # This ensures the user's model choice persists across restarts
        saved_name = get_saved_active_model_name()
        if saved_name:
            model_name = saved_name
            logger.info("Using saved active model: %s", saved_name)
    except Exception as e:
        logger.warning("Failed to fetch active model name: %s", e)

    # Validate model can be loaded and compute adaptive context window
    try:
        from backend.utils.ollama_resource_manager import validate_model_before_load
        safe, reason, num_ctx = validate_model_before_load(model_name)
        if not safe:
            logger.warning(
                "Model '%s' may not fit in available memory: %s. Using minimum context.",
                model_name, reason,
            )
        else:
            logger.info("Resource check for '%s': %s", model_name, reason)
    except Exception as e:
        logger.warning("Failed to validate model resources, using num_ctx=8192: %s", e)
        num_ctx = 8192

    start = time.time()
    timeout_value = min(LLM_REQUEST_TIMEOUT, 180.0)  # Cap at 3 minutes for startup
    llm = Ollama(
        model=model_name,
        base_url=OLLAMA_BASE_URL,
        request_timeout=timeout_value,
        temperature=0.4,
        context_window=num_ctx,
        keep_alive="24h",
        additional_kwargs={"num_ctx": num_ctx, "top_p": 0.8, "top_k": 30}
    )
    logger.info("Loaded LLM '%s' with num_ctx=%d in %.2fs", model_name, num_ctx, time.time() - start)
    return llm


def get_default_embed_model():
    """
    Return embedding model using the EmbeddingRouter architecture.

    The router provides adaptive GPU/CPU load balancing via Ollama:
    - GPU path: Default Ollama (model in VRAM, fast)
    - CPU path: Ollama with num_gpu=0 (model in RAM only, zero VRAM)

    Both paths use the same model and produce identical vectors.
    """
    logger.info("Initializing embedding model")

    try:
        from backend.config import get_active_embedding_model
        from llama_index.embeddings.ollama import OllamaEmbedding

        model_name = get_active_embedding_model()
        logger.info(f"Using Ollama embedding model: {model_name}")

        embed_model = OllamaEmbedding(
            model_name=model_name,
            base_url="http://localhost:11434",
            ollama_additional_kwargs={"mirostat": 0},
            keep_alive=0,  # Unload after use to free VRAM for chat
        )
        return embed_model

    except Exception as e:
        logger.error(f"Failed to initialize embedding model: {e}")
        raise RuntimeError(
            f"Cannot initialize embedding model: {e}. "
            f"Please ensure Ollama is running with an embedding model available."
        ) from e


def persist_active_model_name(model_name: str) -> None:
    """Persist the active model name to ACTIVE_MODEL_FILE (JSON) and database."""
    import json
    from datetime import datetime

    # Save to JSON file
    try:
        os.makedirs(os.path.dirname(ACTIVE_MODEL_FILE), exist_ok=True)
        data = {
            "active_model": model_name,
            "updated_at": datetime.now().isoformat(),
        }
        with open(ACTIVE_MODEL_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Persisted active model '%s' to %s", model_name, ACTIVE_MODEL_FILE)
    except Exception as e:
        logger.warning("Failed to persist active model to %s: %s", ACTIVE_MODEL_FILE, e)

    # Save to database
    try:
        from backend.models import Setting, db

        if db and Setting:
            setting = db.session.get(Setting, "active_model_name")
            if setting:
                setting.value = model_name
            else:
                setting = Setting(key="active_model_name", value=model_name)
                db.session.add(setting)
            db.session.commit()
            logger.info("Persisted active model '%s' to database", model_name)
    except Exception as e:
        logger.warning("Failed to persist active model to database: %s", e)
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass


def get_saved_active_model_name() -> Optional[str]:
    """Return the active model name stored in DB or file, if any."""
    import json

    # Try database first
    try:
        from backend.models import Setting, db

        if db and Setting:
            setting = db.session.get(Setting, "active_model_name")
            if setting and setting.value:
                return setting.value
    except Exception as e:  # pragma: no cover - best effort
        logger.warning("Failed to load active model from DB: %s", e)

    # Fallback to JSON file
    try:
        if os.path.isfile(ACTIVE_MODEL_FILE):
            with open(ACTIVE_MODEL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                model_name = data.get("active_model", "")
                if model_name:
                    return model_name
    except (json.JSONDecodeError, KeyError):
        # Handle legacy plain-text format
        try:
            with open(ACTIVE_MODEL_FILE, "r", encoding="utf-8") as f:
                model_name = f.read().strip()
                if model_name and not model_name.startswith("{"):
                    return model_name
        except Exception:
            pass
    except Exception as e:  # pragma: no cover - best effort
        logger.warning("Failed to read active model file %s: %s", ACTIVE_MODEL_FILE, e)
    return None


def load_active_llm() -> Ollama:
    """Load the last active LLM if available, else fall back to default."""
    from backend.config import LLM_REQUEST_TIMEOUT
    
    saved_model = get_saved_active_model_name()
    if saved_model:
        try:
            from backend.api.model_api import get_available_ollama_models

            models_data = get_available_ollama_models()
            if isinstance(models_data, list):
                names = [m.get("name") for m in models_data if isinstance(m, dict)]
                if saved_model in names:
                    logger.info("Loading previously active model: %s", saved_model)
                    timeout_value = min(LLM_REQUEST_TIMEOUT, 180.0)
                    # Adaptive context window
                    try:
                        from backend.utils.ollama_resource_manager import compute_optimal_num_ctx
                        num_ctx = compute_optimal_num_ctx(saved_model)
                    except Exception:
                        num_ctx = 8192
                    llm = Ollama(
                        model=saved_model,
                        base_url=OLLAMA_BASE_URL,
                        request_timeout=timeout_value,
                        temperature=0.4,
                        context_window=num_ctx,
                        keep_alive="24h",
                        additional_kwargs={"num_ctx": num_ctx, "top_p": 0.8, "top_k": 30}
                    )
                    llm.complete("Test.")
                    return llm
                else:
                    logger.warning(
                        "Saved active model '%s' not present in Ollama. Falling back to default.",
                        saved_model,
                    )
            else:
                logger.warning(
                    "Could not verify available models from Ollama API; falling back to default."
                )
        except Exception as e:  # pragma: no cover - network or other errors
            logger.warning("Failed to load saved active model '%s': %s", saved_model, e)

    logger.info("Using default LLM '%s'", DEFAULT_LLM)
    return get_default_llm()
