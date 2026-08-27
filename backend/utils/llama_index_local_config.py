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


# ---------------------------------------------------------------------------
# Asymmetric query/passage embedding instructions (P1-4a)
# ---------------------------------------------------------------------------
# Many open-weight embedders are trained with *asymmetric* prefixes: queries and
# passages are embedded with different instruction strings so they land in the
# same subspace at retrieval time. Omitting them embeds queries and documents in
# mismatched subspaces and silently halves recall.
#
# LlamaIndex's OllamaEmbedding (>=0.3) applies these via the `query_instruction`
# and `text_instruction` fields: `_format_query`/`_format_text` produce
# f"{instruction.strip()} {text.strip()}". So we supply the prefix *without* a
# trailing space and the formatter joins it with a single space — matching each
# model card's canonical convention (e.g. nomic's "search_query: <text>").
#
# Data-driven, fully offline. Unknown models get no prefix (safe no-op default).
# NOTE: changing passage prefixes requires a REINDEX to take full effect, because
# stored passage vectors must be re-embedded with the document instruction.
def get_embedding_instructions(model_name: str):
    """Return (query_instruction, text_instruction) for an embedding model.

    Matching is substring-based and case-insensitive so tagged Ollama names
    (e.g. "nomic-embed-text:latest", "snowflake-arctic-embed:l") resolve to the
    right family. Returns (None, None) for unknown models — a safe default that
    leaves embeddings unprefixed rather than guessing a wrong convention.
    """
    if not model_name:
        return None, None
    name = model_name.lower()

    # nomic-embed-text (v1/v1.5): canonical task prefixes per model card.
    if "nomic-embed-text" in name:
        return "search_query:", "search_document:"

    # Google embeddinggemma: pipe-delimited task prompts per model card.
    if "embeddinggemma" in name:
        return "task: search result | query:", "title: none | text:"

    # BGE family (bge-m3, bge-large, ...): query instruction only; passages raw.
    if "bge" in name:
        return ("Represent this sentence for searching relevant passages:", None)

    # E5 family (intfloat/e5, multilingual-e5): symmetric "query:"/"passage:".
    if "e5" in name:
        return "query:", "passage:"

    # Snowflake Arctic-embed: query-side instruction; passages raw.
    if "arctic-embed" in name or "snowflake" in name:
        return (
            "Represent this sentence for searching relevant passages:",
            None,
        )

    # qwen3-embedding: instruction-aware query side; passages raw.
    if "qwen3-embedding" in name:
        return (
            "Instruct: Given a search query, retrieve relevant passages\nQuery:",
            None,
        )

    # mxbai-embed-large and anything unrecognized: no prefix (safe default).
    return None, None


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
        import nest_asyncio
        nest_asyncio.apply()
        logger.info("Applied nest_asyncio to prevent nested event loop issues")
    except ImportError:
        logger.warning("nest_asyncio not found. Async event loops might conflict.")

    try:
        # Per edge-portability audit: remove unconditional CUDA_VISIBLE at import
        # (breaks CPU/ARM boxes). Probe for NVIDIA; workers stay CPU.
        if os.environ.get('CELERY_WORKER_MODE', 'false').lower() == 'true':
            os.environ['CUDA_VISIBLE_DEVICES'] = ''
            logger.info("CUDA disabled for Celery worker - using CPU")
        else:
            try:
                import subprocess
                if subprocess.run(['nvidia-smi'], capture_output=True, timeout=3).returncode == 0:
                    os.environ['CUDA_VISIBLE_DEVICES'] = '0'
                    logger.info("CUDA enabled for LlamaIndex - using GPU acceleration")
                else:
                    os.environ['CUDA_VISIBLE_DEVICES'] = ''
                    logger.info("No NVIDIA GPU - LlamaIndex using CPU")
            except Exception:
                os.environ['CUDA_VISIBLE_DEVICES'] = ''
                logger.info("GPU probe failed - LlamaIndex using CPU")

        os.environ['TOKENIZERS_PARALLELISM'] = 'false'  # Disable tokenizer parallelism
        # Import LlamaIndex core components
        from llama_index.core import Settings
        # Use local embeddings instead of HuggingFace
        from llama_index.core.embeddings import BaseEmbedding

        # Configure Ollama with active model (checks saved model first, then preference list)
        try:
            from llama_index.llms.ollama import Ollama  # noqa: F401  (availability probe)
            from backend.utils.ollama_resource_manager import build_ollama

            try:
                from backend.config import get_default_llm

                active_model = get_default_llm()
                logger.info(f"Using active model from file: {active_model}")

                local_llm = build_ollama(active_model, request_timeout=60.0)
                logger.info(f" Configured Ollama with real model: {active_model}")

            except ImportError as e:
                logger.warning(f"Could not import config, using fallback: {e}")
                active_model = "llama3:latest"
                local_llm = build_ollama(active_model, request_timeout=60.0)
                logger.info(f" Configured Ollama with fallback model: {active_model}")

        except ImportError:
            logger.error("Ollama not available - cannot use real LLM")
            raise ImportError("Ollama is required for local LLM functionality")

        # Configure embedding model via VRAM-aware selection in config.py
        try:
            from llama_index.embeddings.ollama import OllamaEmbedding
            from backend.config import get_active_embedding_model, get_embedding_keep_alive

            model_name = get_active_embedding_model()
            query_instruction, text_instruction = get_embedding_instructions(model_name)
            # Asymmetric query/passage prefixes (P1-4a). Only pass when defined so
            # unknown models keep LlamaIndex's default (no prefix).
            _instr_kwargs = {}
            if query_instruction:
                _instr_kwargs["query_instruction"] = query_instruction
            if text_instruction:
                _instr_kwargs["text_instruction"] = text_instruction
            local_embed_model = OllamaEmbedding(
                model_name=model_name,
                base_url="http://127.0.0.1:11434",
                ollama_additional_kwargs={"mirostat": 0},
                # Hardware-aware: short TTL on GPU (frees VRAM after idle, no per-query churn),
                # resident on CPU-only (no disk reload every cycle). See config.get_embedding_keep_alive.
                keep_alive=get_embedding_keep_alive(),
                **_instr_kwargs,
            )
            if _instr_kwargs:
                logger.info(
                    f"Embedding instructions for {model_name}: "
                    f"query={query_instruction!r} text={text_instruction!r} "
                    f"(reindex required for passage prefix to take effect)"
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
    This function must return proper Ollama embeddings (e.g., mxbai-embed-large)
    to match the vector index dimensions. Do NOT use SimpleTextEmbedding or
    hash-based embeddings - this causes dimension mismatch errors.
    Changes require direct permission from the project owner.

    Last verified working: 2026-02-13
    ============================================================================
    """
    # Use direct Ollama embedding to avoid circular imports and initialization hangs
    try:
        from backend.config import get_active_embedding_model, get_embedding_keep_alive
        from llama_index.embeddings.ollama import OllamaEmbedding

        model_name = get_active_embedding_model()
        logger.info(f"get_local_embedding_model: Using Ollama embedding: {model_name}")

        # Asymmetric query/passage prefixes (P1-4a) — must match the primary
        # client in force_local_llama_index_config() so queries and passages
        # land in the same subspace. Reindex required for passage prefix.
        query_instruction, text_instruction = get_embedding_instructions(model_name)
        _instr_kwargs = {}
        if query_instruction:
            _instr_kwargs["query_instruction"] = query_instruction
        if text_instruction:
            _instr_kwargs["text_instruction"] = text_instruction

        return OllamaEmbedding(
            model_name=model_name,
            base_url="http://127.0.0.1:11434",
            keep_alive=get_embedding_keep_alive(),  # consistent with the primary client
            **_instr_kwargs,
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
        from llama_index.llms.ollama import Ollama  # noqa: F401  (availability probe)
        from backend.utils.ollama_resource_manager import build_ollama

        try:
            from backend.config import get_default_llm
            active_model = get_default_llm()
            return build_ollama(active_model, request_timeout=60.0)
        except ImportError:
            return build_ollama("llama3:latest", request_timeout=60.0)

    except ImportError:
        logger.error("Ollama not available - no real LLM possible")
        return None

# Force configuration on import
logger.info("Forcing local LlamaIndex configuration...")
force_local_llama_index_config()
