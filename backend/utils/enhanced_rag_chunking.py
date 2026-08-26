# backend/utils/enhanced_rag_chunking.py
# Enhanced RAG Chunking System
# Implements hierarchical and semantic chunking with intelligent content analysis

import os

# NLTK's CWE-427 import guard (nltk/inisec.py) blocks any module whose file resolves
# *under* the current working directory. This project's venv lives at backend/venv/ --
# inside the repo -- and the backend runs from the repo root, so every site-packages
# module looks like a CWD import and nltk cannot import its own `regex` dependency.
# Measured cost: chunking a 29-section document yielded 76 nodes with nltk blocked
# versus 115 with it working, and semantic chunking degraded to semantic_fallback.
#
# The guard defends against a hostile module shadowing a dependency from the CWD.
# That is not the situation here: the "CWD" is the application's own repository, and
# nothing attacker-writable is on sys.path (uploads land in data/uploads/, which is
# not an import path). Set before any import that can pull nltk in.
os.environ.setdefault("NLTK_DISABLE_IMPORT_SECURITY", "1")

# Force local LlamaIndex configuration BEFORE any LlamaIndex imports
import backend.utils.llama_index_local_config

import logging
import threading
import re
import hashlib
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from abc import ABC, abstractmethod

# Import chunk metadata cache
try:
    from backend.utils.chunk_metadata_cache import (
        get_cached_metadata,
        set_cached_metadata,
        get_cache_stats as get_chunk_cache_stats
    )
    CACHE_AVAILABLE = True
except ImportError:
    logger.warning("Chunk metadata cache not available, caching disabled")
    CACHE_AVAILABLE = False
    def get_cached_metadata(*args, **kwargs):
        return None
    def set_cached_metadata(*args, **kwargs):
        return False
    def get_chunk_cache_stats():
        return {}

# LlamaIndex imports
from llama_index.core import Document as LlamaDocument
from llama_index.core.node_parser import (
    NodeParser, SentenceSplitter, HierarchicalNodeParser, 
    SemanticSplitterNodeParser
)
from llama_index.core.schema import BaseNode, TextNode, NodeRelationship, RelatedNodeInfo
from llama_index.core.text_splitter import TokenTextSplitter

logger = logging.getLogger(__name__)

@dataclass
class ChunkMetadata:
    """Enhanced metadata for chunks"""
    chunk_id: str
    source_document: str
    chunk_type: str  # 'paragraph', 'sentence', 'semantic_block', 'table', 'code'
    content_type: str  # 'text', 'code', 'table', 'list', 'header'
    importance_score: float  # 0.0 to 1.0
    semantic_similarity: float  # Similarity to parent/sibling chunks
    token_count: int
    language: str
    topics: List[str]
    entities: List[str]
    relationships: Dict[str, List[str]]
    created_at: datetime
    chunk_position: int  # Position in original document
    parent_chunk_id: Optional[str] = None
    child_chunk_ids: List[str] = field(default_factory=list)

# Per-chunk bookkeeping that must never reach the embedding model. LlamaIndex
# concatenates the metadata string ahead of the chunk text before embedding, so
# anything left in here is paid for twice: once in tokens, and once in retrieval
# quality, because values that are near-identical across every chunk pull all the
# vectors toward each other. `chunk_id` is a hash, `created_at`/`cached_at` are
# timestamps, `relationships` is usually empty, and `entities` is every
# capitalised word in the chunk. None of it describes what the chunk is about.
# The values stay queryable for filters and citations -- only the embedding
# stops seeing them.
# Semantic splitting embeds every sentence window to find its breakpoints, and
# those chunks are then embedded again on insert -- the same text paid for twice,
# during the phase that is supposed to be cheap. It also mints its nodes on a path
# that bypasses the metadata exclusions below, so those chunks carry the full
# bookkeeping block into their embedding. Off by default until both are fixed;
# set GUAARDVARK_SEMANTIC_CHUNKING=true to re-enable.

# Document metadata worth embedding. Empty by design: contextual_prepender already
# writes the filename, heading path and page into the chunk text, so leaving those
# keys in the metadata budget would spend the chunk twice on the same words. A
# deployment that drops the contextual prefix should widen this instead.
EMBEDDABLE_DOC_METADATA = frozenset()


def _declare_embed_budget(document) -> None:
    """Exclude document metadata from the chunk-size budget, before splitting."""
    meta = getattr(document, "metadata", None) or {}
    if not meta:
        return
    drop = set(meta) - EMBEDDABLE_DOC_METADATA
    for attr in ("excluded_embed_metadata_keys", "excluded_llm_metadata_keys"):
        current = list(getattr(document, attr, None) or [])
        for key in drop:
            if key not in current:
                current.append(key)
        try:
            setattr(document, attr, current)
        except Exception:
            # Some loaders hand back objects that refuse attribute writes; the
            # post-split pass below still covers those.
            pass


SEMANTIC_CHUNKING_ENABLED = os.environ.get(
    "GUAARDVARK_SEMANTIC_CHUNKING", "false").lower() == "true"


NON_SEMANTIC_CHUNK_METADATA = frozenset({
    "cached_at", "chunk_id", "chunk_position", "chunk_type", "content_type",
    "created_at", "entities", "importance_score", "language", "original_text",
    "relationships", "source_document", "token_count", "topics",
})


@dataclass
class ChunkingStrategy:
    """Configuration for chunking strategy"""
    name: str
    max_chunk_size: int = 1000
    overlap_size: int = 200
    min_chunk_size: int = 100
    use_semantic_splitting: bool = True
    use_hierarchical_splitting: bool = True
    preserve_structure: bool = True
    extract_entities: bool = True
    calculate_importance: bool = True
    chunk_by_content_type: bool = True

class BaseChunker(ABC):
    """Base class for all chunkers"""
    
    def __init__(self, strategy: ChunkingStrategy):
        self.strategy = strategy
        self.chunk_cache = {}
        
    @abstractmethod
    def chunk_document(self, document: LlamaDocument) -> List[BaseNode]:
        """Chunk a document into nodes"""
        pass
    
    def _generate_chunk_id(self, content: str, source_id: str) -> str:
        """Generate a unique chunk ID"""
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        return f"{source_id}_{content_hash}"
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text"""
        # Simple approximation: ~4 characters per token
        return max(1, len(text) // 4)
    
    def _detect_content_type(self, text: str) -> str:
        """Detect the type of content"""
        text_lower = text.lower().strip()
        
        # Code patterns
        if any(pattern in text for pattern in ['def ', 'class ', 'import ', 'function', '```']):
            return 'code'
        
        # Table patterns
        if '|' in text and text.count('|') > 2:
            return 'table'
        
        # List patterns
        if re.match(r'^\s*[-*+]\s', text, re.MULTILINE) or re.match(r'^\s*\d+\.\s', text, re.MULTILINE):
            return 'list'
        
        # Header patterns
        if re.match(r'^#{1,6}\s', text) or text.isupper() and len(text) < 100:
            return 'header'
        
        return 'text'
    
    def _detect_language(self, text: str) -> str:
        """Simple language detection based on common patterns"""
        text_lower = text.lower().strip()
        
        # Check for common non-English patterns
        if any(char in text for char in ['ñ', 'ü', 'é', 'à', 'ç']):
            # Likely Spanish/French/German
            if 'que ' in text_lower or 'para ' in text_lower or 'con ' in text_lower:
                return 'es'  # Spanish
            elif 'pour ' in text_lower or 'avec ' in text_lower or 'dans ' in text_lower:
                return 'fr'  # French
            else:
                return 'de'  # German (fallback)
        
        # Check for code-like content
        if any(pattern in text_lower for pattern in ['function', 'class', 'import', 'def ', 'const ', 'var ']):
            return 'code'
        
        # Default to English for most content
        return 'en'
    
    def _extract_entities(self, text: str) -> List[str]:
        """Extract entities from text (simple implementation)"""
        entities = []
        
        # Extract capitalized words (potential proper nouns)
        capitalized_words = re.findall(r'\b[A-Z][a-z]+\b', text)
        entities.extend(capitalized_words)
        
        # Extract email addresses
        emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        entities.extend(emails)
        
        # Extract URLs
        urls = re.findall(r'https?://[A-Za-z0-9$\-_@.&+!*(),%/?=:#~;]+', text)
        entities.extend(urls)
        
        # Extract phone numbers
        phones = re.findall(r'\b\d{3}-\d{3}-\d{4}\b|\b\(\d{3}\)\s*\d{3}-\d{4}\b', text)
        entities.extend(phones)
        
        return list(set(entities))
    
    def _extract_topics(self, text: str) -> List[str]:
        """Extract topics from text using keyword frequency"""
        # Simple topic extraction based on word frequency
        words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        
        # Filter out common words
        stop_words = {
            'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
            'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does',
            'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can',
            'this', 'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
            'a', 'an', 'as', 'if', 'then', 'than', 'so', 'but', 'not', 'no', 'yes'
        }
        
        filtered_words = [word for word in words if len(word) > 3 and word not in stop_words]
        
        # Count word frequencies
        word_freq = defaultdict(int)
        for word in filtered_words:
            word_freq[word] += 1
        
        # Get top words as topics
        topics = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:5]
        return [topic[0] for topic in topics]
    
    def _calculate_importance(self, chunk: str, metadata: Dict[str, Any]) -> float:
        """Calculate importance score for a chunk"""
        importance = 0.5  # Base importance
        
        # Content type importance
        content_type = metadata.get('content_type', 'text')
        type_weights = {
            'header': 0.9,
            'code': 0.8,
            'table': 0.7,
            'list': 0.6,
            'text': 0.5
        }
        importance = type_weights.get(content_type, 0.5)
        
        # Length-based importance (longer chunks might be more important)
        length_bonus = min(0.2, len(chunk) / 10000)
        importance += length_bonus
        
        # Keyword-based importance
        important_keywords = ['important', 'critical', 'key', 'main', 'primary', 'summary']
        if any(keyword in chunk.lower() for keyword in important_keywords):
            importance += 0.1
        
        # Entity-rich content is more important
        entities = metadata.get('entities', [])
        if entities:
            importance += min(0.15, len(entities) * 0.03)
        
        return min(1.0, importance)


def _safe_hierarchical_overlap(max_chunk_size: int, overlap: int) -> int:
    """Clamp overlap so it stays proportionate to the SMALLEST hierarchical tier.

    HierarchicalNodeParser applies one chunk_overlap across tiers of
    [n, n/2, n/4]. An overlap sized for the largest tier is close to the whole of
    the smallest, collapsing the stride and multiplying node count. Returns at
    most a quarter of the smallest tier.
    """
    smallest = max(1, int(max_chunk_size) // 4)
    return max(0, min(int(overlap), smallest // 4))


class HierarchicalChunker(BaseChunker):
    """Hierarchical chunker that creates parent-child relationships"""
    
    def __init__(self, strategy: ChunkingStrategy):
        super().__init__(strategy)
        self.node_parser = HierarchicalNodeParser.from_defaults(
            chunk_sizes=[strategy.max_chunk_size, strategy.max_chunk_size // 2, strategy.max_chunk_size // 4],
            # One overlap applies to EVERY tier, so it has to be safe for the
            # smallest. The configured 1000/200 gives tiers [1000, 500, 250] and an
            # overlap of 200 -- a stride of 50 at the finest tier, i.e. 80% overlap,
            # which multiplies output several-fold per tier. Unnoticeable on small
            # sections; on a large one it turned 495 KB of source into 353,154 nodes.
            # Cap at a quarter of the smallest tier so overlap stays proportionate.
            chunk_overlap=_safe_hierarchical_overlap(
                strategy.max_chunk_size, strategy.overlap_size
            )
        )
    
    def chunk_document(self, document: LlamaDocument) -> List[BaseNode]:
        """Chunk document using hierarchical approach"""
        try:
            # Use LlamaIndex hierarchical parser
            nodes = self.node_parser.get_nodes_from_documents([document])
            
            # Enhance nodes with our metadata
            enhanced_nodes = []
            for i, node in enumerate(nodes):
                enhanced_metadata = self._create_enhanced_metadata(
                    node.get_content(),
                    document.doc_id or f"doc_{i}",
                    i
                )
                
                # BUG FIX #1: Preserve document-level metadata by merging in correct order
                # Order: enhanced_metadata (lowest priority) <- node.metadata <- document.metadata (highest priority)
                merged_metadata = {**enhanced_metadata, **node.metadata}
                if hasattr(document, 'metadata') and document.metadata:
                    merged_metadata.update(document.metadata)

                # Create enhanced node
                enhanced_node = TextNode(
                    text=node.get_content(),
                    metadata=merged_metadata,
                    id_=enhanced_metadata['chunk_id']
                )
                
                # Copy relationships
                enhanced_node.relationships = node.relationships
                
                enhanced_nodes.append(enhanced_node)
            
            return enhanced_nodes
            
        except Exception as e:
            logger.error(f"Error in hierarchical chunking: {e}")
            # Fallback to simple chunking
            return self._simple_chunk_document(document)
    
    def _create_enhanced_metadata(self, content: str, source_id: str, position: int) -> Dict[str, Any]:
        """Create enhanced metadata for a chunk with caching support"""
        # Generate content hash for cache key
        content_hash = hashlib.md5(content.encode()).hexdigest()
        
        # Try to get from cache first
        if CACHE_AVAILABLE:
            cached_metadata = get_cached_metadata(source_id, content_hash)
            if cached_metadata:
                # Update position and timestamp (these may change)
                cached_metadata['chunk_position'] = position
                cached_metadata['created_at'] = datetime.now(timezone.utc).isoformat()
                return cached_metadata
        
        # Calculate metadata if not cached
        chunk_id = self._generate_chunk_id(content, source_id)
        content_type = self._detect_content_type(content)
        entities = self._extract_entities(content) if self.strategy.extract_entities else []
        topics = self._extract_topics(content)
        
        metadata = {
            'chunk_id': chunk_id,
            'source_document': source_id,
            'chunk_type': 'hierarchical',
            'content_type': content_type,
            'token_count': self._estimate_tokens(content),
            'language': self._detect_language(content),
            'topics': topics,
            'entities': entities,
            'relationships': {},
            'created_at': datetime.now(timezone.utc).isoformat(),
            'chunk_position': position
        }
        
        if self.strategy.calculate_importance:
            metadata['importance_score'] = self._calculate_importance(content, metadata)
        
        # Cache the metadata
        if CACHE_AVAILABLE:
            set_cached_metadata(source_id, content_hash, metadata, ttl=86400)  # 24 hours
        
        return metadata
    
    def _simple_chunk_document(self, document: LlamaDocument) -> List[BaseNode]:
        """Fallback simple chunking"""
        text_splitter = TokenTextSplitter(
            chunk_size=self.strategy.max_chunk_size,
            chunk_overlap=self.strategy.overlap_size
        )
        
        chunks = text_splitter.split_text(document.get_content())
        nodes = []
        
        for i, chunk in enumerate(chunks):
            metadata = self._create_enhanced_metadata(
                chunk,
                document.doc_id or f"doc_{i}",
                i
            )

            # BUG FIX #1: Include document-level metadata in simple chunks
            if hasattr(document, 'metadata') and document.metadata:
                metadata.update(document.metadata)

            node = TextNode(
                text=chunk,
                metadata=metadata,
                id_=metadata['chunk_id']
            )
            nodes.append(node)
        
        return nodes

class SemanticChunker(BaseChunker):
    """Semantic chunker that groups related content"""
    
    def __init__(self, strategy: ChunkingStrategy):
        super().__init__(strategy)
        try:
            # ==========================================================================
            # PROTECTED CODE - Embedding Model Initialization
            # --------------------------------------------------------------------------
            # Uses get_local_embedding_model() which returns proper Ollama embeddings
            # (mxbai-embed-large with 1024 dimensions). Do NOT replace with
            # SimpleTextEmbedding or hash-based embeddings - causes dimension mismatch.
            # Changes require project owner permission. Last verified: 2026-01-31
            # ==========================================================================
            local_embed_model = backend.utils.llama_index_local_config.get_local_embedding_model()

            if local_embed_model:
                self.node_parser = SemanticSplitterNodeParser.from_defaults(
                    embed_model=local_embed_model,
                    buffer_size=1,
                    breakpoint_percentile_threshold=95
                )
                logger.info("SemanticSplitterNodeParser configured with local embeddings")
            else:
                logger.warning("Local embedding model not available, using fallback")
                self.node_parser = None
        except (ImportError, AttributeError) as e:
            logger.warning(f"SemanticSplitterNodeParser not available: {e}")
            self.node_parser = None
        except Exception as e:
            logger.error(f"Unexpected error configuring SemanticSplitterNodeParser: {e}")
            self.node_parser = None
    
    def chunk_document(self, document: LlamaDocument) -> List[BaseNode]:
        """Chunk document using semantic approach"""
        if self.node_parser is None:
            return self._fallback_semantic_chunking(document)
        
        try:
            # Use semantic parser
            nodes = self.node_parser.get_nodes_from_documents([document])
            
            # Enhance with metadata
            enhanced_nodes = []
            for i, node in enumerate(nodes):
                enhanced_metadata = self._create_enhanced_metadata(
                    node.get_content(),
                    document.doc_id or f"doc_{i}",
                    i
                )
                enhanced_metadata['chunk_type'] = 'semantic'

                # BUG FIX #1: Preserve document-level metadata
                merged_metadata = {**enhanced_metadata, **node.metadata}
                if hasattr(document, 'metadata') and document.metadata:
                    merged_metadata.update(document.metadata)

                enhanced_node = TextNode(
                    text=node.get_content(),
                    metadata=merged_metadata,
                    id_=enhanced_metadata['chunk_id']
                )
                
                enhanced_nodes.append(enhanced_node)
            
            return enhanced_nodes
            
        except Exception as e:
            logger.error(f"Error in semantic chunking: {e}")
            return self._fallback_semantic_chunking(document)
    
    def _fallback_semantic_chunking(self, document: LlamaDocument) -> List[BaseNode]:
        """Improved fallback semantic chunking with better heuristics"""
        content = document.get_content()
        
        if not content.strip():
            return []
        
        # Detect document structure
        has_headers = bool(re.search(r'^#{1,6}\s', content, re.MULTILINE))
        has_sections = content.count('\n\n') > 3
        has_lists = bool(re.search(r'^\s*[-*+]\s', content, re.MULTILINE))
        has_code_blocks = '```' in content or '```' in content
        
        # Strategy 1: If document has clear structure (headers, sections), use that
        if has_headers or (has_sections and len(content) > 2000):
            chunks = self._chunk_by_structure(content)
        # Strategy 2: If document has lists, preserve list boundaries
        elif has_lists:
            chunks = self._chunk_by_lists(content)
        # Strategy 3: If document has code blocks, preserve them
        elif has_code_blocks:
            chunks = self._chunk_preserving_code_blocks(content)
        # Strategy 4: Default to improved paragraph/sentence splitting
        else:
            chunks = self._chunk_by_paragraphs_improved(content)
        
        # Create nodes
        nodes = []
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
                
            metadata = self._create_enhanced_metadata(
                chunk,
                document.doc_id or f"doc_{i}",
                i
            )
            metadata['chunk_type'] = 'semantic_fallback'
            
            node = TextNode(
                text=chunk,
                metadata=metadata,
                id_=metadata['chunk_id']
            )
            nodes.append(node)
        
        return nodes
    
    def _chunk_by_structure(self, content: str) -> List[str]:
        """Chunk by document structure (headers, sections)"""
        chunks = []
        lines = content.split('\n')
        current_section = []
        
        for line in lines:
            # Detect section boundaries
            is_header = re.match(r'^#{1,6}\s', line)
            is_major_break = line.strip() == '' and len(current_section) > 0
            
            if is_header and current_section:
                # Start new section
                chunk = '\n'.join(current_section).strip()
                if chunk:
                    chunks.append(chunk)
                current_section = [line]
            elif is_major_break and len(current_section) > 10:
                # Major break in content
                chunk = '\n'.join(current_section).strip()
                if chunk:
                    chunks.append(chunk)
                current_section = []
            else:
                current_section.append(line)
        
        # Add final section
        if current_section:
            chunk = '\n'.join(current_section).strip()
            if chunk:
                chunks.append(chunk)
        
        return chunks
    
    def _chunk_by_lists(self, content: str) -> List[str]:
        """Chunk preserving list boundaries"""
        chunks = []
        lines = content.split('\n')
        current_chunk = []
        current_list = []
        
        for line in lines:
            is_list_item = re.match(r'^\s*[-*+]\s', line) or re.match(r'^\s*\d+\.\s', line)
            
            if is_list_item:
                current_list.append(line)
            else:
                if current_list:
                    # End of list, add to chunk
                    current_chunk.extend(current_list)
                    current_list = []
                
                if line.strip():
                    current_chunk.append(line)
                elif current_chunk:
                    # Empty line, potential chunk boundary
                    chunk = '\n'.join(current_chunk).strip()
                    if chunk and len(chunk) > self.strategy.min_chunk_size:
                        chunks.append(chunk)
                        current_chunk = []
        
        # Add remaining content
        if current_list:
            current_chunk.extend(current_list)
        if current_chunk:
            chunk = '\n'.join(current_chunk).strip()
            if chunk:
                chunks.append(chunk)
        
        return chunks
    
    def _chunk_preserving_code_blocks(self, content: str) -> List[str]:
        """Chunk preserving code block boundaries"""
        chunks = []
        parts = re.split(r'(```[^\n]*\n.*?\n```)', content, flags=re.DOTALL)
        
        current_chunk = ""
        for part in parts:
            if part.startswith('```'):
                # Code block - keep together
                if current_chunk and len(current_chunk) > self.strategy.min_chunk_size:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                chunks.append(part.strip())
            else:
                # Regular text
                if len(current_chunk) + len(part) < self.strategy.max_chunk_size:
                    current_chunk += part
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = part
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
    def _chunk_by_paragraphs_improved(self, content: str) -> List[str]:
        """Improved paragraph-based chunking with better grouping"""
        # Split by paragraphs first
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        
        # If no paragraph breaks, try sentence splitting
        if len(paragraphs) == 1:
            sentences = re.split(r'(?<=[.!?])\s+', content)
            paragraphs = [s.strip() for s in sentences if s.strip()]
        
        # Group paragraphs intelligently
        chunks = []
        current_chunk = ""
        
        for paragraph in paragraphs:
            # Calculate if adding this paragraph would exceed max size
            test_chunk = current_chunk + "\n\n" + paragraph if current_chunk else paragraph
            
            if len(test_chunk) <= self.strategy.max_chunk_size:
                current_chunk = test_chunk
            else:
                # Current chunk is full, save it
                if current_chunk:
                    chunks.append(current_chunk)
                
                # If paragraph itself is too large, split it
                if len(paragraph) > self.strategy.max_chunk_size:
                    # Split large paragraph by sentences
                    sentences = re.split(r'(?<=[.!?])\s+', paragraph)
                    temp_chunk = ""
                    for sentence in sentences:
                        if len(temp_chunk) + len(sentence) < self.strategy.max_chunk_size:
                            temp_chunk += " " + sentence if temp_chunk else sentence
                        else:
                            if temp_chunk:
                                chunks.append(temp_chunk)
                            temp_chunk = sentence
                    current_chunk = temp_chunk
                else:
                    current_chunk = paragraph
        
        # Add final chunk
        if current_chunk:
            chunks.append(current_chunk)
        
        return chunks
    
    def _create_enhanced_metadata(self, content: str, source_id: str, position: int) -> Dict[str, Any]:
        """Create enhanced metadata for semantic chunks with caching"""
        # Generate content hash for cache key
        content_hash = hashlib.md5(content.encode()).hexdigest()
        
        # Try to get from cache first
        if CACHE_AVAILABLE:
            cached_metadata = get_cached_metadata(source_id, content_hash)
            if cached_metadata:
                # Update position and timestamp (these may change)
                cached_metadata['chunk_position'] = position
                cached_metadata['created_at'] = datetime.now(timezone.utc).isoformat()
                cached_metadata['chunk_type'] = 'semantic'  # Ensure correct type
                return cached_metadata
        
        # Calculate metadata if not cached
        chunk_id = self._generate_chunk_id(content, source_id)
        content_type = self._detect_content_type(content)
        entities = self._extract_entities(content) if self.strategy.extract_entities else []
        topics = self._extract_topics(content)
        
        metadata = {
            'chunk_id': chunk_id,
            'source_document': source_id,
            'chunk_type': 'semantic',
            'content_type': content_type,
            'token_count': self._estimate_tokens(content),
            'language': self._detect_language(content),
            'topics': topics,
            'entities': entities,
            'relationships': {},
            'created_at': datetime.now(timezone.utc).isoformat(),
            'chunk_position': position
        }
        
        if self.strategy.calculate_importance:
            metadata['importance_score'] = self._calculate_importance(content, metadata)
        
        # Cache the metadata
        if CACHE_AVAILABLE:
            set_cached_metadata(source_id, content_hash, metadata, ttl=86400)  # 24 hours
        
        return metadata

class AdaptiveChunker(BaseChunker):
    """Adaptive chunker that selects the best strategy based on content"""
    
    def __init__(self, strategy: ChunkingStrategy):
        super().__init__(strategy)
        self.hierarchical_chunker = HierarchicalChunker(strategy)
        self.semantic_chunker = SemanticChunker(strategy)
    
    def chunk_document(self, document: LlamaDocument) -> List[BaseNode]:
        """Chunk document using adaptive approach"""
        content = document.get_content()
        
        # Analyze content to choose best strategy
        strategy_choice = self._analyze_content_for_strategy(content)
        
        if strategy_choice == 'hierarchical':
            return self.hierarchical_chunker.chunk_document(document)
        elif strategy_choice == 'semantic':
            return self.semantic_chunker.chunk_document(document)
        else:
            # Use hybrid approach
            return self._hybrid_chunking(document)
    
    def _analyze_content_for_strategy(self, content: str) -> str:
        """Analyze content to determine best chunking strategy"""
        # Check if content has clear hierarchical structure
        header_count = len(re.findall(r'^#{1,6}\s', content, re.MULTILINE))
        section_count = len(re.findall(r'\n\s*\n', content))
        
        # Check for code content
        code_indicators = ['def ', 'class ', 'function', 'import ', '```']
        code_score = sum(1 for indicator in code_indicators if indicator in content.lower())
        
        # Check for structured content
        list_count = len(re.findall(r'^\s*[-*+]\s', content, re.MULTILINE))
        table_count = content.count('|') // 3  # Rough table estimation
        
        # Decision logic
        if header_count > 3 or (section_count > 5 and len(content) > 5000):
            return 'hierarchical'
        elif code_score > 2 or table_count > 2:
            return 'hierarchical'
        elif section_count > 2 and len(content) > 2000:
            return 'semantic' if SEMANTIC_CHUNKING_ENABLED else 'hierarchical'
        else:
            return 'adaptive'
    
    def _hybrid_chunking(self, document: LlamaDocument) -> List[BaseNode]:
        """Hybrid chunking that combines strategies"""
        content = document.get_content()
        
        # Use hierarchical chunking first
        hierarchical_nodes = self.hierarchical_chunker.chunk_document(document)
        
        # For very large chunks, apply semantic chunking
        final_nodes = []
        for node in hierarchical_nodes:
            if len(node.get_content()) > self.strategy.max_chunk_size * 1.5:
                # Create a temporary document for semantic chunking
                temp_doc = LlamaDocument(
                    text=node.get_content(),
                    doc_id=f"{document.doc_id}_chunk_{node.id_}"
                )
                semantic_nodes = self.semantic_chunker.chunk_document(temp_doc)
                final_nodes.extend(semantic_nodes)
            else:
                final_nodes.append(node)
        
        return final_nodes

class CodeChunker(BaseChunker):
    """Specialized chunker for code files that preserves complete structure"""

    def __init__(self, strategy: ChunkingStrategy):
        super().__init__(strategy)
        self.code_extensions = {
            '.py': 'python',
            '.js': 'javascript',
            '.jsx': 'jsx',  # JSX needs distinct handling for React components
            '.ts': 'typescript',
            '.tsx': 'tsx',  # TSX is TypeScript + JSX, needs special handling
            '.html': 'html',
            '.htm': 'html',
            '.css': 'css',
            '.php': 'php',
            '.java': 'java',
            '.c': 'c',
            '.cpp': 'cpp',
            '.h': 'c',
            '.hpp': 'cpp',
            '.go': 'go',
            '.rs': 'rust',
            '.rb': 'ruby',
            '.sql': 'sql',
            '.json': 'json',
            '.xml': 'xml',
            '.yml': 'yaml',
            '.yaml': 'yaml'
        }

        # JSX-specific patterns for better chunking
        self.jsx_patterns = {
            'component_start': r'(?:export\s+(?:default\s+)?)?(?:function|const|class)\s+([A-Z][a-zA-Z0-9_]*)',
            'hook_usage': r'(?:use[A-Z][a-zA-Z0-9_]*|useState|useEffect|useContext|useCallback|useMemo)',
            'jsx_element': r'<[A-Z][a-zA-Z0-9_]*(?:\s+[^>]*)?(?:/>|>.*?</[A-Z][a-zA-Z0-9_]*>)',
            'import_react': r'import\s+(?:React|.*?)\s+from\s+[\'"]react[\'"]',
            'export_component': r'export\s+(?:default\s+)?[A-Z][a-zA-Z0-9_]*'
        }

        # Python-specific patterns for better chunking
        self.python_patterns = {
            'class_def': r'^class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?:',
            'function_def': r'^def\s+([a-z_][a-z0-9_]*)\s*\(',
            'method_def': r'^\s+def\s+([a-z_][a-z0-9_]*)\s*\(',
            'import_statement': r'^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_.]*)',
            'decorator': r'^@[a-zA-Z_][a-zA-Z0-9_.]*',
            'docstring': r'""".*?"""',
            'main_block': r'if __name__ == ["\']__main__["\']:',
            'exception_def': r'class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(?:.*Exception|.*Error)',
            'constant': r'^[A-Z_][A-Z0-9_]*\s*=',
            'type_hint': r':\s*(?:List|Dict|Optional|Union|Tuple|Set)\['
        }

    def chunk_document(self, document: LlamaDocument) -> List[BaseNode]:
        """Chunk code document preserving complete file structure"""
        content = document.get_content()
        metadata = document.metadata or {}

        # Determine if this is a code file
        file_extension = metadata.get('file_extension', '').lower()
        language = self.code_extensions.get(file_extension, 'text')

        # For code files, we want to preserve the complete structure
        if language != 'text':
            # Special handling for specific languages
            if language in ['jsx', 'tsx']:
                return self._chunk_jsx_file(document, language)
            elif language == 'python':
                return self._chunk_python_file(document, language)
            else:
                return self._chunk_code_file(document, language)
        else:
            # Fall back to standard chunking for non-code files
            return self._chunk_standard(document)

    def _chunk_code_file(self, document: LlamaDocument, language: str) -> List[BaseNode]:
        """Create chunks for code files while preserving complete structure"""
        content = document.get_content()
        metadata = document.metadata or {}

        nodes = []
        file_size = len(content)

        # For small to medium code files (< 50KB), keep them as single chunks
        if file_size <= 50000:
            chunk_metadata = ChunkMetadata(
                chunk_id=self._generate_chunk_id(content, document.doc_id),
                source_document=document.doc_id,
                chunk_type='complete_file',
                content_type='code',
                importance_score=0.9,  # Code files are high importance
                semantic_similarity=1.0,
                token_count=self._estimate_tokens(content),
                language=language,
                topics=self._extract_topics(content),
                entities=self._extract_entities(content),
                relationships={},
                created_at=datetime.now(timezone.utc),
                chunk_position=0
            )

            # Create enhanced metadata
            enhanced_metadata = {
                **metadata,
                'chunk_metadata': chunk_metadata.__dict__,
                'chunk_type': 'complete_file',
                'content_type': 'code',
                'programming_language': language,
                'file_size_chars': file_size,
                'preservation_mode': 'complete_file',
                'chunking_strategy': 'code_preserving'
            }

            node = TextNode(
                text=content,
                id_=chunk_metadata.chunk_id,
                metadata=enhanced_metadata
            )
            nodes.append(node)

        else:
            # For large code files (> 50KB), create intelligent chunks
            nodes = self._chunk_large_code_file(document, language)

        return nodes

    def _chunk_large_code_file(self, document: LlamaDocument, language: str) -> List[BaseNode]:
        """Chunk large code files by logical sections while preserving context"""
        content = document.get_content()
        metadata = document.metadata or {}
        nodes = []

        # Split by major code sections based on language
        if language in ['javascript', 'typescript']:
            sections = self._split_javascript_sections(content)
        elif language == 'python':
            sections = self._split_python_sections(content)
        elif language in ['html', 'xml']:
            sections = self._split_markup_sections(content)
        elif language == 'css':
            sections = self._split_css_sections(content)
        else:
            # Generic splitting for other languages
            sections = self._split_generic_code_sections(content)

        # Create nodes for each section
        for i, section in enumerate(sections):
            if not section.strip():
                continue

            chunk_metadata = ChunkMetadata(
                chunk_id=self._generate_chunk_id(section, f"{document.doc_id}_section_{i}"),
                source_document=document.doc_id,
                chunk_type='code_section',
                content_type='code',
                importance_score=0.8,
                semantic_similarity=0.9,
                token_count=self._estimate_tokens(section),
                language=language,
                topics=self._extract_topics(section),
                entities=self._extract_entities(section),
                relationships={},
                created_at=datetime.now(timezone.utc),
                chunk_position=i
            )

            # Create enhanced metadata
            enhanced_metadata = {
                **metadata,
                'chunk_metadata': chunk_metadata.__dict__,
                'chunk_type': 'code_section',
                'content_type': 'code',
                'programming_language': language,
                'section_index': i,
                'total_sections': len(sections),
                'preservation_mode': 'logical_sections',
                'chunking_strategy': 'code_preserving'
            }

            node = TextNode(
                text=section,
                id_=chunk_metadata.chunk_id,
                metadata=enhanced_metadata
            )
            nodes.append(node)

        return nodes

    def _split_javascript_sections(self, content: str) -> List[str]:
        """Split JavaScript/TypeScript code by functions and classes"""
        sections = []
        lines = content.split('\n')
        current_section = []
        indent_level = 0

        for line in lines:
            stripped = line.strip()

            # Track brace-based indentation
            indent_level += line.count('{') - line.count('}')

            # Start new section on top-level function/class declarations
            if (indent_level <= 1 and
                (stripped.startswith(('function ', 'class ', 'const ', 'let ', 'var ', 'export ')) or
                 'function' in stripped or '= (' in stripped or '=>' in stripped)):

                if current_section:
                    sections.append('\n'.join(current_section))
                    current_section = []

            current_section.append(line)

        if current_section:
            sections.append('\n'.join(current_section))

        return sections

    def _split_python_sections(self, content: str) -> List[str]:
        """Split Python code by functions and classes with better structure preservation"""
        import re
        sections = []
        lines = content.split('\n')

        # Extract imports and module-level content first
        imports_section = []
        main_sections = []
        current_section = []
        in_class = False
        class_indent = 0

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Collect imports and module docstrings at the beginning
            if i < 50 and (stripped.startswith(('import ', 'from ')) or
                          (stripped.startswith('"""') or stripped.startswith("'''")) or
                          stripped.startswith('#') or stripped == ''):
                imports_section.append(line)
                continue

            # Detect class and function definitions with proper indentation
            if stripped.startswith('class '):
                if current_section:
                    main_sections.append('\n'.join(current_section))
                    current_section = []
                current_section.extend(imports_section)  # Include imports in each section
                current_section.append(line)
                in_class = True
                class_indent = len(line) - len(line.lstrip())

            elif stripped.startswith('def '):
                # Handle both top-level functions and class methods
                line_indent = len(line) - len(line.lstrip())

                # If it's a top-level function or we're not in a class
                if line_indent == 0 or not in_class:
                    if current_section and not in_class:
                        main_sections.append('\n'.join(current_section))
                        current_section = []
                    if not current_section:  # Starting new section
                        current_section.extend(imports_section)
                    current_section.append(line)
                    in_class = False
                else:
                    # Method inside class - keep with current section
                    current_section.append(line)

            elif line.strip() == '' or line.startswith(' ') or line.startswith('\t'):
                # Empty lines or indented content - belongs to current section
                current_section.append(line)

            else:
                # Top-level code - check if we should start new section
                if stripped.startswith(('if __name__', '@')):
                    # Keep decorators and main blocks with current section
                    current_section.append(line)
                else:
                    # Other top-level code
                    if in_class and len(line) - len(line.lstrip()) <= class_indent:
                        in_class = False
                    current_section.append(line)

        # Add the final section
        if current_section:
            main_sections.append('\n'.join(current_section))

        # If no main sections found, return the whole content
        if not main_sections:
            return [content]

        return main_sections

    def _split_markup_sections(self, content: str) -> List[str]:
        """Split HTML/XML by major sections"""
        # Simple approach: split by major HTML tags
        import re

        # Find major section boundaries
        section_tags = ['<html', '<head', '<body', '<main', '<section', '<article', '<div class', '<div id']

        sections = []
        current_pos = 0

        for match in re.finditer(r'<(html|head|body|main|section|article)\b[^>]*>', content, re.IGNORECASE):
            if current_pos < match.start():
                section = content[current_pos:match.start()].strip()
                if section:
                    sections.append(section)
            current_pos = match.start()

        # Add remaining content
        if current_pos < len(content):
            remaining = content[current_pos:].strip()
            if remaining:
                sections.append(remaining)

        # If no major sections found, keep as single chunk
        if not sections:
            sections = [content]

        return sections

    def _split_css_sections(self, content: str) -> List[str]:
        """Split CSS by logical sections"""
        # Split by major CSS blocks
        import re

        sections = []
        current_section = []
        lines = content.split('\n')

        for line in lines:
            stripped = line.strip()

            # Start new section on top-level selectors (not nested)
            if (stripped and not stripped.startswith(('@', '/*', '*/', '}')) and
                '{' in stripped and not line.startswith(' ')):

                if current_section:
                    sections.append('\n'.join(current_section))
                    current_section = []

            current_section.append(line)

        if current_section:
            sections.append('\n'.join(current_section))

        return sections

    def _split_generic_code_sections(self, content: str) -> List[str]:
        """Generic code splitting for unknown languages"""
        # Simple line-based chunking with reasonable size limits
        lines = content.split('\n')
        sections = []
        current_section = []
        current_size = 0
        max_section_size = self.strategy.max_chunk_size

        for line in lines:
            current_section.append(line)
            current_size += len(line) + 1  # +1 for newline

            # Split when reaching size limit
            if current_size >= max_section_size:
                sections.append('\n'.join(current_section))
                current_section = []
                current_size = 0

        if current_section:
            sections.append('\n'.join(current_section))

        return sections

    def _chunk_jsx_file(self, document: LlamaDocument, language: str) -> List[BaseNode]:
        """Create chunks for JSX files with React-specific awareness"""
        import re
        content = document.get_content()
        metadata = document.metadata or {}

        nodes = []
        file_size = len(content)

        # For small to medium JSX files (< 30KB), keep them as single chunks
        if file_size <= 30000:
            jsx_metadata = self._extract_jsx_metadata(content)

            chunk_metadata = ChunkMetadata(
                chunk_id=self._generate_chunk_id(content, document.doc_id),
                source_document=document.doc_id,
                chunk_type='complete_jsx_component',
                content_type='jsx',
                importance_score=0.95,
                semantic_similarity=1.0,
                token_count=self._estimate_tokens(content),
                language=language,
                topics=self._extract_topics(content),
                entities=self._extract_entities(content),
                relationships={},
                created_at=datetime.now(timezone.utc),
                chunk_position=0
            )

            enhanced_metadata = {
                **metadata,
                'chunk_metadata': chunk_metadata.__dict__,
                'chunk_type': 'complete_jsx_component',
                'content_type': 'jsx',
                'programming_language': language,
                'file_size_chars': file_size,
                'preservation_mode': 'complete_component',
                'chunking_strategy': 'jsx_component_preserving',
                **jsx_metadata
            }

            node = TextNode(
                text=content,
                id_=chunk_metadata.chunk_id,
                metadata=enhanced_metadata
            )
            nodes.append(node)
        else:
            # For large JSX files, use standard code chunking
            nodes = self._chunk_large_code_file(document, language)

        return nodes

    def _extract_jsx_metadata(self, content: str) -> dict:
        """Extract JSX-specific metadata from content"""
        import re

        metadata = {}

        # Extract component names
        component_matches = re.findall(self.jsx_patterns['component_start'], content)
        metadata['components'] = list(set(component_matches)) if component_matches else []

        # Extract hooks usage
        hook_matches = re.findall(self.jsx_patterns['hook_usage'], content)
        metadata['hooks_used'] = list(set(hook_matches)) if hook_matches else []

        # Check for React imports
        metadata['has_react_import'] = bool(re.search(self.jsx_patterns['import_react'], content))

        # Check for exports
        metadata['has_export'] = bool(re.search(self.jsx_patterns['export_component'], content))

        # Count JSX elements
        jsx_elements = re.findall(self.jsx_patterns['jsx_element'], content, re.DOTALL)
        metadata['jsx_elements_count'] = len(jsx_elements)

        # Determine component type
        if 'function ' in content or 'const ' in content and '=>' in content:
            metadata['component_type'] = 'functional'
        elif 'class ' in content and 'extends' in content:
            metadata['component_type'] = 'class'
        else:
            metadata['component_type'] = 'unknown'

        return metadata

    def _chunk_python_file(self, document: LlamaDocument, language: str) -> List[BaseNode]:
        """Create chunks for Python files with Python-specific awareness"""
        import re
        content = document.get_content()
        metadata = document.metadata or {}

        nodes = []
        file_size = len(content)

        # For small to medium Python files (< 40KB), keep them as single chunks
        if file_size <= 40000:
            python_metadata = self._extract_python_metadata(content)

            chunk_metadata = ChunkMetadata(
                chunk_id=self._generate_chunk_id(content, document.doc_id),
                source_document=document.doc_id,
                chunk_type='complete_python_module',
                content_type='python',
                importance_score=0.9,
                semantic_similarity=1.0,
                token_count=self._estimate_tokens(content),
                language=language,
                topics=self._extract_topics(content),
                entities=self._extract_entities(content),
                relationships={},
                created_at=datetime.now(timezone.utc),
                chunk_position=0
            )

            enhanced_metadata = {
                **metadata,
                'chunk_metadata': chunk_metadata.__dict__,
                'chunk_type': 'complete_python_module',
                'content_type': 'python',
                'programming_language': language,
                'file_size_chars': file_size,
                'preservation_mode': 'complete_module',
                'chunking_strategy': 'python_module_preserving',
                **python_metadata
            }

            node = TextNode(
                text=content,
                id_=chunk_metadata.chunk_id,
                metadata=enhanced_metadata
            )
            nodes.append(node)

        else:
            # For large Python files, use improved sectioning
            nodes = self._chunk_large_python_file(document, language)

        return nodes

    def _extract_python_metadata(self, content: str) -> dict:
        """Extract Python-specific metadata from content"""
        import re

        metadata = {}

        # Extract classes
        class_matches = re.findall(self.python_patterns['class_def'], content, re.MULTILINE)
        metadata['classes'] = list(set(class_matches)) if class_matches else []

        # Extract functions
        function_matches = re.findall(self.python_patterns['function_def'], content, re.MULTILINE)
        metadata['functions'] = list(set(function_matches)) if function_matches else []

        # Extract methods (indented def statements)
        method_matches = re.findall(self.python_patterns['method_def'], content, re.MULTILINE)
        metadata['methods'] = list(set(method_matches)) if method_matches else []

        # Extract imports
        import_matches = re.findall(self.python_patterns['import_statement'], content, re.MULTILINE)
        metadata['imports'] = list(set(import_matches)) if import_matches else []

        # Extract decorators
        decorator_matches = re.findall(self.python_patterns['decorator'], content, re.MULTILINE)
        metadata['decorators'] = list(set(decorator_matches)) if decorator_matches else []

        # Check for docstrings
        metadata['has_docstring'] = bool(re.search(self.python_patterns['docstring'], content, re.DOTALL))

        # Check for main block
        metadata['has_main_block'] = bool(re.search(self.python_patterns['main_block'], content))

        # Check for exception definitions
        exception_matches = re.findall(self.python_patterns['exception_def'], content)
        metadata['exceptions'] = list(set(exception_matches)) if exception_matches else []

        # Check for constants
        constant_matches = re.findall(self.python_patterns['constant'], content, re.MULTILINE)
        metadata['constants'] = list(set(constant_matches)) if constant_matches else []

        # Check for type hints
        metadata['has_type_hints'] = bool(re.search(self.python_patterns['type_hint'], content))

        # Determine module type
        if metadata['classes'] and not metadata['functions']:
            metadata['module_type'] = 'class_module'
        elif metadata['functions'] and not metadata['classes']:
            metadata['module_type'] = 'function_module'
        elif metadata['classes'] and metadata['functions']:
            metadata['module_type'] = 'mixed_module'
        elif metadata['exceptions']:
            metadata['module_type'] = 'exception_module'
        elif metadata['has_main_block']:
            metadata['module_type'] = 'script'
        else:
            metadata['module_type'] = 'utility'

        return metadata

    def _chunk_large_python_file(self, document: LlamaDocument, language: str) -> List[BaseNode]:
        """Chunk large Python files by classes and functions with imports preserved"""
        content = document.get_content()
        metadata = document.metadata or {}
        nodes = []

        # Use the improved Python sectioning
        sections = self._split_python_sections(content)

        for i, section in enumerate(sections):
            if section.strip():
                python_metadata = self._extract_python_metadata(section)

                chunk_metadata = ChunkMetadata(
                    chunk_id=self._generate_chunk_id(section, f"{document.doc_id}_python_section_{i}"),
                    source_document=document.doc_id,
                    chunk_type='python_code_section',
                    content_type='python',
                    importance_score=0.85,
                    semantic_similarity=0.8,
                    token_count=self._estimate_tokens(section),
                    language=language,
                    topics=self._extract_topics(section),
                    entities=self._extract_entities(section),
                    relationships={},
                    created_at=datetime.now(timezone.utc),
                    chunk_position=i
                )

                enhanced_metadata = {
                    **metadata,
                    'chunk_metadata': chunk_metadata.__dict__,
                    'chunk_type': 'python_code_section',
                    'content_type': 'python',
                    'programming_language': language,
                    'section_index': i,
                    'total_sections': len(sections),
                    'chunking_strategy': 'python_sectioning',
                    **python_metadata
                }

                node = TextNode(
                    text=section,
                    id_=chunk_metadata.chunk_id,
                    metadata=enhanced_metadata
                )
                nodes.append(node)

        return nodes

    # Prose sizing for the non-code branch of the code chunker. The code strategy
    # runs at 8000 tokens to keep functions whole; applying that to prose yields
    # ~32k-character chunks that are far too coarse to retrieve against -- one chunk
    # covers several unrelated topics, so its embedding means nothing in particular.
    PROSE_CHUNK_SIZE = 1000
    PROSE_CHUNK_OVERLAP = 200

    def _chunk_standard(self, document: LlamaDocument) -> List[BaseNode]:
        """Standard chunking for non-code files"""
        # Deliberately NOT self.strategy.max_chunk_size: this method is reached when
        # the code chunker is handed content that is not code -- which happens
        # whenever prose trips the code detector, e.g. a design document quoting
        # snippets. Inheriting the code size there produced chunks averaging 15k
        # characters and peaking at 42k.
        splitter = SentenceSplitter(
            chunk_size=min(self.strategy.max_chunk_size, self.PROSE_CHUNK_SIZE),
            chunk_overlap=min(self.strategy.overlap_size, self.PROSE_CHUNK_OVERLAP)
        )

        nodes = splitter.get_nodes_from_documents([document])

        # Enhance metadata for standard chunks
        for i, node in enumerate(nodes):
            if hasattr(node, 'metadata'):
                node.metadata = node.metadata or {}
                node.metadata.update({
                    'chunk_type': 'standard_text',
                    'content_type': 'text',
                    'chunking_strategy': 'sentence_splitting',
                    'chunk_index': i
                })

        return nodes


def _ensure_source_relationship(nodes, document) -> int:
    """Point every chunk back at the document it came from. Returns how many were fixed.

    LlamaIndex derives a node's `ref_doc_id` from its SOURCE relationship, and the
    vector store writes that into `document_id` -- falling back to the literal
    string "None" when it is missing. A chunk with "None" there cannot be purged,
    cannot be replaced on re-index, and is not removed when its document is
    deleted, so it accumulates silently. Measured on a live corpus: 1,404 of
    36,522 rows, 3.8%, across every parser.

    Only some chunkers inherit the relationship -- the hierarchical path copies it
    from the splitter's node, while the paths that build a node from a raw text
    chunk have no parent node to copy from. Setting it here, once, after whichever
    chunker ran, covers them all and cannot be missed by the next one added.
    """
    try:
        from llama_index.core.schema import NodeRelationship, RelatedNodeInfo
    except Exception:  # noqa: BLE001
        return 0
    doc_id = getattr(document, "id_", None) or getattr(document, "doc_id", None)
    if not doc_id:
        return 0
    fixed = 0
    for n in nodes:
        rels = getattr(n, "relationships", None)
        if rels is None:
            try:
                n.relationships = {}
                rels = n.relationships
            except Exception:  # noqa: BLE001
                continue
        if rels.get(NodeRelationship.SOURCE) is None:
            rels[NodeRelationship.SOURCE] = RelatedNodeInfo(
                node_id=doc_id,
                metadata=dict(getattr(document, "metadata", None) or {}),
            )
            fixed += 1
    return fixed


class EnhancedRAGChunker:
    """Main chunking coordinator that manages different chunking strategies"""

    def __init__(self, default_strategy: Optional[ChunkingStrategy] = None):
        self.default_strategy = default_strategy or ChunkingStrategy(
            name="default",
            max_chunk_size=1000,
            overlap_size=200,
            use_semantic_splitting=True,
            use_hierarchical_splitting=True
        )

        # Create code-specific strategy with larger chunk sizes for better preservation
        self.code_strategy = ChunkingStrategy(
            name="code_preserving",
            max_chunk_size=8000,  # Larger chunks for code
            overlap_size=400,     # More overlap to maintain context
            use_semantic_splitting=False,  # Disable semantic splitting for code
            use_hierarchical_splitting=False,
            preserve_structure=True,
            chunk_by_content_type=True
        )

        self.chunkers = {
            'hierarchical': HierarchicalChunker(self.default_strategy),
            'semantic': SemanticChunker(self.default_strategy),
            'adaptive': AdaptiveChunker(self.default_strategy),
            'code': CodeChunker(self.code_strategy)
        }
        
        self.chunking_stats = {
            'total_documents': 0,
            'total_chunks': 0,
            'chunking_errors': 0,
            'strategy_usage': defaultdict(int)
        }
    
    def _detect_best_chunking_strategy(self, document: LlamaDocument) -> str:
        """Automatically detect the best chunking strategy for a document"""
        metadata = document.metadata or {}
        file_extension = metadata.get('file_extension', '').lower()

        # Code file extensions that should use the code chunker
        code_extensions = {
            '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.htm', '.css',
            '.php', '.java', '.c', '.cpp', '.h', '.hpp', '.go', '.rs',
            '.rb', '.sql', '.json', '.xml', '.yml', '.yaml'
        }

        if file_extension in code_extensions:
            return 'code'

        # Fallback: analyze content for code patterns if no file extension
        if not file_extension:
            content = document.get_content()
            if content:
                content_lower = content.lower()
                # Check for common code patterns
                code_patterns = [
                    'def ', 'class ', 'function ', 'import ', 'require(',
                    '<?php', '<html', '#!/', 'package ', 'public class',
                    'const ', 'let ', 'var ', '=>', 'function(', 'return',
                    '{', '}', '#include', 'SELECT ', 'CREATE TABLE'
                ]

                # If content has multiple code indicators, likely a code file
                code_indicator_count = sum(1 for pattern in code_patterns if pattern in content_lower)
                if code_indicator_count >= 3:  # Threshold for code detection
                    logger.info(f"Content-based code detection: {code_indicator_count} patterns found")
                    return 'code'

        # Use adaptive for other files
        return 'adaptive'

    def _reduce_to_leaf_nodes(self, nodes: List[BaseNode]) -> List[BaseNode]:
        """Drop hierarchical parent nodes, keeping only the leaves.

        HierarchicalNodeParser emits every tier -- a 1000-token parent, its
        500-token children, their 250-token children -- and a parent contains its
        children's text verbatim. Indexing all tiers therefore embeds the same
        prose two or three times (measured: 1.95x the nodes, 43% of the embedded
        characters duplicated), doubles the vectors stored, and lets one query
        match both a parent and its own child so the same passage is returned
        twice at different granularities.

        Leaves go to the vector store, the docstore and BM25 alike, so every
        retrieval leg sees one consistent granularity. Set
        GUAARDVARK_INDEX_LEAF_NODES_ONLY=false to index every tier instead.
        """
        if os.environ.get("GUAARDVARK_INDEX_LEAF_NODES_ONLY", "true").lower() != "true":
            return nodes
        if not nodes:
            return nodes
        try:
            from llama_index.core.node_parser import get_leaf_nodes
            leaves = get_leaf_nodes(nodes)
        except Exception as e:
            logger.debug(f"leaf-node reduction skipped: {e}")
            return nodes
        # A flat parser returns every node as a leaf; only act on a real hierarchy,
        # and never reduce to nothing.
        if not leaves or len(leaves) >= len(nodes):
            return nodes
        logger.info(
            "Hierarchical chunking: indexing %d leaf node(s), dropping %d parent copies",
            len(leaves), len(nodes) - len(leaves),
        )
        return leaves

    def chunk_documents(self, documents: List[LlamaDocument],
                       strategy_name: str = 'auto') -> List[BaseNode]:
        """Chunk multiple documents using specified strategy"""
        all_nodes = []

        for document in documents:
            try:
                # Auto-detect strategy if requested
                if strategy_name == 'auto':
                    actual_strategy = self._detect_best_chunking_strategy(document)
                else:
                    actual_strategy = strategy_name

                if actual_strategy not in self.chunkers:
                    logger.warning(f"Unknown strategy '{actual_strategy}', using adaptive")
                    actual_strategy = 'adaptive'

                # Declare the embed budget on the DOCUMENT, before it is split.
                # LlamaIndex sizes each chunk as `chunk_size - len(metadata)`, and it
                # reads that metadata from the document being split, not from the nodes
                # coming out. Excluding keys afterwards therefore fixes what gets
                # embedded but not what gets *counted* -- so a document carrying a long
                # path, a heading breadcrumb, tags and notes silently loses most of its
                # chunk to metadata it was never going to embed. Measured on a real
                # corpus: a nominal 250-token tier left as little as 44 tokens of text.
                #
                # Set here, the exclusions propagate to every split
                # (build_nodes_from_splits copies them), so the budget reflects what is
                # actually embedded. Nothing is lost: the values stay on the nodes for
                # filtering and citation, and contextual_prepender writes source,
                # section and page into the chunk text itself.
                _declare_embed_budget(document)

                chunker = self.chunkers[actual_strategy]
                nodes = chunker.chunk_document(document)

                # Whatever chunker ran, every chunk must know which document it
                # came from -- that link is what makes it purgeable, replaceable
                # and deletable later.
                _orphaned = _ensure_source_relationship(nodes, document)
                if _orphaned:
                    logger.debug("Set source relationship on %d/%d chunk(s) from %s",
                                 _orphaned, len(nodes),
                                 (getattr(document, "metadata", None) or {}).get(
                                     "source_filename", "?"))

                # Carry the source document's metadata onto every chunk. The per-chunk
                # metadata is built fresh by _create_enhanced_metadata, which never sees
                # the document, so without this the file name, page label and heading
                # path are lost at chunk time -- and a chunk that cannot name its source
                # cannot be cited. setdefault so chunk-specific keys always win.
                doc_meta = getattr(document, "metadata", None) or {}
                if doc_meta:
                    for _n in nodes:
                        if getattr(_n, "metadata", None) is None:
                            _n.metadata = {}
                        for _k, _v in doc_meta.items():
                            _n.metadata.setdefault(_k, _v)

                        # Keep the carried keys out of the embed/LLM metadata budget.
                        # LlamaIndex counts metadata against chunk size, and the
                        # hierarchical splitter's smallest tier is a quarter of the
                        # configured size -- a long file path plus a heading breadcrumb
                        # overruns it and the whole document drops to a fallback
                        # splitter ("Metadata length (409) is longer than chunk size
                        # (250)"). The information is not lost: it stays queryable for
                        # filters and citations, and contextual_prepender already puts
                        # source, section and page into the text itself, so leaving it
                        # in the metadata budget would double-count it.
                        try:
                            for _attr in ("excluded_embed_metadata_keys",
                                          "excluded_llm_metadata_keys"):
                                _cur = list(getattr(_n, _attr, None) or [])
                                _drop = set(doc_meta) | (
                                    set(_n.metadata) & NON_SEMANTIC_CHUNK_METADATA)
                                for _k in _drop:
                                    if _k not in _cur:
                                        _cur.append(_k)
                                setattr(_n, _attr, _cur)
                        except Exception:
                            pass

                all_nodes.extend(nodes)

                self.chunking_stats['total_documents'] += 1
                self.chunking_stats['total_chunks'] += len(nodes)
                self.chunking_stats['strategy_usage'][actual_strategy] += 1

                logger.info(f"Chunked document {document.doc_id} using '{actual_strategy}' strategy: {len(nodes)} chunks")

            except Exception as e:
                logger.error(f"Error chunking document {document.doc_id}: {e}")
                self.chunking_stats['chunking_errors'] += 1

        all_nodes = self._reduce_to_leaf_nodes(all_nodes)

        return all_nodes
    
    def get_chunking_stats(self) -> Dict[str, Any]:
        """Get chunking statistics including cache performance"""
        stats = {
            'total_documents': self.chunking_stats['total_documents'],
            'total_chunks': self.chunking_stats['total_chunks'],
            'chunking_errors': self.chunking_stats['chunking_errors'],
            'strategy_usage': dict(self.chunking_stats['strategy_usage']),
            'average_chunks_per_document': (
                self.chunking_stats['total_chunks'] / self.chunking_stats['total_documents']
                if self.chunking_stats['total_documents'] > 0 else 0
            )
        }
        
        # Add cache statistics if available
        if CACHE_AVAILABLE:
            try:
                cache_stats = get_chunk_cache_stats()
                stats['cache'] = cache_stats
            except Exception as e:
                logger.warning(f"Failed to get cache stats: {e}")
                stats['cache'] = {'error': str(e)}
        
        return stats
    
    def add_custom_strategy(self, name: str, strategy: ChunkingStrategy, 
                          chunker_class: type) -> None:
        """Add a custom chunking strategy"""
        self.chunkers[name] = chunker_class(strategy)
        logger.info(f"Added custom chunking strategy: {name}")
    
    def optimize_strategy_for_content(self, content_sample: str) -> str:
        """Recommend optimal chunking strategy for given content"""
        # Analyze content characteristics
        length = len(content_sample)
        line_count = content_sample.count('\n')
        
        # Check for structured content
        has_headers = bool(re.search(r'^#{1,6}\s', content_sample, re.MULTILINE))
        has_code = any(indicator in content_sample.lower() 
                      for indicator in ['def ', 'class ', 'function', 'import '])
        has_lists = bool(re.search(r'^\s*[-*+]\s', content_sample, re.MULTILINE))
        
        # Improved recommendation logic
        recommended_strategy = 'adaptive'  # Default fallback
        
        if has_headers and length > 5000:
            recommended_strategy = 'hierarchical'
        elif has_code or has_lists:
            recommended_strategy = 'hierarchical'
        elif length > 1000 and (line_count / length * 1000) < 10:  # Dense text with few line breaks
            recommended_strategy = 'semantic'
        
        # Validate that the recommended strategy exists
        if recommended_strategy not in self.chunkers:
            logger.warning(f"Recommended strategy '{recommended_strategy}' not available, using adaptive")
            recommended_strategy = 'adaptive'
        
        return recommended_strategy 


_shared_chunker = None
_shared_chunker_lock = threading.Lock()


def get_shared_chunker() -> "EnhancedRAGChunker":
    """One chunker for the process, built on first use.

    Constructing an EnhancedRAGChunker is not cheap: it eagerly builds four
    chunkers, and two of them are SemanticChunkers that each construct an
    OllamaEmbedding -- a pair of httpx clients and a settings lookup apiece. Doing
    that per document meant four HTTP clients and two database round-trips built
    and discarded for every file ingested. The chunkers hold configuration, not
    per-document state, so one instance serves the whole process.

    `chunking_stats` becomes cumulative across documents rather than per call;
    it is a counter, and callers that want per-document figures read the returned
    node list instead.
    """
    global _shared_chunker
    if _shared_chunker is None:
        with _shared_chunker_lock:
            if _shared_chunker is None:
                _shared_chunker = EnhancedRAGChunker()
    return _shared_chunker
