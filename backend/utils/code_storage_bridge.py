# backend/utils/code_storage_bridge.py
# Bridge between database-stored code files and Guaardvark's unified index manager
# Enables RAG retrieval of stored code without duplicating content

# Force local LlamaIndex configuration BEFORE any LlamaIndex imports
import backend.utils.llama_index_local_config

import logging
import os
import json
from typing import List, Optional, Dict, Any
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# LlamaIndex imports
from llama_index.core import Document as LlamaDocument
from llama_index.core.schema import BaseNode

# Guaardvark imports
try:
    from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker
    from backend.utils.unified_index_manager import UnifiedIndexManager
except ImportError as e:
    logging.warning(f"Could not import Guaardvark components: {e}")
    EnhancedRAGChunker = None
    UnifiedIndexManager = None

logger = logging.getLogger(__name__)


# ============================================================================
# Database connection (PostgreSQL via SQLAlchemy)
# ============================================================================

def _get_database_url():
    url = os.environ.get('DATABASE_URL')
    if url:
        return url
    return "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"

_engine = None
_SessionFactory = None

def _get_db_session():
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_engine(_get_database_url(), pool_pre_ping=True)
        _SessionFactory = sessionmaker(bind=_engine)
    return _SessionFactory()


class CodeStorageBridge:
    """Bridge between database-stored code files and Guaardvark's indexing system"""

    def __init__(self, db_path: str, storage_dir: str):
        # db_path is kept for logging/compatibility but database access goes through SQLAlchemy
        self.db_path = db_path
        self.storage_dir = Path(storage_dir)

        # Initialize Guaardvark components
        if EnhancedRAGChunker:
            self.chunker = EnhancedRAGChunker()
        else:
            self.chunker = None

        if UnifiedIndexManager:
            self.index_manager = UnifiedIndexManager(str(self.storage_dir))
        else:
            self.index_manager = None

        logger.info(f"Code storage bridge initialized (db_path kept for reference: {db_path})")

    def get_stored_code_documents(self, project_id: Optional[int] = None) -> List[LlamaDocument]:
        """Retrieve stored code files from database as LlamaDocuments"""

        documents = []

        try:
            session = _get_db_session()

            try:
                # Query for indexed code files
                query = """
                    SELECT id, filename, content, file_metadata, size, indexed_at
                    FROM documents
                    WHERE is_code_file = TRUE AND index_status = 'INDEXED' AND content IS NOT NULL
                """

                params = {}

                if project_id is not None:
                    query += " AND project_id = :project_id"
                    params["project_id"] = project_id

                result = session.execute(text(query), params)
                rows = result.fetchall()

                for row in rows:
                    doc_id, filename, content, file_metadata_json, size, indexed_at = row

                    # Parse metadata
                    try:
                        file_metadata = json.loads(file_metadata_json) if file_metadata_json else {}
                    except json.JSONDecodeError:
                        file_metadata = {}

                    # Create enhanced metadata for LlamaIndex
                    metadata = {
                        'document_id': doc_id,
                        'filename': filename,
                        'file_extension': Path(filename).suffix,
                        'source': 'database_storage',
                        'indexed_at': indexed_at,
                        'size_bytes': size,
                        'storage_method': 'enhanced_code_aware',
                        **file_metadata  # Include enhanced metadata from indexing
                    }

                    # Create LlamaDocument
                    llama_doc = LlamaDocument(
                        text=content,
                        doc_id=f"stored_doc_{doc_id}",
                        metadata=metadata
                    )

                    documents.append(llama_doc)
                    logger.debug(f"Retrieved stored document: {filename} ({len(content)} chars)")

            finally:
                session.close()

            logger.info(f"Retrieved {len(documents)} stored code documents")
            return documents

        except Exception as e:
            logger.error(f"Failed to retrieve stored code documents: {e}")
            return []

    def chunk_stored_documents(self, documents: List[LlamaDocument]) -> List[BaseNode]:
        """Chunk stored documents using Guaardvark's CodeChunker"""

        if not self.chunker:
            logger.warning("CodeChunker not available")
            return []

        try:
            # Use auto-detection which will choose 'code' strategy for code files
            nodes = self.chunker.chunk_documents(documents, strategy_name='auto')

            logger.info(f"Chunked {len(documents)} documents into {len(nodes)} nodes")

            # Log chunking statistics
            stats = self.chunker.get_chunking_stats()
            logger.info(f"Chunking stats: {stats}")

            return nodes

        except Exception as e:
            logger.error(f"Failed to chunk stored documents: {e}")
            return []

    def create_or_update_index(self, project_id: Optional[int] = None,
                              force_rebuild: bool = False) -> bool:
        """Create or update the index with stored code files"""

        if not self.index_manager:
            logger.warning("Index manager not available")
            return False

        try:
            logger.info(f"Creating/updating index for project {project_id}")

            # Get stored documents
            documents = self.get_stored_code_documents(project_id)
            if not documents:
                logger.warning("No stored code documents found")
                return True  # Not an error, just nothing to index

            # Chunk documents
            nodes = self.chunk_stored_documents(documents)
            if not nodes:
                logger.warning("No chunks generated from documents")
                return False

            # Get or create index
            try:
                index, storage_context = self.index_manager.get_index(project_id, create_if_missing=True)

                if force_rebuild:
                    # Clear existing nodes and rebuild
                    # Note: This would require index manager method to clear nodes
                    logger.info("Force rebuild requested - would clear existing nodes")

                # Add nodes to index
                # Convert nodes to documents for insertion
                from llama_index.core import Document as LlamaDocument

                docs_to_insert = []
                for node in nodes:
                    # Clean metadata for JSON serialization
                    clean_metadata = {}
                    for key, value in node.metadata.items():
                        if isinstance(value, (str, int, float, bool, list, dict)):
                            clean_metadata[key] = value
                        else:
                            # Convert other types to string
                            clean_metadata[key] = str(value)

                    # Create a document from the node for insertion
                    doc = LlamaDocument(
                        text=node.get_content(),
                        metadata=clean_metadata,
                        doc_id=node.node_id
                    )
                    docs_to_insert.append(doc)

                # Insert documents into index
                for doc in docs_to_insert:
                    index.insert(doc)

                # Persist the updated index
                self.index_manager.persist_index(project_id)

                logger.info(f"Successfully updated index with {len(nodes)} nodes")
                return True

            except Exception as e:
                logger.error(f"Failed to update index: {e}")
                return False

        except Exception as e:
            logger.error(f"Failed to create/update index: {e}")
            return False

    def search_stored_code(self, query: str, project_id: Optional[int] = None,
                          top_k: int = 5) -> List[Dict[str, Any]]:
        """Search stored code files using the index"""

        if not self.index_manager:
            logger.warning("Index manager not available")
            return []

        try:
            # Get index
            index, storage_context = self.index_manager.get_index(project_id, create_if_missing=True)

            # Create retriever
            retriever = index.as_retriever(similarity_top_k=top_k)

            # Perform search
            retrieved_nodes = retriever.retrieve(query)

            # Format results
            results = []
            for node in retrieved_nodes:
                result = {
                    'content': node.get_content(),
                    'metadata': node.metadata,
                    'score': getattr(node, 'score', 0.0),
                    'document_id': node.metadata.get('document_id'),
                    'filename': node.metadata.get('filename'),
                    'file_extension': node.metadata.get('file_extension')
                }
                results.append(result)

            logger.info(f"Found {len(results)} results for query: {query[:50]}...")
            return results

        except Exception as e:
            logger.error(f"Failed to search stored code: {e}")
            return []

    def get_bridge_stats(self) -> Dict[str, Any]:
        """Get statistics about the code storage bridge"""

        try:
            session = _get_db_session()

            try:
                # Count documents by status
                result = session.execute(text("""
                    SELECT index_status, COUNT(*)
                    FROM documents
                    WHERE is_code_file = TRUE
                    GROUP BY index_status
                """))
                status_counts = dict(result.fetchall())

                # Count by file type
                result = session.execute(text("""
                    SELECT
                        CASE
                            WHEN filename LIKE '%.js' THEN 'JavaScript'
                            WHEN filename LIKE '%.jsx' THEN 'React JSX'
                            WHEN filename LIKE '%.ts' THEN 'TypeScript'
                            WHEN filename LIKE '%.tsx' THEN 'React TSX'
                            WHEN filename LIKE '%.py' THEN 'Python'
                            WHEN filename LIKE '%.java' THEN 'Java'
                            ELSE 'Other'
                        END as file_type,
                        COUNT(*)
                    FROM documents
                    WHERE is_code_file = TRUE AND index_status = 'INDEXED'
                    GROUP BY file_type
                """))
                type_counts = dict(result.fetchall())

                # Total content size
                result = session.execute(text("""
                    SELECT SUM(LENGTH(content))
                    FROM documents
                    WHERE is_code_file = TRUE AND index_status = 'INDEXED' AND content IS NOT NULL
                """))
                total_content_size = result.fetchone()[0] or 0

            finally:
                session.close()

            stats = {
                'status_counts': status_counts,
                'file_type_counts': type_counts,
                'total_content_size': total_content_size,
                'chunker_available': self.chunker is not None,
                'index_manager_available': self.index_manager is not None
            }

            if self.chunker:
                stats['chunking_stats'] = self.chunker.get_chunking_stats()

            return stats

        except Exception as e:
            logger.error(f"Failed to get bridge stats: {e}")
            return {'error': str(e)}


# Convenience functions for easy integration
def get_code_storage_bridge(db_path: str = None, storage_dir: str = None) -> CodeStorageBridge:
    """Get a configured code storage bridge instance"""

    if not db_path:
        # db_path is kept for compatibility/logging; actual DB access uses DATABASE_URL
        db_path = os.path.join(
            os.environ.get('GUAARDVARK_STORAGE_DIR', os.path.join(os.environ.get('GUAARDVARK_ROOT', '.'), 'data')),
            'database',
            'system_analysis.db'
        )

    if not storage_dir:
        storage_dir = os.path.join(
            os.environ.get('GUAARDVARK_STORAGE_DIR', os.path.join(os.environ.get('GUAARDVARK_ROOT', '.'), 'data')),
            'indexes'
        )

    return CodeStorageBridge(db_path, storage_dir)


def search_code_files(query: str, project_id: Optional[int] = None,
                     top_k: int = 5) -> List[Dict[str, Any]]:
    """Convenience function to search stored code files"""

    bridge = get_code_storage_bridge()
    return bridge.search_stored_code(query, project_id, top_k)


def update_code_index(project_id: Optional[int] = None,
                     force_rebuild: bool = False) -> bool:
    """Convenience function to update the code index"""

    bridge = get_code_storage_bridge()
    return bridge.create_or_update_index(project_id, force_rebuild)
