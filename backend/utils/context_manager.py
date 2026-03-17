# backend/utils/context_manager.py
# Advanced Context Window Management System
# Implements sliding window, intelligent truncation, and context compression

import logging
import json
import hashlib
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ContextChunk:
    """Represents a chunk of context with metadata"""

    content: str
    timestamp: datetime
    token_count: int
    importance: float  # 0.0 to 1.0
    chunk_type: str  # 'message', 'document', 'summary', 'system'
    source_id: str
    metadata: Dict[str, Any]


@dataclass
class ContextWindow:
    """Represents a managed context window"""

    chunks: List[ContextChunk]
    total_tokens: int
    max_tokens: int
    compression_ratio: float
    last_updated: datetime


class ContextManager:
    """Advanced context window management with sliding window and compression"""

    def __init__(
        self,
        max_tokens: int = 8192,
        compression_threshold: float = 0.8,
        persistence_dir: Optional[str] = None,
    ):
        self.max_tokens = max_tokens
        self.compression_threshold = compression_threshold
        self.persistence_dir = Path(persistence_dir) if persistence_dir else None
        self.context_windows: Dict[str, ContextWindow] = {}
        self.tokenizer_cache = {}

        # Initialize persistence directory
        if self.persistence_dir:
            self.persistence_dir.mkdir(parents=True, exist_ok=True)
            self._load_persisted_contexts()

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text (cached for performance)"""
        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash in self.tokenizer_cache:
            return self.tokenizer_cache[text_hash]

        # Simple token estimation: ~4 chars per token for English
        # This can be replaced with actual tokenizer when available
        token_count = max(1, len(text) // 4)
        self.tokenizer_cache[text_hash] = token_count
        return token_count

    def _safe_parse_datetime(self, datetime_str: str) -> datetime:
        """Safely parse datetime string with fallback"""
        try:
            return datetime.fromisoformat(datetime_str)
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Failed to parse datetime '{datetime_str}': {e}, using current time"
            )
            return datetime.now()

    def _calculate_importance(self, chunk: ContextChunk) -> float:
        """Calculate importance score for a context chunk"""
        base_importance = 0.5

        # Type-based importance
        type_weights = {"system": 0.9, "summary": 0.8, "message": 0.6, "document": 0.7}
        importance = type_weights.get(chunk.chunk_type, base_importance)

        # Recency boost (more recent = more important)
        age_hours = (datetime.now() - chunk.timestamp).total_seconds() / 3600
        recency_boost = max(0, 1 - age_hours / 24)  # Decays over 24 hours
        importance = min(1.0, importance + recency_boost * 0.2)

        # Content-based importance
        if any(
            keyword in chunk.content.lower()
            for keyword in ["error", "important", "critical"]
        ):
            importance = min(1.0, importance + 0.1)

        return importance

    def _compress_context(self, chunks: List[ContextChunk]) -> List[ContextChunk]:
        """Compress context by summarizing low-importance chunks"""
        if not chunks:
            return chunks

        # Sort by importance (keep high-importance chunks)
        sorted_chunks = sorted(chunks, key=lambda c: c.importance, reverse=True)

        # Keep high-importance chunks, summarize the rest
        keep_threshold = 0.7
        keep_chunks = [c for c in sorted_chunks if c.importance >= keep_threshold]
        compress_chunks = [c for c in sorted_chunks if c.importance < keep_threshold]

        if not compress_chunks:
            return keep_chunks

        # Create summary of compressed chunks
        summary_content = self._create_summary(compress_chunks)
        summary_chunk = ContextChunk(
            content=summary_content,
            timestamp=datetime.now(),
            token_count=self._estimate_tokens(summary_content),
            importance=0.8,  # Summaries are important
            chunk_type="summary",
            source_id=f"summary_{len(compress_chunks)}_chunks",
            metadata={"original_chunks": len(compress_chunks)},
        )

        return keep_chunks + [summary_chunk]

    def _create_summary(self, chunks: List[ContextChunk]) -> str:
        """Create a summary of multiple context chunks"""
        if not chunks:
            return ""

        # Group chunks by type
        grouped = {}
        for chunk in chunks:
            chunk_type = chunk.chunk_type
            if chunk_type not in grouped:
                grouped[chunk_type] = []
            grouped[chunk_type].append(chunk)

        summary_parts = []
        for chunk_type, type_chunks in grouped.items():
            if chunk_type == "message":
                # Summarize conversation flow
                summary_parts.append(
                    f"Previous conversation included {len(type_chunks)} messages about: {self._extract_topics(type_chunks)}"
                )
            elif chunk_type == "document":
                # Summarize document content
                summary_parts.append(
                    f"Referenced {len(type_chunks)} documents covering: {self._extract_topics(type_chunks)}"
                )
            else:
                # Generic summary
                summary_parts.append(
                    f"Context included {len(type_chunks)} {chunk_type} items"
                )

        return "CONTEXT SUMMARY: " + " | ".join(summary_parts)

    def _extract_topics(self, chunks: List[ContextChunk]) -> str:
        """Extract key topics from chunks"""
        # Simple keyword extraction
        all_content = " ".join(chunk.content for chunk in chunks)
        words = all_content.lower().split()

        # Filter out common words and get meaningful terms
        stop_words = {
            "the",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "is",
            "are",
            "was",
            "were",
            "a",
            "an",
        }
        meaningful_words = [w for w in words if len(w) > 3 and w not in stop_words]

        # Get top 3 most frequent words
        word_freq = {}
        for word in meaningful_words:
            word_freq[word] = word_freq.get(word, 0) + 1

        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:3]
        return ", ".join(word for word, _ in top_words)

    def add_context(
        self,
        session_id: str,
        content: str,
        chunk_type: str = "message",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add new context to the session"""
        if session_id not in self.context_windows:
            self.context_windows[session_id] = ContextWindow(
                chunks=[],
                total_tokens=0,
                max_tokens=self.max_tokens,
                compression_ratio=1.0,
                last_updated=datetime.now(),
            )

        window = self.context_windows[session_id]

        # Create new chunk
        chunk = ContextChunk(
            content=content,
            timestamp=datetime.now(),
            token_count=self._estimate_tokens(content),
            importance=0.6,  # Default importance
            chunk_type=chunk_type,
            source_id=f"{session_id}_{len(window.chunks)}",
            metadata=metadata or {},
        )

        # Calculate importance
        chunk.importance = self._calculate_importance(chunk)

        # Add to window
        window.chunks.append(chunk)
        window.total_tokens += chunk.token_count
        window.last_updated = datetime.now()

        # Manage window size
        self._manage_window_size(session_id)

        # Persist if configured
        if self.persistence_dir:
            self._persist_context(session_id)

    def _manage_window_size(self, session_id: str) -> None:
        """Manage context window size using sliding window and compression"""
        window = self.context_windows[session_id]

        if window.total_tokens <= window.max_tokens:
            return

        # First, try compression
        if window.compression_ratio > 0.5:
            compressed_chunks = self._compress_context(window.chunks)
            new_total = sum(chunk.token_count for chunk in compressed_chunks)

            if new_total <= window.max_tokens:
                window.chunks = compressed_chunks
                window.total_tokens = new_total
                window.compression_ratio = new_total / (
                    window.total_tokens if window.total_tokens > 0 else 1
                )
                return

        # If compression isn't enough, use sliding window
        self._apply_sliding_window(session_id)

    def _apply_sliding_window(self, session_id: str) -> None:
        """Apply sliding window to remove old context"""
        window = self.context_windows[session_id]

        # Sort chunks by importance and recency
        sorted_chunks = sorted(
            window.chunks, key=lambda c: (c.importance, c.timestamp), reverse=True
        )

        # Keep chunks until we hit the token limit
        kept_chunks = []
        current_tokens = 0

        for chunk in sorted_chunks:
            if current_tokens + chunk.token_count <= window.max_tokens:
                kept_chunks.append(chunk)
                current_tokens += chunk.token_count
            else:
                break

        # Update window
        window.chunks = kept_chunks
        window.total_tokens = current_tokens
        window.compression_ratio = current_tokens / (
            window.total_tokens if window.total_tokens > 0 else 1
        )

    def get_context(self, session_id: str, max_tokens: Optional[int] = None) -> str:
        """Get formatted context for the session"""
        if session_id not in self.context_windows:
            return ""

        window = self.context_windows[session_id]
        effective_max = max_tokens or window.max_tokens

        # Sort chunks by timestamp for natural flow
        sorted_chunks = sorted(window.chunks, key=lambda c: c.timestamp)

        # Build context string
        context_parts = []
        current_tokens = 0

        for chunk in sorted_chunks:
            if current_tokens + chunk.token_count <= effective_max:
                if chunk.chunk_type == "system":
                    context_parts.append(f"[SYSTEM] {chunk.content}")
                elif chunk.chunk_type == "summary":
                    context_parts.append(f"[SUMMARY] {chunk.content}")
                else:
                    context_parts.append(chunk.content)
                current_tokens += chunk.token_count
            else:
                break

        return "\n".join(context_parts)

    def get_context_stats(self, session_id: str) -> Dict[str, Any]:
        """Get statistics about the context window"""
        if session_id not in self.context_windows:
            return {}

        window = self.context_windows[session_id]
        chunk_types = {}
        for chunk in window.chunks:
            chunk_types[chunk.chunk_type] = chunk_types.get(chunk.chunk_type, 0) + 1

        return {
            "total_chunks": len(window.chunks),
            "total_tokens": window.total_tokens,
            "max_tokens": window.max_tokens,
            "compression_ratio": window.compression_ratio,
            "chunk_types": chunk_types,
            "last_updated": window.last_updated.isoformat(),
            "utilization": window.total_tokens / window.max_tokens,
        }

    def _persist_context(self, session_id: str) -> None:
        """Persist context to disk with robust error handling"""
        if not self.persistence_dir or session_id not in self.context_windows:
            return

        try:
            # Ensure persistence directory exists
            if not self.persistence_dir.exists():
                try:
                    self.persistence_dir.mkdir(parents=True, exist_ok=True)
                    logger.debug(
                        f"Created persistence directory: {self.persistence_dir}"
                    )
                except Exception as dir_error:
                    logger.error(
                        f"Failed to create persistence directory {self.persistence_dir}: {dir_error}"
                    )
                    return

            window = self.context_windows[session_id]

            # Validate window data before persistence
            if not window.chunks:
                logger.warning(f"No chunks to persist for session {session_id}")
                return

            # Prepare data with enhanced error handling
            try:
                persist_data = {
                    "chunks": [],
                    "total_tokens": window.total_tokens,
                    "max_tokens": window.max_tokens,
                    "compression_ratio": window.compression_ratio,
                    "last_updated": window.last_updated.isoformat(),
                }

                # Convert chunks with individual error handling
                for i, chunk in enumerate(window.chunks):
                    try:
                        chunk_dict = asdict(chunk)
                        # Ensure timestamp is properly serialized
                        if isinstance(chunk_dict.get("timestamp"), datetime):
                            chunk_dict["timestamp"] = chunk_dict[
                                "timestamp"
                            ].isoformat()
                        persist_data["chunks"].append(chunk_dict)
                    except Exception as chunk_error:
                        logger.warning(
                            f"Failed to serialize chunk {i} for session {session_id}: {chunk_error}"
                        )
                        continue

            except Exception as data_error:
                logger.error(
                    f"Failed to prepare persistence data for session {session_id}: {data_error}"
                )
                return

            # Write to temporary file first for atomic operation
            file_path = self.persistence_dir / f"{session_id}.json"
            temp_file_path = self.persistence_dir / f"{session_id}.json.tmp"

            try:
                with open(temp_file_path, "w", encoding="utf-8") as f:
                    json.dump(
                        persist_data, f, indent=2, default=str, ensure_ascii=False
                    )

                # Atomic move to final location
                temp_file_path.rename(file_path)
                logger.debug(f"Successfully persisted context for session {session_id}")

            except Exception as write_error:
                logger.error(
                    f"Failed to write context file for session {session_id}: {write_error}"
                )
                # Clean up temporary file if it exists
                if temp_file_path.exists():
                    try:
                        temp_file_path.unlink()
                    except Exception:
                        pass
                return

        except Exception as e:
            logger.error(
                f"Failed to persist context for session {session_id}: {e}",
                exc_info=True,
            )

    def _load_persisted_contexts(self) -> None:
        """Load persisted contexts from disk"""
        if not self.persistence_dir or not self.persistence_dir.exists():
            return

        try:
            for file_path in self.persistence_dir.glob("*.json"):
                session_id = file_path.stem
                with open(file_path, "r") as f:
                    data = json.load(f)

                # Reconstruct chunks
                chunks = []
                for chunk_data in data.get("chunks", []):
                    chunk_data["timestamp"] = datetime.fromisoformat(
                        chunk_data["timestamp"]
                    )
                    chunks.append(ContextChunk(**chunk_data))

                # Create window
                window = ContextWindow(
                    chunks=chunks,
                    total_tokens=data.get("total_tokens", 0),
                    max_tokens=data.get("max_tokens", self.max_tokens),
                    compression_ratio=data.get("compression_ratio", 1.0),
                    last_updated=self._safe_parse_datetime(
                        data.get("last_updated", datetime.now().isoformat())
                    ),
                )

                self.context_windows[session_id] = window
                logger.info(f"Loaded persisted context for session {session_id}")

        except Exception as e:
            logger.error(f"Failed to load persisted contexts: {e}")

    def clear_session(self, session_id: str) -> None:
        """Clear context for a specific session"""
        if session_id in self.context_windows:
            del self.context_windows[session_id]

        # Remove persisted file
        if self.persistence_dir:
            file_path = self.persistence_dir / f"{session_id}.json"
            if file_path.exists():
                file_path.unlink()

    def cleanup_old_sessions(self, days_old: int = 7) -> None:
        """Clean up old sessions"""
        cutoff_time = datetime.now() - timedelta(days=days_old)

        to_remove = []
        for session_id, window in self.context_windows.items():
            if window.last_updated < cutoff_time:
                to_remove.append(session_id)

        for session_id in to_remove:
            self.clear_session(session_id)
            logger.info(f"Cleaned up old session: {session_id}")
