# backend/utils/unified_index_manager.py
# Unified Index Management System
# Consolidates all index loading mechanisms and provides better resource management

# Force local LlamaIndex configuration BEFORE any LlamaIndex imports
import backend.utils.llama_index_local_config

import logging
import os
import threading
from typing import Dict, Optional, Tuple, Any
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from contextlib import contextmanager

# LlamaIndex imports
from llama_index.core import (
    VectorStoreIndex,
    StorageContext,
    Settings,
    load_index_from_storage,
)
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.core.storage.index_store import SimpleIndexStore
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.query_engine import BaseQueryEngine

logger = logging.getLogger(__name__)


@dataclass
class IndexInfo:
    """Information about a loaded index"""

    index: VectorStoreIndex
    storage_context: StorageContext
    persist_dir: str
    project_id: Optional[str]
    last_accessed: datetime
    access_count: int
    memory_usage: int  # Estimated memory usage in bytes


class UnifiedIndexManager:
    """Unified index manager that handles all index operations"""

    def __init__(self, base_storage_dir: str, max_cached_indexes: int = 5):
        self.base_storage_dir = Path(base_storage_dir)
        self.max_cached_indexes = max_cached_indexes
        self.cached_indexes: Dict[str, IndexInfo] = {}
        self.lock = threading.RLock()

        # Ensure storage directory exists
        self.base_storage_dir.mkdir(parents=True, exist_ok=True)

        # Index access statistics
        self.access_stats = {
            "total_loads": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "index_creates": 0,
            "errors": 0,
        }

        logger.info(
            f"Unified Index Manager initialized with storage: {self.base_storage_dir}"
        )

    def _generate_index_key(self, project_id: Optional[str] = None) -> str:
        """Generate a unique key for the index"""
        return f"project_{project_id}" if project_id else "global"

    def _get_persist_dir(self, project_id: Optional[str] = None) -> Path:
        """Get the persistence directory for an index"""
        if project_id:
            return self.base_storage_dir / "projects" / str(project_id)
        else:
            # Use main storage directory instead of empty global_index subdirectory
            return self.base_storage_dir

    def _estimate_memory_usage(self, index: VectorStoreIndex) -> int:
        """Estimate memory usage of an index"""
        # This is a rough estimate - in practice, you'd want more sophisticated measurement
        try:
            # Try to get node count and estimate based on that
            if hasattr(index, "docstore") and hasattr(index.docstore, "docs"):
                doc_count = len(index.docstore.docs)
                return doc_count * 1024  # Estimate 1KB per document
            return 1024 * 1024  # Default 1MB estimate
        except Exception as e:
            logger.warning(f"Failed to estimate memory usage for index: {e}")
            return 1024 * 1024  # Default 1MB estimate

    def _should_evict_index(self) -> bool:
        """Check if we should evict an index from cache"""
        return len(self.cached_indexes) >= self.max_cached_indexes

    def _evict_least_recently_used(self) -> None:
        """Evict the least recently used index from cache"""
        if not self.cached_indexes:
            return

        # Find the least recently used index
        lru_key = min(
            self.cached_indexes.keys(),
            key=lambda k: self.cached_indexes[k].last_accessed,
        )

        logger.info(f"Evicting index from cache: {lru_key}")
        del self.cached_indexes[lru_key]

    def _load_index_from_storage(
        self, persist_dir: Path
    ) -> Tuple[VectorStoreIndex, StorageContext]:
        """Load index from storage directory"""
        try:
            if not persist_dir.exists():
                raise FileNotFoundError(
                    f"Storage directory does not exist: {persist_dir}"
                )

            # Check for required files
            required_files = ["docstore.json", "index_store.json"]
            for required_file in required_files:
                if not (persist_dir / required_file).exists():
                    raise FileNotFoundError(f"Required file missing: {required_file}")

            # Load the index
            storage_context = StorageContext.from_defaults(persist_dir=str(persist_dir))
            index = load_index_from_storage(storage_context)

            logger.info(f"Successfully loaded index from {persist_dir}")
            return index, storage_context

        except Exception as e:
            logger.error(f"Failed to load index from {persist_dir}: {e}")
            raise

    def _create_empty_index(
        self, persist_dir: Path
    ) -> Tuple[VectorStoreIndex, StorageContext]:
        """Create a new empty index"""
        try:
            # Ensure directory exists
            persist_dir.mkdir(parents=True, exist_ok=True)

            # Check if LLM and embedding model are configured
            if not Settings.llm or not Settings.embed_model:
                raise RuntimeError(
                    "LLM and embedding model must be configured in Settings"
                )

            # Create storage components
            docstore = SimpleDocumentStore()
            index_store = SimpleIndexStore()

            # Create storage context with explicit vector store
            from llama_index.core.vector_stores import SimpleVectorStore

            vector_store = SimpleVectorStore()
            storage_context = StorageContext.from_defaults(
                docstore=docstore,
                index_store=index_store,
                vector_store=vector_store,
                persist_dir=str(persist_dir),
            )

            # Create empty index
            index = VectorStoreIndex.from_documents([], storage_context=storage_context)

            # Persist the empty index
            storage_context.persist(persist_dir=str(persist_dir))

            logger.info(f"Created new empty index at {persist_dir}")
            self.access_stats["index_creates"] += 1

            return index, storage_context

        except Exception as e:
            logger.error(f"Failed to create empty index at {persist_dir}: {e}")
            self.access_stats["errors"] += 1
            raise

    @contextmanager
    def _index_access_context(self, index_key: str):
        """Context manager for index access tracking"""
        with self.lock:
            self.access_stats["total_loads"] += 1
            start_time = datetime.now()

            try:
                yield
                if index_key in self.cached_indexes:
                    self.cached_indexes[index_key].last_accessed = start_time
                    self.cached_indexes[index_key].access_count += 1
                    self.access_stats["cache_hits"] += 1
                else:
                    self.access_stats["cache_misses"] += 1
            except Exception as e:
                self.access_stats["errors"] += 1
                raise

    def get_index(
        self, project_id: Optional[str] = None, create_if_missing: bool = True
    ) -> Tuple[VectorStoreIndex, StorageContext]:
        """Get or create an index for the specified project"""
        index_key = self._generate_index_key(project_id)
        persist_dir = self._get_persist_dir(project_id)

        with self._index_access_context(index_key):
            # Check cache first
            if index_key in self.cached_indexes:
                index_info = self.cached_indexes[index_key]
                logger.debug(f"Index cache hit for {index_key}")
                return index_info.index, index_info.storage_context

            # Try to load from storage
            try:
                index, storage_context = self._load_index_from_storage(persist_dir)
            except FileNotFoundError as e:
                if create_if_missing:
                    logger.info(f"Index not found, creating new one: {e}")
                    index, storage_context = self._create_empty_index(persist_dir)
                else:
                    raise
            except PermissionError as e:
                logger.error(
                    f"Permission denied accessing index directory {persist_dir}: {e}"
                )
                raise
            except Exception as e:
                logger.error(f"Unexpected error loading index from {persist_dir}: {e}")
                if create_if_missing:
                    logger.warning("Attempting to create new index due to load error")
                    index, storage_context = self._create_empty_index(persist_dir)
                else:
                    raise

            # Add to cache
            if self._should_evict_index():
                self._evict_least_recently_used()

            index_info = IndexInfo(
                index=index,
                storage_context=storage_context,
                persist_dir=str(persist_dir),
                project_id=project_id,
                last_accessed=datetime.now(),
                access_count=1,
                memory_usage=self._estimate_memory_usage(index),
            )

            self.cached_indexes[index_key] = index_info
            logger.info(f"Cached index for {index_key}")

            return index, storage_context

    def get_retriever(
        self, project_id: Optional[str] = None, similarity_top_k: int = 5, **kwargs
    ) -> BaseRetriever:
        """Get a retriever for the specified project"""
        index, _ = self.get_index(project_id)
        return index.as_retriever(similarity_top_k=similarity_top_k, **kwargs)

    def get_query_engine(
        self, project_id: Optional[str] = None, **kwargs
    ) -> BaseQueryEngine:
        """Get a query engine for the specified project"""
        index, _ = self.get_index(project_id)
        return index.as_query_engine(**kwargs)

    def refresh_index(self, project_id: Optional[str] = None) -> None:
        """Refresh an index by reloading it from storage"""
        index_key = self._generate_index_key(project_id)

        with self.lock:
            # Remove from cache if present
            if index_key in self.cached_indexes:
                del self.cached_indexes[index_key]

            # Force reload on next access
            logger.info(f"Refreshed index: {index_key}")

    def persist_index(self, project_id: Optional[str] = None) -> None:
        """Persist an index to storage"""
        index_key = self._generate_index_key(project_id)

        with self.lock:
            if index_key not in self.cached_indexes:
                logger.warning(f"Cannot persist index {index_key}: not in cache")
                return

            index_info = self.cached_indexes[index_key]
            try:
                index_info.storage_context.persist(persist_dir=index_info.persist_dir)
                logger.info(f"Persisted index: {index_key}")
            except Exception as e:
                logger.error(f"Failed to persist index {index_key}: {e}")
                raise

    def clear_cache(self) -> None:
        """Clear all cached indexes"""
        with self.lock:
            self.cached_indexes.clear()
            logger.info("Cleared index cache")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.lock:
            cache_info = {}
            total_memory = 0

            for key, info in self.cached_indexes.items():
                cache_info[key] = {
                    "project_id": info.project_id,
                    "last_accessed": info.last_accessed.isoformat(),
                    "access_count": info.access_count,
                    "memory_usage": info.memory_usage,
                    "persist_dir": info.persist_dir,
                }
                total_memory += info.memory_usage

            return {
                "cached_indexes": cache_info,
                "total_cached": len(self.cached_indexes),
                "max_cached": self.max_cached_indexes,
                "total_memory_usage": total_memory,
                "access_stats": self.access_stats.copy(),
            }

    def cleanup_old_indexes(self, max_age_days: int = 30) -> None:
        """Clean up old index files from storage"""
        cutoff_time = datetime.now().timestamp() - (max_age_days * 24 * 3600)

        try:
            for index_dir in self.base_storage_dir.rglob("*"):
                if index_dir.is_dir() and index_dir.name != self.base_storage_dir.name:
                    # Check if directory is old
                    if index_dir.stat().st_mtime < cutoff_time:
                        # Check if it's not currently cached
                        is_cached = any(
                            info.persist_dir == str(index_dir)
                            for info in self.cached_indexes.values()
                        )

                        if not is_cached:
                            logger.info(f"Cleaning up old index directory: {index_dir}")
                            # Remove directory and contents
                            for file in index_dir.rglob("*"):
                                if file.is_file():
                                    file.unlink()
                            index_dir.rmdir()

        except Exception as e:
            logger.error(f"Error during index cleanup: {e}")


# Global instance
_global_index_manager: Optional[UnifiedIndexManager] = None
_manager_lock = threading.Lock()


def get_global_index_manager() -> UnifiedIndexManager:
    """Get the global index manager instance"""
    global _global_index_manager

    if _global_index_manager is None:
        with _manager_lock:
            if _global_index_manager is None:
                try:
                    from backend.config import STORAGE_DIR

                    storage_dir = STORAGE_DIR
                except (ImportError, AttributeError) as e:
                    logger.warning(f"Failed to import STORAGE_DIR: {e}, using fallback")
                    # Fallback to a reasonable default
                    import os
                    from pathlib import Path

                    storage_dir = str(Path.cwd() / "data")
                    os.makedirs(storage_dir, exist_ok=True)

                _global_index_manager = UnifiedIndexManager(storage_dir)

    return _global_index_manager


def reset_global_index_manager() -> None:
    """Reset the global index manager (useful for testing)"""
    global _global_index_manager
    with _manager_lock:
        _global_index_manager = None
