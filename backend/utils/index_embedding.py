"""The embedding client the vector index stores passages with.

``IndexOllamaEmbedding`` is ``OllamaEmbedding`` with one addition: while the
GPU Embedding plugin is switched on in Settings, passage embeddings go through
it when ``PluginEmbeddingGate`` allows (same model, probe vector matched; see
backend/utils/gpu_embedding_plugin.py). Query embeddings, and every passage
the gate refuses, take OllamaEmbedding's own path unchanged.
"""

import asyncio
import logging
import threading
import time
from typing import List, Optional

from llama_index.embeddings.ollama import OllamaEmbedding

logger = logging.getLogger(__name__)

_ENABLED_TTL_S = 5.0
_enabled_lock = threading.Lock()
_enabled_cache = {"at": -_ENABLED_TTL_S, "value": False}


def _plugin_switched_on() -> bool:
    """The plugin's Settings toggle, read at most every few seconds without
    building the EmbeddingRouter."""
    now = time.monotonic()
    with _enabled_lock:
        if now - _enabled_cache["at"] >= _ENABLED_TTL_S:
            try:
                from backend.utils.gpu_embedding_plugin import plugin_enabled
                _enabled_cache["value"] = plugin_enabled()
            except Exception:
                _enabled_cache["value"] = False
            _enabled_cache["at"] = now
        return _enabled_cache["value"]


class IndexOllamaEmbedding(OllamaEmbedding):
    """OllamaEmbedding whose passage embeddings may go through the GPU Embedding plugin."""

    def _via_plugin(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Vectors from the plugin, or None to embed on this client's own path."""
        if not texts or not _plugin_switched_on():
            return None
        try:
            from backend.utils.embedding_router import get_embedding_router
            router = get_embedding_router()
            gate = router.plugin_gate()
            if not gate.enabled():
                return None
        except Exception as e:
            logger.debug(f"GPU Embedding plugin gate unavailable: {e}")
            return None
        vectors = None
        try:
            own = lambda t: OllamaEmbedding._get_text_embeddings(self, t)  # noqa: E731
            if gate.usable(self.model_name, own, self._format_text):
                vectors = gate.embed(texts, self._format_text)
        except Exception as e:
            logger.warning(f"GPU Embedding plugin failed, the index embeds through Ollama: {e}")
        if vectors is None:
            gate.count(len(texts), via_plugin=False)
            router._note_path("ollama", len(texts))
            return None
        gate.count(len(texts), via_plugin=True)
        router._note_path("gpu_embedding_plugin", len(texts))
        return vectors

    def _get_text_embedding(self, text: str) -> List[float]:
        vectors = self._via_plugin([text])
        return vectors[0] if vectors is not None else super()._get_text_embedding(text)

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        vectors = self._via_plugin(texts)
        return vectors if vectors is not None else super()._get_text_embeddings(texts)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        vectors = await asyncio.to_thread(self._via_plugin, [text]) if _plugin_switched_on() else None
        return vectors[0] if vectors is not None else await super()._aget_text_embedding(text)

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        vectors = await asyncio.to_thread(self._via_plugin, texts) if _plugin_switched_on() else None
        return vectors if vectors is not None else await super()._aget_text_embeddings(texts)
