#!/usr/bin/env python3
"""
LlamaIndex Local Configuration Module
Forcibly configures LlamaIndex to use local models instead of OpenAI
This should be imported BEFORE any other LlamaIndex imports to prevent OpenAI defaults
"""

import logging
import os

logger = logging.getLogger(__name__)

def _patch_chatmessage_content_setter():
    """Patch LlamaIndex ChatMessage.content setter to handle multi-block messages.

    LlamaIndex 0.14.x raises ValueError when setting .content on a ChatMessage
    with multiple blocks (e.g. ThinkingBlock + TextBlock from thinking models).
    This breaks write_response_to_history() in chat engines.
    """
    try:
        from llama_index.core.base.llms.types import ChatMessage, TextBlock

        original_prop = ChatMessage.__dict__.get('content')
        if not isinstance(original_prop, property):
            return

        def _safe_setter(self, content: str) -> None:
            if not self.blocks:
                self.blocks = [TextBlock(text=content)]
            elif len(self.blocks) == 1 and isinstance(self.blocks[0], TextBlock):
                self.blocks = [TextBlock(text=content)]
            else:
                # Multi-block message (thinking models): replace all blocks with single TextBlock
                self.blocks = [TextBlock(text=content)]

        ChatMessage.content = property(
            fget=original_prop.fget,
            fset=_safe_setter,
            doc=original_prop.__doc__,
        )
        logger.info("Patched ChatMessage.content setter for multi-block compatibility")
    except Exception as e:
        logger.warning(f"Could not patch ChatMessage.content setter: {e}")


_local_config_applied = False

def force_local_llama_index_config():
    """
    Forcibly configure LlamaIndex to use local models
    This must be called before any LlamaIndex imports that use Settings
    """
    global _local_config_applied
    if _local_config_applied:
        logger.debug("LlamaIndex local config already applied, skipping")
        return True
    try:
        # Configure CUDA for optimal performance
        # Only disable CUDA for Celery workers to prevent multiprocessing issues
        if os.environ.get('CELERY_WORKER_MODE', 'false').lower() != 'true':
            # Enable CUDA for main application
            os.environ['CUDA_VISIBLE_DEVICES'] = '0'  # Use GPU 0
            logger.info("CUDA enabled for LlamaIndex - using GPU acceleration")
        else:
            # CPU-only for Celery workers to prevent multiprocessing issues
            os.environ['CUDA_VISIBLE_DEVICES'] = ''
            logger.info("CUDA disabled for Celery worker - using CPU")

        os.environ['TOKENIZERS_PARALLELISM'] = 'false'  # Disable tokenizer parallelism
        # Import LlamaIndex core components
        from llama_index.core import Settings
        # Use local embeddings instead of HuggingFace
        from llama_index.core.embeddings import BaseEmbedding

        # Configure Ollama with active model (checks saved model first, then preference list)
        try:
            from llama_index.llms.ollama import Ollama

            try:
                from backend.config import get_default_llm

                active_model = get_default_llm()
                logger.info(f"Using active model from file: {active_model}")

                local_llm = Ollama(model=active_model, request_timeout=60.0)
                logger.info(f" Configured Ollama with real model: {active_model}")

            except ImportError as e:
                logger.warning(f"Could not import config, using fallback: {e}")
                active_model = "llama3:latest"
                local_llm = Ollama(model=active_model, request_timeout=60.0)
                logger.info(f" Configured Ollama with fallback model: {active_model}")

        except ImportError:
            logger.error("Ollama not available - cannot use real LLM")
            raise ImportError("Ollama is required for local LLM functionality")

        # Configure embedding model via VRAM-aware selection in config.py
        try:
            from llama_index.embeddings.ollama import OllamaEmbedding
            from backend.config import get_active_embedding_model

            model_name = get_active_embedding_model()
            local_embed_model = OllamaEmbedding(
                model_name=model_name,
                base_url="http://localhost:11434",
                ollama_additional_kwargs={"mirostat": 0},
                keep_alive=0,  # Unload embedding model after use to free VRAM for chat LLM
            )
            if not hasattr(local_embed_model, "model_name"):
                local_embed_model.model_name = model_name
            logger.info(f"Using Ollama embedding: {model_name} (VRAM-aware selection)")

        except ImportError as import_err:
            # ============================================================================
            # PROTECTED CODE - DO NOT ADD SIMPLETEXTEMBEDDING FALLBACK
            # ----------------------------------------------------------------------------
            # SimpleTextEmbedding with 384-dim causes dimension mismatch with the
            # vector index. Ollama embeddings are REQUIRED.
            # Last verified: 2026-02-13
            # ============================================================================
            logger.error(f"Ollama embeddings import failed: {import_err}")
            raise RuntimeError(
                f"Cannot initialize embedding model - Ollama embeddings required. "
                f"Import error: {import_err}. "
                f"Please ensure llama-index-embeddings-ollama is installed."
            ) from import_err

        Settings.embed_model = local_embed_model

        # Set global settings to use local models
        Settings.llm = local_llm

        # Patch ChatMessage.content setter for multi-block compatibility (thinking models)
        _patch_chatmessage_content_setter()

        # Disable OpenAI environment variables to prevent fallback
        os.environ.pop('OPENAI_API_KEY', None)
        os.environ.pop('OPENAI_API_BASE', None)

        _local_config_applied = True
        logger.info(" LlamaIndex configured to use local models only")
        return True

    except ImportError as e:
        logger.error(f"Failed to import required LlamaIndex components: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to configure local LlamaIndex: {e}")
        return False

def get_local_embedding_model():
    """
    Get the local embedding model instance using proper Ollama embeddings.

    ============================================================================
    PROTECTED CODE - DO NOT MODIFY WITHOUT EXPLICIT PERMISSION
    ----------------------------------------------------------------------------
    This function must return proper Ollama embeddings (e.g., qwen3-embedding)
    to match the vector index dimensions. Do NOT use SimpleTextEmbedding or
    hash-based embeddings - this causes dimension mismatch errors.
    Changes require direct permission from the project owner.

    Last verified working: 2026-02-13
    ============================================================================
    """
    # Use direct Ollama embedding to avoid circular imports and initialization hangs
    try:
        from backend.config import get_active_embedding_model
        from llama_index.embeddings.ollama import OllamaEmbedding

        model_name = get_active_embedding_model()
        logger.info(f"get_local_embedding_model: Using Ollama embedding: {model_name}")

        return OllamaEmbedding(
            model_name=model_name,
            base_url="http://localhost:11434",
        )
    except Exception as e:
        logger.error(f"Failed to initialize Ollama embedding: {e}")
        raise RuntimeError(
            f"Cannot initialize embedding model: {e}. "
            f"Please ensure Ollama is running with an embedding model available."
        ) from e

def get_local_llm():
    """Get a local LLM instance using real active model"""
    try:
        from llama_index.llms.ollama import Ollama

        try:
            from backend.config import get_default_llm
            active_model = get_default_llm()
            return Ollama(model=active_model, request_timeout=60.0)
        except ImportError:
            return Ollama(model="llama3:latest", request_timeout=60.0)

    except ImportError:
        logger.error("Ollama not available - no real LLM possible")
        return None

# Force configuration on import
logger.info("Forcing local LlamaIndex configuration...")
force_local_llama_index_config()
