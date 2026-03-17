"""
Embedding Adapter for LlamaIndex
Adapts GPU Embedding Service to LlamaIndex BaseEmbedding interface.
"""

import logging
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# Try to import LlamaIndex BaseEmbedding
try:
    from llama_index.core.embeddings import BaseEmbedding
    from llama_index.core.bridge.pydantic import PrivateAttr

    LLAMAINDEX_AVAILABLE = True
except ImportError:
    LLAMAINDEX_AVAILABLE = False
    BaseEmbedding = object  # Fallback for type hints

    # Mock PrivateAttr for fallback
    def PrivateAttr(default=None):
        return default

    logger.warning(
        "LlamaIndex not available - embedding adapter will have limited functionality"
    )

from .gpu_embedding_client import GPUEmbeddingClient, create_client


class GPUEmbeddingAdapter(BaseEmbedding if LLAMAINDEX_AVAILABLE else object):
    """
    Adapter that wraps GPU Embedding Service to work with LlamaIndex.

    Implements the BaseEmbedding interface while using the GPU service
    for actual embedding generation. Falls back to CPU/Ollama if GPU
    service is unavailable.
    """

    _client: Any = PrivateAttr()
    _fallback_embedding: Optional[BaseEmbedding] = PrivateAttr()
    _fallback_enabled: bool = PrivateAttr()
    _embed_dim: Optional[int] = PrivateAttr(default=None)
    _model_name: Optional[str] = PrivateAttr(default=None)

    def __init__(
        self,
        service_url: Optional[str] = None,
        timeout: int = 30,
        fallback_embedding: Optional[BaseEmbedding] = None,
        fallback_enabled: bool = True,
        model_name: Optional[str] = None,
    ):
        """
        Initialize GPU Embedding Adapter.

        Args:
            service_url: URL of GPU embedding service
            timeout: Request timeout in seconds
            fallback_embedding: Fallback embedding model if GPU service unavailable
            fallback_enabled: Whether to use fallback on failure
            model_name: Optional model name to use for GPU service requests
        """
        if LLAMAINDEX_AVAILABLE:
            # Initialize with default embed_dim (will be updated from service)
            super().__init__()

        self._client = create_client(service_url=service_url, timeout=timeout)
        self._fallback_embedding = fallback_embedding
        self._fallback_enabled = fallback_enabled
        self._model_name = model_name  # Store provided model name
        self._embed_dim = None

        # Try to get model info to set embed_dim
        self._initialize_model_info()

        logger.info(
            f"Initialized GPU Embedding Adapter for {self._client.service_url}"
            + (f" with model: {model_name}" if model_name else "")
        )

    def _initialize_model_info(self):
        """Initialize model information from service"""
        try:
            if self._client.is_available():
                models_info = self._client.get_models()
                self._embed_dim = models_info.get("embed_dim")
                self._model_name = models_info.get("current_model")
                logger.info(
                    f"GPU service model: {self._model_name}, dim: {self._embed_dim}"
                )
            else:
                logger.warning("GPU service not available, using fallback dimensions")
                # Use fallback dimensions if available
                if self._fallback_embedding and hasattr(
                    self._fallback_embedding, "embed_dim"
                ):
                    self._embed_dim = self._fallback_embedding.embed_dim
        except Exception as e:
            logger.warning(f"Failed to initialize model info: {e}")
            if self._fallback_embedding and hasattr(
                self._fallback_embedding, "embed_dim"
            ):
                self._embed_dim = self._fallback_embedding.embed_dim

    @property
    def embed_dim(self) -> int:
        """Get embedding dimension"""
        if self._embed_dim is None:
            # Default dimension if unknown
            return 768
        return self._embed_dim

    def _get_query_embedding(self, query: str) -> List[float]:
        """Get embedding for a query (synchronous)"""
        return self._get_text_embedding(query)

    def _get_text_embedding(self, text: str) -> List[float]:
        """Get embedding for text (synchronous)"""
        # Try GPU service first
        if self._client.is_available():
            try:
                result = self._client.generate_embedding(text, model=self._model_name)
                embedding = result.get("embedding")
                if embedding:
                    return embedding
            except Exception as e:
                logger.warning(f"GPU embedding failed: {e}, using fallback")

        # Fallback to CPU/Ollama embedding
        if self._fallback_enabled and self._fallback_embedding:
            try:
                if hasattr(self._fallback_embedding, "_get_text_embedding"):
                    return self._fallback_embedding._get_text_embedding(text)
                elif hasattr(self._fallback_embedding, "get_text_embedding"):
                    return self._fallback_embedding.get_text_embedding(text)
            except Exception as e:
                logger.error(f"Fallback embedding also failed: {e}")

        # Last resort: raise error
        raise RuntimeError("Both GPU service and fallback embedding failed")

    async def _aget_query_embedding(self, query: str) -> List[float]:
        """Get embedding for a query (async)"""
        return await self._aget_text_embedding(query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        """Get embedding for text (async)"""
        # For now, use synchronous method (can be improved with async HTTP client)
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_text_embedding, text)

    def get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Get embeddings for multiple texts (batch processing).

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        # Try GPU service batch endpoint first
        if self._client.is_available():
            try:
                result = self._client.generate_embeddings_batch(
                    texts, model=self._model_name
                )
                embeddings = result.get("embeddings")
                if embeddings and len(embeddings) == len(texts):
                    return embeddings
            except Exception as e:
                logger.warning(f"GPU batch embedding failed: {e}, using fallback")

        # Fallback to sequential processing with fallback model
        if self._fallback_enabled and self._fallback_embedding:
            try:
                if hasattr(self._fallback_embedding, "get_text_embeddings"):
                    return self._fallback_embedding.get_text_embeddings(texts)
                else:
                    # Sequential fallback
                    return [self._get_text_embedding(text) for text in texts]
            except Exception as e:
                logger.error(f"Fallback batch embedding failed: {e}")

        # Last resort: raise error
        raise RuntimeError("Both GPU service and fallback embedding failed for batch")

    def is_gpu_available(self) -> bool:
        """Check if GPU service is available"""
        return self._client.is_available()

    def get_service_info(self) -> Dict[str, Any]:
        """Get information about the GPU service"""
        health = self._client.health_check()
        return {
            "service_url": self._client.service_url,
            "available": health.get("available", False),
            "status": health.get("status", "unknown"),
            "model_name": health.get("model_name"),
            "gpu_available": health.get("gpu_available", False),
        }
