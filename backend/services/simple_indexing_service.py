# backend/services/simple_indexing_service.py
# Version 1.000
# Simple indexing service that bypasses LlamaIndex to avoid CUDA issues

import datetime
import logging
import os
import time
import threading
import json
from pathlib import Path
from typing import List, Optional, Dict, Any

# --- Logger Setup ---
logger = logging.getLogger(__name__)
# --- End Logger Setup ---

# --- Use absolute imports for custom parsers AND DB ---
try:
    from backend.models import Document as DBDocument
    from backend.models import db
    logger.info("Successfully imported db and DBDocument model.")
except ImportError as e:
    logger.critical(f"Failed to import local dependencies for simple_indexing_service: {e}.", exc_info=True)
    DBDocument = None
    db = None
# --- END IMPORTS ---

# Simple document storage
_document_store = {}
_index_lock = threading.Lock()

def update_document_status(doc_id: int, status: str, error_message: Optional[str] = None):
    """Update document status in database using a separate session."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.config import DATABASE_URL

    try:
        # Create a new engine and session to avoid transaction conflicts
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        session = Session()
        
        try:
            document = session.get(DBDocument, doc_id)
            if document:
                document.index_status = status
                document.error_message = error_message
                if status == "INDEXED":
                    document.indexed_at = datetime.datetime.now()
                session.commit()
                logger.info(f"Updated document {doc_id} status to {status}")
            else:
                logger.warning(f"Document {doc_id} not found in database")
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Failed to update document status: {e}")

def add_file_to_index(file_path: str, db_document: DBDocument) -> bool:
    """Simple file indexing without LlamaIndex."""
    logger.info(f"Starting simple indexing for: {file_path}")
    
    try:
        # Stage 1: File validation
        if not os.path.exists(file_path):
            logger.error(f"File path does not exist: {file_path}")
            update_document_status(db_document.id, "ERROR", f"File not found: {file_path}")
            return False
            
        # Stage 2: Read file content
        logger.info(f"Reading file content: {file_path}")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            # Try with different encoding
            try:
                with open(file_path, 'r', encoding='latin-1') as f:
                    content = f.read()
            except Exception as e:
                logger.error(f"Failed to read file {file_path}: {e}")
                return False
        
        # Stage 3: Process content
        logger.info(f"Processing content for document {db_document.id}")
        
        # Simple text processing
        lines = content.split('\n')
        chunks = []
        current_chunk = ""
        chunk_size = 1000  # characters per chunk
        
        for line in lines:
            if len(current_chunk) + len(line) > chunk_size and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = line
            else:
                current_chunk += line + "\n"
        
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        
        # Stage 4: Store in simple index
        with _index_lock:
            doc_key = f"doc_{db_document.id}"
            _document_store[doc_key] = {
                "id": db_document.id,
                "filename": db_document.filename,
                "content": content,
                "chunks": chunks,
                "metadata": {
                    "source_filename": db_document.filename,
                    "file_path": file_path,
                    "document_id": str(db_document.id),
                    "upload_date": db_document.uploaded_at.isoformat() if db_document.uploaded_at else None,
                    "project_id": str(db_document.project_id) if db_document.project_id else None,
                    "tags": db_document.tags,
                    "chunk_count": len(chunks),
                    "total_length": len(content)
                },
                "indexed_at": datetime.datetime.now().isoformat()
            }
        
        logger.info(f"Successfully indexed document {db_document.id} with {len(chunks)} chunks")
        return True
        
    except Exception as e:
        logger.error(f"Error indexing document {db_document.id}: {e}", exc_info=True)
        return False

def search_documents(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Simple search through indexed documents."""
    results = []
    
    with _index_lock:
        for doc_key, doc_data in _document_store.items():
            # Simple text search
            content_lower = doc_data["content"].lower()
            query_lower = query.lower()
            
            if query_lower in content_lower:
                # Calculate simple relevance score
                score = content_lower.count(query_lower) / len(content_lower.split())
                
                results.append({
                    "document_id": doc_data["id"],
                    "filename": doc_data["filename"],
                    "content_preview": doc_data["content"][:200] + "..." if len(doc_data["content"]) > 200 else doc_data["content"],
                    "score": score,
                    "metadata": doc_data["metadata"]
                })
    
    # Sort by score and return top_k
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]

def get_index_stats() -> Dict[str, Any]:
    """Get statistics about the simple index."""
    with _index_lock:
        return {
            "total_documents": len(_document_store),
            "total_chunks": sum(doc["metadata"]["chunk_count"] for doc in _document_store.values()),
            "total_content_length": sum(doc["metadata"]["total_length"] for doc in _document_store.values()),
            "documents": [
                {
                    "id": doc["id"],
                    "filename": doc["filename"],
                    "chunk_count": doc["metadata"]["chunk_count"],
                    "indexed_at": doc["indexed_at"]
                }
                for doc in _document_store.values()
            ]
        } 