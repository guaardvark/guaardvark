
import datetime
import logging
import os
import time
import threading
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

from backend.utils.experiment_context import get_experiment_config
import backend.utils.llama_index_local_config

if os.environ.get('CELERY_WORKER_MODE', 'false').lower() == 'true':
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    logger.info("CUDA disabled for Celery worker - using CPU")
else:
    os.environ['CUDA_VISIBLE_DEVICES'] = '0'
    logger.info("CUDA enabled for indexing service - using GPU acceleration")
    
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

os.environ['OMP_NUM_THREADS'] = '2'
os.environ['MKL_NUM_THREADS'] = '2'
os.environ['NUMEXPR_NUM_THREADS'] = '2'

if os.environ.get('CELERY_WORKER_MODE', 'false').lower() != 'true':
    os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'max_split_size_mb:512,expandable_segments:True,roundup_power2_divisions:16'
    os.environ['CUDA_LAUNCH_BLOCKING'] = '0'
    logger.info("CUDA memory management optimized for GPU acceleration")

LlamaDocument = None
ServiceContext = None
Settings = None
StorageContext = None
VectorStoreIndex = None
load_index_from_storage = None
IngestionPipeline = None
HierarchicalNodeParser = None
get_leaf_nodes = None
SimpleDirectoryReader = None
SimpleDocumentStore = None
SimpleIndexStore = None

def _validate_settings() -> bool:
    try:
        from llama_index.core import Settings
        
        if Settings.llm is None:
            logger.warning("LLM not configured in Settings")
            return False
        
        if Settings.embed_model is None:
            logger.warning("Embed model not configured in Settings")
            return False
        
        if not hasattr(Settings.llm, 'model_name') and not hasattr(Settings.llm, 'model'):
            logger.warning("LLM appears to be improperly initialized")
            return False
        
        if not hasattr(Settings.embed_model, 'model_name') and not hasattr(Settings.embed_model, 'embed_batch_size'):
            logger.warning("Embed model appears to be improperly initialized")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Error validating LlamaIndex Settings: {e}")
        return False

def _lazy_load_llamaindex():
    global LlamaDocument, ServiceContext, Settings, StorageContext, VectorStoreIndex
    global load_index_from_storage, IngestionPipeline, HierarchicalNodeParser
    global get_leaf_nodes, SimpleDirectoryReader, SimpleDocumentStore, SimpleIndexStore
    
    if LlamaDocument is not None:
        return
    
    try:
        try:
            from backend.utils.llama_index_local_config import force_local_llama_index_config
            force_local_llama_index_config()
        except Exception as e:
            logger.error(f"Failed to force local LlamaIndex config in indexing_service: {e}")
        
        from llama_index.core import Document as LlamaDocument
        from llama_index.core import (
            ServiceContext, Settings, StorageContext, VectorStoreIndex,
            load_index_from_storage)
        from llama_index.core.ingestion import IngestionPipeline
        from llama_index.core.node_parser import (HierarchicalNodeParser,
                                                  get_leaf_nodes)
        from llama_index.core.readers import SimpleDirectoryReader
        from llama_index.core.storage.docstore import SimpleDocumentStore
        from llama_index.core.storage.index_store import SimpleIndexStore
        
        logger.info("Successfully loaded LlamaIndex components in CPU-only mode")
        
    except Exception as e:
        logger.error(f"Failed to load LlamaIndex components: {e}")
        raise

SimpleVectorStore = None
PDFReaderClass = None

def _lazy_load_optional_components():
    global SimpleVectorStore, PDFReaderClass
    
    try:
        from llama_index.core.vector_stores import SimpleVectorStore
    except Exception:
        logger.warning("SimpleVectorStore import failed; vector store must be provided explicitly")
    
    try:
        from llama_index.readers.file import PDFReader
        PDFReaderClass = PDFReader
        logger.info("Successfully imported PDFReader from llama_index.readers.file")
    except ImportError:
        logger.warning("Could not import PDFReader. PDF parsing will use SimpleDirectoryReader if available.")

try:
    from backend.models import Document as DBDocument
    from backend.models import db
    from backend.utils.csv_chunker import parse_csv_rows
    from backend.utils.xml_sitemap_handler import parse_sitemap
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType

    logger.info("Successfully imported custom parsers, db, and DBDocument model.")
except ImportError as e:
    logger.critical(
        f"Failed to import local dependencies for indexing_service: {e}.", exc_info=True
    )
    parse_csv_rows = None
    parse_sitemap = None
    DBDocument = None
    db = None

index: Optional[VectorStoreIndex] = None
storage_context: Optional[StorageContext] = None

_index_operation_lock = threading.RLock()
_persistence_lock = threading.Lock()


def query_index(query_text, project_id=None, top_k=3):
    try:
        result = get_or_create_index(project_id=project_id)
        index = result[0] if isinstance(result, tuple) else result
        if index:
            query_engine = index.as_query_engine(similarity_top_k=top_k)
            response = query_engine.query(query_text)
            return response
        return None
    except Exception as e:
        logger.error(f"Error querying index: {e}")
        return None

def get_or_create_index(project_id: Optional[str] = None):
    try:
        from flask import current_app
        flask_available = True
    except ImportError:
        flask_available = False
        current_app = None

    global index, storage_context

    from backend.config import INDEX_ROOT, PROJECT_INDEX_MODE

    index_mode = os.getenv("GUAARDVARK_PROJECT_INDEX_MODE", PROJECT_INDEX_MODE)
    index_root = os.getenv("GUAARDVARK_INDEX_ROOT", INDEX_ROOT)

    key = "global"
    persist_dir = index_root
    
    if index_mode == "per_project" and project_id:
        key = str(project_id)
        persist_dir = os.path.join(index_root, str(project_id))

    # Get the global index manager for access_stats tracking
    try:
        from backend.utils.unified_index_manager import get_global_index_manager
        uim = get_global_index_manager()
    except Exception:
        uim = None

    if flask_available and current_app:
        try:
            cache = current_app.config.setdefault("INDEX_CACHE", {})
            if key in cache:
                cached = cache[key]
                index, storage_context = cached["index"], cached["storage_context"]
                if uim:
                    uim.access_stats['total_loads'] += 1
                    uim.access_stats['cache_hits'] += 1
                return index, storage_context, persist_dir
        except RuntimeError:
            logger.debug("No Flask app context available, skipping index cache")
    else:
        logger.debug("Flask not available, skipping index cache")

    _initialize_index(persist_dir)

    if index is not None and storage_context is not None and flask_available and current_app:
        try:
            cache = current_app.config.setdefault("INDEX_CACHE", {})
            cache[key] = {"index": index, "storage_context": storage_context}
            if uim:
                uim.access_stats['total_loads'] += 1
                uim.access_stats['cache_misses'] += 1
                uim.access_stats['index_creates'] += 1
        except (RuntimeError, NameError):
            logger.debug("No Flask app context available for storing index cache")
    else:
        logger.debug("Flask not available or no index, skipping cache storage")
        if uim:
            uim.access_stats['total_loads'] += 1
            uim.access_stats['cache_misses'] += 1

    return index, storage_context, persist_dir


def _initialize_index(storage_path: str):
    global index, storage_context

    with _index_operation_lock:
        _lazy_load_llamaindex()
        _lazy_load_optional_components()

    if index is not None and storage_context is not None:
        current_persist_dir = getattr(storage_context, "persist_dir", None)
        if current_persist_dir and os.path.abspath(
            current_persist_dir
        ) == os.path.abspath(storage_path):
            logger.info(
                f"Index already initialized and storage path matches: {storage_path}. Skipping re-initialization."
            )
            return
        else:
            logger.warning(
                f"Index was previously initialized but with a different storage_path or context. Re-initializing with new path: {storage_path}"
            )
            index = None
            storage_context = None

    logger.info(
        f"Attempting to initialize LlamaIndex from storage path: {storage_path}"
    )
    
    if not isinstance(storage_path, str) or not storage_path:
        logger.error(
            "Invalid storage_path (must be a non-empty string) provided for index initialization."
        )
        index = None
        storage_context = None
        return

    abs_storage_path = os.path.abspath(storage_path)
    
    if "/storage" in abs_storage_path or "\\storage" in abs_storage_path or abs_storage_path.endswith("/storage") or abs_storage_path.endswith("\\storage"):
        from backend.config import INDEX_ROOT
        abs_storage_path = os.path.abspath(INDEX_ROOT)
        logger.warning(f"Prevented use of legacy storage folder, redirecting to {abs_storage_path}")
    
    docstore_file_path = Path(abs_storage_path) / "docstore.json"

    should_create_new = False
    if not os.path.isdir(abs_storage_path):
        logger.warning(
            f"Storage directory does not exist: {abs_storage_path}. Will create."
        )
        try:
            os.makedirs(abs_storage_path, exist_ok=True)
            logger.info(f"Created storage directory: {abs_storage_path}")
            should_create_new = True
        except OSError as e:
            logger.error(
                f"Failed to create storage directory {abs_storage_path}: {e}",
                exc_info=True,
            )
            index = None
            storage_context = None
            return
    elif not docstore_file_path.exists():
        logger.warning(
            f"Storage directory {abs_storage_path} exists, but 'docstore.json' (key index file) is missing. Will create new empty index."
        )
        should_create_new = True
    else:
        try:
            import json
            with open(docstore_file_path, 'r') as f:
                docstore_data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            logger.warning(f"Failed to read or parse docstore.json: {e}. Will create new empty index.")
            should_create_new = True
            docstore_data = {}
        
        if not should_create_new:
            ref_doc_info = docstore_data.get('docstore/ref_doc_info', {})
            if not ref_doc_info:
                # Empty docstore is normal — no documents have been indexed yet.
                # Load it as-is instead of deleting and recreating every restart.
                logger.info(f"Index at {abs_storage_path} exists with no documents yet. Loading as-is.")

    if should_create_new:
            logger.info(f"Creating new empty LlamaIndex structure at: {abs_storage_path}")
            if not _validate_settings():
                logger.error(
                    "Cannot create new index: LLM or Embed Model not properly configured in LlamaIndex global Settings."
                )
                index = None
                storage_context = None
                return
            try:
                docstore_instance = SimpleDocumentStore()
                index_store_instance = SimpleIndexStore()

                storage_defaults = {
                    "docstore": docstore_instance,
                    "index_store": index_store_instance,
                    "persist_dir": abs_storage_path,
                }

                try:
                    from inspect import signature

                    sig_params = signature(StorageContext.from_defaults).parameters
                    if SimpleVectorStore and "vector_store" in sig_params:
                        storage_defaults["vector_store"] = SimpleVectorStore()
                except Exception:
                    if SimpleVectorStore:
                        try:
                            storage_defaults["vector_store"] = SimpleVectorStore()
                        except Exception:
                            pass

                storage_context_instance = StorageContext.from_defaults(**storage_defaults)

                index_instance = VectorStoreIndex.from_documents(
                    [],
                    storage_context=storage_context_instance,
                )
                storage_context_instance.persist(persist_dir=abs_storage_path)

                index = index_instance
                storage_context = storage_context_instance
                logger.info(
                    f"Successfully created and persisted new empty index at: {abs_storage_path}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to create or persist new empty index at {abs_storage_path}: {e}",
                    exc_info=True,
                )
                index = None
                storage_context = None
    else:
        try:
            logger.info(f"Attempting to load existing index from: {abs_storage_path}")
            storage_context_instance = StorageContext.from_defaults(
                persist_dir=abs_storage_path
            )
            index_instance = load_index_from_storage(storage_context_instance)

            index = index_instance
            storage_context = storage_context_instance
            logger.info(f"Successfully loaded index from {abs_storage_path}")
        except Exception as e:
            # Common case: storage dir has docstore.json but index_store.json was purged,
            # or one of the state files is corrupted. Rather than leaving the index unusable
            # (which cascades into BrainState.is_ready=False and kills the Reflex tier),
            # rebuild an empty index in place so the system can still respond to chat.
            logger.warning(
                f"Load failed at {abs_storage_path}: {e}. "
                f"Storage appears incomplete — rebuilding as an empty index so chat stays alive."
            )
            if not _validate_settings():
                logger.error(
                    "Cannot rebuild index: LLM or Embed Model not properly configured in LlamaIndex global Settings."
                )
                index = None
                storage_context = None
            else:
                try:
                    docstore_instance = SimpleDocumentStore()
                    index_store_instance = SimpleIndexStore()

                    storage_defaults = {
                        "docstore": docstore_instance,
                        "index_store": index_store_instance,
                        "persist_dir": abs_storage_path,
                    }

                    try:
                        from inspect import signature

                        sig_params = signature(StorageContext.from_defaults).parameters
                        if SimpleVectorStore and "vector_store" in sig_params:
                            storage_defaults["vector_store"] = SimpleVectorStore()
                    except Exception:
                        if SimpleVectorStore:
                            try:
                                storage_defaults["vector_store"] = SimpleVectorStore()
                            except Exception:
                                pass

                    storage_context_instance = StorageContext.from_defaults(**storage_defaults)
                    index_instance = VectorStoreIndex.from_documents(
                        [],
                        storage_context=storage_context_instance,
                    )
                    storage_context_instance.persist(persist_dir=abs_storage_path)

                    index = index_instance
                    storage_context = storage_context_instance
                    logger.info(
                        f"Rebuilt empty index at {abs_storage_path} after load failure."
                    )
                except Exception as rebuild_err:
                    logger.error(
                        f"Rebuild after load failure also failed at {abs_storage_path}: {rebuild_err}",
                        exc_info=True,
                    )
                    index = None
                    storage_context = None


def deduplicate_chunks(chunks: list, similarity_threshold: float = 0.85) -> list:
    """Remove near-duplicate retrieved chunks based on embedding similarity."""
    if len(chunks) <= 1:
        return chunks

    try:
        import ollama as ollama_client
        from backend.config import CHUNK_SIMILARITY_THRESHOLD
        threshold = similarity_threshold or CHUNK_SIMILARITY_THRESHOLD

        exp_config = get_experiment_config()
        if exp_config and "dedup_threshold" in exp_config:
            threshold = exp_config["dedup_threshold"]

        texts = [c.get("text", "") if isinstance(c, dict) else getattr(c, "text", str(c)) for c in chunks]
        embeddings = []
        for text in texts:
            resp = ollama_client.embeddings(model="nomic-embed-text", prompt=text[:500])
            embeddings.append(resp["embedding"])

        import numpy as np
        emb_array = np.array(embeddings)
        norms = np.linalg.norm(emb_array, axis=1, keepdims=True)
        norms[norms == 0] = 1
        normalized = emb_array / norms
        sim_matrix = normalized @ normalized.T

        keep = set(range(len(chunks)))
        for i in range(len(chunks)):
            if i not in keep:
                continue
            for j in range(i + 1, len(chunks)):
                if j not in keep:
                    continue
                if sim_matrix[i][j] > threshold:
                    score_i = chunks[i].get("score", 0) if isinstance(chunks[i], dict) else getattr(chunks[i], "score", 0)
                    score_j = chunks[j].get("score", 0) if isinstance(chunks[j], dict) else getattr(chunks[j], "score", 0)
                    keep.discard(j if score_i >= score_j else i)

        deduped = [chunks[i] for i in sorted(keep)]
        if len(deduped) < len(chunks):
            logger.info(f"Deduplicated {len(chunks)} chunks to {len(deduped)}")
        return deduped

    except Exception as e:
        logger.warning(f"Chunk deduplication failed: {e}")
        return chunks


def search_with_llamaindex(query: str, max_chunks: int = 5, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    global index

    try:
        with _index_operation_lock:
            local_index = index
            if local_index is None:
                logger.warning("search_with_llamaindex: Index not available, attempting to load...")
                get_or_create_index(project_id=str(project_id) if project_id else None)
                local_index = index

        if local_index is None:
            logger.error("search_with_llamaindex: Failed to load index")
            return []

        if not query or not isinstance(query, str):
            logger.warning("search_with_llamaindex: Invalid query input")
            return []

        max_chunks = max(1, min(max_chunks, 50))

        # Apply experiment config overrides (autoresearch)
        exp_config = get_experiment_config()
        if exp_config:
            effective_top_k = exp_config.get("top_k", max_chunks)
            max_chunks = exp_config.get("context_window_chunks", max_chunks)
        else:
            effective_top_k = max_chunks

        if project_id is not None:
            try:
                from llama_index.core.vector_stores.types import MetadataFilters, MetadataFilter, FilterOperator
                metadata_filters = MetadataFilters(
                    filters=[
                        MetadataFilter(key="project_id", value=str(project_id), operator=FilterOperator.EQ)
                    ]
                )
                base_retriever = local_index.as_retriever(similarity_top_k=effective_top_k, filters=metadata_filters)
            except Exception:
                logger.debug("search_with_llamaindex: MetadataFilters not available, falling back to unfiltered")
                base_retriever = local_index.as_retriever(similarity_top_k=effective_top_k)
        else:
            base_retriever = local_index.as_retriever(similarity_top_k=effective_top_k)
        # Hybrid search: add BM25 retrieval alongside vector search
        hybrid_alpha = float(os.environ.get("GUAARDVARK_HYBRID_SEARCH_ALPHA", "0.3"))
        retriever = base_retriever  # Default to vector-only

        if hybrid_alpha > 0.0 and storage_context is not None:
            try:
                from llama_index.retrievers.bm25 import BM25Retriever
                from llama_index.core.retrievers import QueryFusionRetriever

                bm25_retriever = BM25Retriever.from_defaults(
                    docstore=storage_context.docstore,
                    similarity_top_k=effective_top_k,
                )
                retriever = QueryFusionRetriever(
                    retrievers=[base_retriever, bm25_retriever],
                    similarity_top_k=effective_top_k,
                    num_queries=1,
                    mode="reciprocal_rerank",
                )
                logger.info(f"Using hybrid search (BM25 + vector, alpha={hybrid_alpha})")
            except ImportError:
                logger.debug("BM25Retriever not available, using vector-only search")
            except Exception as e:
                logger.debug(f"Hybrid search setup failed, using vector-only: {e}")

        from llama_index.core.schema import QueryBundle

        if isinstance(query, str):
            query_bundle = QueryBundle(query_str=query)
        else:
            query_bundle = query

        nodes = retriever.retrieve(query_bundle)
        
        results = []
        for node_with_score in nodes:
            node = node_with_score.node if hasattr(node_with_score, 'node') else node_with_score
            score = node_with_score.score if hasattr(node_with_score, 'score') else 0.0
            
            result = {
                'text': node.get_content(),
                'score': score,
                'metadata': node.metadata if hasattr(node, 'metadata') else {},
                'node_id': node.node_id if hasattr(node, 'node_id') else None
            }
            
            if 'source_filename' not in result['metadata']:
                result['metadata']['source_filename'] = result['metadata'].get('filename', 'Unknown')
                
            results.append(result)
            
        logger.info(f"search_with_llamaindex: Retrieved {len(results)} results for query: {query[:50]}... (project_id={project_id})")

        # Deduplicate near-identical chunks
        results = deduplicate_chunks(results)

        # Expand results with cross-file dependency context
        try:
            from backend.utils.context_expander import expand_with_dependencies
            results = expand_with_dependencies(results)
        except Exception as e:
            logger.debug(f"Context expansion skipped: {e}")

        # Fallback: if project-scoped search returned 0 results, retry with global scope
        if not results and project_id is not None:
            logger.info(f"search_with_llamaindex: No project-scoped results, falling back to global search")
            return search_with_llamaindex(query, max_chunks=max_chunks, project_id=None)

        return results

    except Exception as e:
        err_msg = str(e)
        if "not aligned" in err_msg or "dim 0" in err_msg or ("4096" in err_msg and "384" in err_msg):
            logger.warning(
                "search_with_llamaindex failed: Vector index was built with a different embedding model. "
                "Please use Settings to reset/rebuild the index and re-upload documents. Details: %s",
                err_msg[:200],
            )
        else:
            logger.error(f"search_with_llamaindex failed: {e}", exc_info=True)
        return []


def add_text_to_index(text: str, metadata: Dict[str, Any], project_id: Optional[str] = None) -> bool:
    global index, storage_context

    try:
        # Ensure project_id is stored in document metadata for retrieval filtering
        if project_id and 'project_id' not in metadata:
            metadata['project_id'] = str(project_id)

        local_index = index
        if local_index is None:
            logger.warning("add_text_to_index: Index not available, attempting to load...")
            get_or_create_index(project_id)
            local_index = index

        if local_index is None:
            logger.error("add_text_to_index: Failed to load index")
            return False

        _lazy_load_llamaindex()

        document = LlamaDocument(text=text, metadata=metadata)
        
        from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker
        rag_chunker = EnhancedRAGChunker()

        nodes = rag_chunker.chunk_documents([document], strategy_name='auto')
        
        with _index_operation_lock:
            if not nodes or len(nodes) == 0:
                logger.warning("BUG FIX 3: No valid nodes to insert, skipping")
                return False
            
            valid_nodes = []
            for node in nodes:
                if hasattr(node, 'text') and node.text and hasattr(node, 'metadata'):
                    valid_nodes.append(node)
                else:
                    logger.warning(f"BUG FIX 3: Skipping invalid node: {type(node)}")
            
            if not valid_nodes:
                logger.error("BUG FIX 3: No valid nodes after validation")
                return False

            local_index.insert_nodes(valid_nodes)

            with _persistence_lock:
                persist_dir = getattr(storage_context, "persist_dir", None)
                if not persist_dir:
                    from backend.config import INDEX_ROOT
                    persist_dir = INDEX_ROOT
                if persist_dir and ("/storage" in persist_dir or "\\storage" in persist_dir or persist_dir.endswith("/storage") or persist_dir.endswith("\\storage")):
                    from backend.config import INDEX_ROOT
                    persist_dir = INDEX_ROOT
                    logger.warning(f"Prevented use of legacy storage folder, using {persist_dir} instead")
                storage_context.persist(persist_dir=persist_dir)

        logger.info(f"add_text_to_index: Successfully added text with {len(nodes)} nodes")

        # Notify autoresearch that corpus has changed
        try:
            from backend.celery_app import celery_app as _celery
            _celery.send_task("autoresearch.on_index_complete")
        except Exception:
            pass  # autoresearch is optional

        return True

    except Exception as e:
        logger.error(f"add_text_to_index failed: {e}", exc_info=True)
        return False

    finally:
        if 'nodes' in locals():
            del nodes
        if 'valid_nodes' in locals():
            del valid_nodes
        if 'document' in locals():
            del document
        import gc
        gc.collect()


def get_documents_from_file(file_path: str, client: Optional[str] = None, upload_date: Optional[str] = None) -> List[LlamaDocument]:
    documents: List[LlamaDocument] = []
    try:
        file_extension = os.path.splitext(file_path)[1].lower()
        filename = os.path.basename(file_path)
        path_obj = Path(file_path)
        logger.info(f"Processing file: {filename} Extension: {file_extension}")

        if not path_obj.exists():
            logger.error(f"File not found at path: {file_path}")
            return []
        if not path_obj.is_file():
            logger.error(f"Path is not a file: {file_path}")
            return []

        try:
            from backend.utils.file_processor_adapter import (
                process_file_to_llamaindex,
                is_enhanced_processing_available
            )
            
            if is_enhanced_processing_available(file_path):
                logger.info(f"Using EnhancedFileProcessor for: {filename}")
                enhanced_docs = process_file_to_llamaindex(
                    file_path=file_path,
                    client=client,
                    upload_date=upload_date
                )
                
                if enhanced_docs:
                    logger.info(f"EnhancedFileProcessor successfully processed {filename}: {len(enhanced_docs)} document(s)")
                    return enhanced_docs
                else:
                    logger.info(f"EnhancedFileProcessor returned no documents for {filename}, falling back to legacy processing")
            else:
                logger.debug(f"EnhancedFileProcessor does not support {filename}, using legacy processing")
                
        except ImportError as ie:
            logger.debug(f"EnhancedFileProcessor not available: {ie}, using legacy processing")
        except Exception as e:
            logger.warning(f"EnhancedFileProcessor failed for {filename}: {e}, falling back to legacy processing")

        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'}
        if file_extension in image_extensions:
            try:
                from backend.services.image_content_service import extract_text_from_image
                logger.info(f"Processing image file: {filename}")
                
                extraction_result = extract_text_from_image(file_path)
                
                if extraction_result.get('success'):
                    text_content = extraction_result.get('text_content', '')
                    
                    metadata = {
                        "source_filename": filename,
                        "file_path": str(path_obj),
                        "file_type": "image",
                        "file_extension": file_extension,
                        "extraction_method": "vision_model_ocr",
                        "vision_model_used": extraction_result.get('model_used'),
                        "extraction_confidence": extraction_result.get('confidence', 0.0),
                        "client": client,
                        "upload_date": upload_date
                    }
                    
                    if not text_content:
                        text_content = f"Image file: {filename} (no text content detected through OCR)"
                        metadata["content_type"] = "image_no_text"
                    else:
                        metadata["content_type"] = "image_with_text"
                        metadata["extracted_text_length"] = len(text_content)
                    
                    document = LlamaDocument(text=text_content, metadata=metadata)
                    documents.append(document)
                    
                    logger.info(f"Successfully processed image {filename}: extracted {len(text_content)} characters")
                else:
                    error_msg = extraction_result.get('error', 'Unknown error')
                    text_content = f"Image file: {filename} (OCR extraction failed: {error_msg})"
                    
                    metadata = {
                        "source_filename": filename,
                        "file_path": str(path_obj),
                        "file_type": "image",
                        "file_extension": file_extension,
                        "extraction_method": "vision_model_ocr",
                        "extraction_error": error_msg,
                        "content_type": "image_extraction_failed",
                        "client": client,
                        "upload_date": upload_date
                    }
                    
                    document = LlamaDocument(text=text_content, metadata=metadata)
                    documents.append(document)
                    
                    logger.warning(f"Image extraction failed for {filename}: {error_msg}")
                    
            except ImportError:
                logger.warning(f"Image content service not available for {filename}, falling back to SimpleDirectoryReader")
            except Exception as e:
                logger.error(f"BUG FIX 8: Error processing image {filename}: {e}", exc_info=True)
                text_content = f"Image file: {filename} (processing error: {str(e)})"
                metadata = {
                    "source_filename": filename,
                    "file_path": str(path_obj),
                    "file_type": "image",
                    "file_extension": file_extension,
                    "processing_error": str(e),
                    "content_type": "image_processing_error",
                    "client": client,
                    "upload_date": upload_date,
                    "error_type": "image_processing_failure"
                }
                try:
                    document = LlamaDocument(text=text_content, metadata=metadata)
                    documents.append(document)
                except Exception as doc_error:
                    logger.error(f"BUG FIX 8: Failed to create error document for {filename}: {doc_error}")

        elif file_extension in {'.xlsx', '.xls', '.xlsm', '.xlsb'}:
            try:
                from backend.services.excel_content_service import extract_excel_content
                logger.info(f"Processing Excel file: {filename}")
                
                extraction_result = extract_excel_content(file_path)
                
                if extraction_result.get('success'):
                    text_content = extraction_result.get('text_content', '')
                    excel_metadata = extraction_result.get('metadata')
                    structured_data = extraction_result.get('structured_data', {})
                    
                    metadata = {
                        "source_filename": filename,
                        "file_path": str(path_obj),
                        "file_type": "excel",
                        "file_extension": file_extension,
                        "extraction_method": "pandas_excel_processing",
                        "client": client,
                        "upload_date": upload_date
                    }
                    
                    if excel_metadata:
                        metadata.update({
                            "total_sheets": getattr(excel_metadata, 'total_sheets', 0),
                            "total_rows": getattr(excel_metadata, 'total_rows', 0),
                            "total_columns": getattr(excel_metadata, 'total_columns', 0),
                            "has_formulas": getattr(excel_metadata, 'has_formulas', False),
                            "file_format": getattr(excel_metadata, 'file_format', file_extension.replace('.', '')),
                            "worksheets": [ws.name for ws in getattr(excel_metadata, 'worksheets', [])],
                            "content_type": "excel_with_data"
                        })
                    
                    processing_info = extraction_result.get('processing_info', {})
                    metadata.update({
                        "pandas_used": processing_info.get('pandas_used', False),
                        "openpyxl_used": processing_info.get('openpyxl_used', False),
                        "advanced_features": processing_info.get('advanced_features', False)
                    })
                    
                    if not text_content:
                        text_content = f"Excel file: {filename} (no readable content found)"
                        metadata["content_type"] = "excel_no_content"
                    else:
                        metadata["extracted_text_length"] = len(text_content)
                        metadata["structured_data_available"] = bool(structured_data)
                    
                    document = LlamaDocument(text=text_content, metadata=metadata)
                    documents.append(document)
                    
                    logger.info(f"Successfully processed Excel file {filename}: {len(text_content)} characters from {metadata.get('total_sheets', 0)} sheets")
                    
                else:
                    error_msg = extraction_result.get('error', 'Unknown error')
                    text_content = f"Excel file: {filename} (Excel extraction failed: {error_msg})"
                    
                    metadata = {
                        "source_filename": filename,
                        "file_path": str(path_obj),
                        "file_type": "excel",
                        "file_extension": file_extension,
                        "extraction_method": "pandas_excel_processing",
                        "extraction_error": error_msg,
                        "content_type": "excel_extraction_failed",
                        "client": client,
                        "upload_date": upload_date
                    }
                    
                    document = LlamaDocument(text=text_content, metadata=metadata)
                    documents.append(document)
                    
                    logger.warning(f"Excel extraction failed for {filename}: {error_msg}")
                    
            except ImportError:
                logger.warning(f"Excel content service not available for {filename}, falling back to SimpleDirectoryReader")
            except Exception as e:
                logger.error(f"BUG FIX 9: Error processing Excel file {filename}: {e}", exc_info=True)
                text_content = f"Excel file: {filename} (processing error: {str(e)})"
                metadata = {
                    "source_filename": filename,
                    "file_path": str(path_obj),
                    "file_type": "excel",
                    "file_extension": file_extension,
                    "processing_error": str(e),
                    "content_type": "excel_processing_error",
                    "client": client,
                    "upload_date": upload_date,
                    "error_type": "excel_processing_failure"
                }
                try:
                    document = LlamaDocument(text=text_content, metadata=metadata)
                    documents.append(document)
                except Exception as doc_error:
                    logger.error(f"BUG FIX 9: Failed to create error document for {filename}: {doc_error}")

        elif file_extension in {'.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.htm', '.css', '.php', '.java', '.c', '.cpp', '.h', '.hpp', '.go', '.rs', '.rb', '.sql', '.json', '.xml', '.yml', '.yaml'}:
            try:
                import hashlib
                logger.info(f"Processing code file: {filename}")

                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    code_content = f.read()

                language_map = {
                    '.py': 'python',
                    '.js': 'javascript',
                    '.jsx': 'jsx',
                    '.ts': 'typescript',
                    '.tsx': 'tsx',
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

                programming_language = language_map.get(file_extension, 'text')

                metadata = {
                    "source_filename": filename,
                    "file_path": str(path_obj),
                    "file_type": "code",
                    "file_extension": file_extension,
                    "programming_language": programming_language,
                    "file_size_chars": len(code_content),
                    "file_size_bytes": path_obj.stat().st_size,
                    "content_type": "code",
                    "extraction_method": "direct_file_read",
                    "processing_mode": "code_preserving",
                    "client": client,
                    "upload_date": upload_date
                }

                document = LlamaDocument(
                    text=code_content,
                    metadata=metadata,
                    doc_id=f"{filename}_{hashlib.md5(str(path_obj).encode()).hexdigest()[:8]}"
                )
                documents.append(document)

                logger.info(f"Successfully processed code file: {filename} ({len(code_content):,} chars, {programming_language})")

            except Exception as e:
                logger.error(f"BUG FIX 10: Failed to process code file {filename}: {e}", exc_info=True)
                error_content = f"Code file: {filename} (processing error: {str(e)})"
                metadata = {
                    "source_filename": filename,
                    "file_path": str(path_obj),
                    "file_type": "code",
                    "file_extension": file_extension,
                    "processing_error": str(e),
                    "content_type": "code_processing_error",
                    "client": client,
                    "upload_date": upload_date,
                    "error_type": "code_processing_failure"
                }
                try:
                    document = LlamaDocument(text=error_content, metadata=metadata)
                    documents.append(document)
                except Exception as doc_error:
                    logger.error(f"BUG FIX 10: Failed to create error document for {filename}: {doc_error}")

        elif file_extension == ".csv" and parse_csv_rows:
            documents = parse_csv_rows(str(path_obj), client=client, upload_date=upload_date)
        elif file_extension == ".xml" and parse_sitemap:
            documents = parse_sitemap(str(path_obj))
        elif file_extension == ".pdf" and PDFReaderClass:
            try:
                logger.debug(f"Using PydfReader for: {filename}")
                pdf_reader = PDFReaderClass()
                loaded_docs = pdf_reader.load_data(file=path_obj)
                for doc in loaded_docs:
                    doc.metadata = doc.metadata or {}
                    doc.metadata["source_filename"] = filename
                    doc.metadata["file_path"] = str(path_obj)
                documents.extend(loaded_docs)
                logger.info(f"Parsed {len(documents)} docs from PDF: {filename}")
            except Exception as e:
                logger.error(
                    f"Failed to parse PDF {filename} with PydfReader: {e}",
                    exc_info=True,
                )
                return []

        if not documents and SimpleDirectoryReader:
            logger.info(
                f"Using SimpleDirectoryReader as fallback/default for: {filename}"
            )
            try:

                def file_metadata_func(fn: str) -> dict:
                    return {
                        "source_filename": os.path.basename(fn),
                        "file_path": fn,
                        "parsed_by": "SimpleDirectoryReader",
                    }

                code_extensions = {'.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.htm', '.css', '.php', '.java', '.c', '.cpp', '.h', '.hpp', '.go', '.rs', '.rb', '.sql', '.json', '.xml', '.yml', '.yaml'}
                if (file_extension not in [".csv", ".xml"] and
                    (file_extension != ".pdf" or not PDFReaderClass or not documents) and
                    file_extension not in image_extensions and
                    file_extension not in {'.xlsx', '.xls', '.xlsm', '.xlsb'} and
                    file_extension not in code_extensions):
                    reader = SimpleDirectoryReader(
                        input_files=[path_obj],
                        file_metadata=file_metadata_func,
                        errors="ignore",
                    )
                    documents.extend(reader.load_data())
                    logger.info(
                        f"Loaded {len(documents)} docs via SimpleDirectoryReader: {filename}"
                    )
                elif not documents:
                    logger.warning(
                        f"Specific parser for {file_extension} yielded no documents for {filename}, SimpleDirectoryReader not re-attempted under these conditions."
                    )
            except Exception as e:
                logger.error(
                    f"Failed default read using SimpleDirectoryReader for {filename}: {e}",
                    exc_info=True,
                )
                return []

    except Exception as e:
        logger.error(f"File processing setup failed for {filename}: {e}", exc_info=True)
        return []

    if not documents:
        logger.warning(f"No documents generated from {file_path}; could represent a file type unsupported by all available parsers.")

    for doc in documents:
        if doc.metadata is None:
            doc.metadata = {}
        doc.metadata["client"] = client
        doc.metadata["upload_date"] = upload_date
        doc.metadata["file_path"] = str(path_obj)

    return documents


def add_file_to_index(file_path: str, db_document: DBDocument, progress_callback=None) -> bool:
    try:
        from flask import current_app
        flask_available = True
    except ImportError:
        flask_available = False
        current_app = None

    import gc
    import os
    import time

    global index, storage_context

    _lazy_load_llamaindex()
    _lazy_load_optional_components()

    get_or_create_index(db_document.project_id if db_document else None)

    if index is None or storage_context is None:
        logger.error(
            "Cannot add file: Index or Storage Context not properly initialized."
        )
        logger.error("Index service not ready for document indexing")
        return False

    if db_document is None:
        logger.error(f"Cannot add file: Missing DB document info for {file_path}.")
        return False

    file_size_bytes = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    file_size_mb = file_size_bytes / (1024 * 1024)

    logger.info(f"Starting indexing process for: {file_path} (DB ID: {db_document.id}, Size: {file_size_mb:.2f}MB)")

    progress_system = get_unified_progress()
    process_id = progress_system.create_process(
        ProcessType.INDEXING,
        description=f"Indexing {db_document.filename}",
        additional_data={
            "filename": db_document.filename,
            "file_size_mb": file_size_mb,
            "document_id": db_document.id,
            "project_id": db_document.project_id if db_document.project_id else None
        }
    )

    if progress_callback:
        progress_callback(10, f"Starting indexing: {db_document.filename}")
    
    try:
        logger.info(f"Validating file {db_document.filename}")
        progress_system.update_process(process_id, 20, f"Validating file: {db_document.filename}")
        if progress_callback:
            progress_callback(20, f"Validating file: {db_document.filename}")

        if not os.path.exists(file_path):
            logger.error(f"File path does not exist: {file_path}")
            progress_system.error_process(process_id, f"File not found: {db_document.filename}")
            return False

        logger.info(f"Loading document {db_document.filename}")
        progress_system.update_process(process_id, 30, f"Loading document: {db_document.filename}")
        if progress_callback:
            progress_callback(30, f"Loading document: {db_document.filename}")
        
        try:
            documents = get_documents_from_file(
                file_path=file_path,
                client=db_document.project.client.name if db_document.project and db_document.project.client else None,
                upload_date=db_document.uploaded_at.isoformat() if db_document.uploaded_at else None
            )
            
            if not documents:
                logger.error(f"No documents loaded from {file_path}")
                logger.error("No content could be extracted from file")
                return False
            
            logger.info(f"Loaded {len(documents)} document(s) from {file_path}")
            
        except Exception as e:
            logger.error(f"Error loading document {file_path}: {e}", exc_info=True)
            logger.error(f"Failed to load document: {str(e)}")
            return False
        
        logger.info(f"Processing text for {db_document.filename}")
        progress_system.update_process(process_id, 50, f"Processing text: {db_document.filename}")
        if progress_callback:
            progress_callback(50, f"Processing text: {db_document.filename}")
        
        for doc in documents:
            if not doc.metadata:
                doc.metadata = {}
            
            doc.metadata.update({
                "source_filename": db_document.filename,
                "file_path": file_path,
                "document_id": str(db_document.id),
                "upload_date": db_document.uploaded_at.isoformat() if db_document.uploaded_at else None,
            })
            
            if db_document.project_id:
                doc.metadata["project_id"] = str(db_document.project_id)
                doc.metadata["project_id_str"] = str(db_document.project_id)
                if hasattr(db_document, 'project') and db_document.project:
                    doc.metadata["project_name"] = db_document.project.name
            
            if db_document.tags:
                doc.metadata["tags"] = db_document.tags

            if db_document.notes:
                doc.metadata["notes"] = db_document.notes

            doc.id_ = f"doc_{db_document.id}_{hash(doc.text)}"
        
        logger.info(f"Adding documents to index for {db_document.filename}")
        progress_system.update_process(process_id, 70, f"Adding to vector index: {db_document.filename}")
        if progress_callback:
            progress_callback(70, f"Adding to vector index: {db_document.filename}")
        
        try:
            # Determine if this is a code file and route to appropriate chunker
            from backend.utils.code_chunker import CodeAwareChunker
            from backend.utils.contextual_prepender import prepend_context_to_nodes

            code_chunker = CodeAwareChunker()
            language = code_chunker.get_language(db_document.filename)

            if language and documents:
                # AST-aware chunking for code files
                logger.info(f"Using AST code chunker for {db_document.filename} (language: {language})")
                nodes = []
                for doc in documents:
                    doc_nodes = code_chunker.chunk_code(doc.text, language, file_path)
                    # Carry over document-level metadata to each node
                    for node in doc_nodes:
                        node.metadata.update(doc.metadata or {})
                        node.metadata["language"] = language
                        node.metadata["content_type"] = "code"
                        node.metadata["is_code_file"] = True
                    nodes.extend(doc_nodes)

                # Extract symbols for the entire file
                from backend.utils.code_symbol_extractor import extract_symbols
                file_text = documents[0].text if documents else ""
                file_symbols = extract_symbols(file_text, language)

                # Attach per-file symbol summary to each node
                symbol_names = [s["name"] for s in file_symbols if s["type"] in ("function", "class", "method")]
                import_names = [s["name"] for s in file_symbols if s["type"] == "import"]

                for node in nodes:
                    node.metadata["file_symbols"] = ",".join(symbol_names[:50])
                    node.metadata["file_imports"] = ",".join(import_names[:50])

                    # Try to identify which symbol this specific chunk belongs to
                    chunk_text = node.metadata.get("original_text", node.text)
                    for sym in file_symbols:
                        if sym["type"] in ("function", "class", "method"):
                            if (f"def {sym['name']}" in chunk_text or
                                f"function {sym['name']}" in chunk_text or
                                f"class {sym['name']}" in chunk_text or
                                f"func {sym['name']}" in chunk_text or
                                f"fn {sym['name']}" in chunk_text):
                                node.metadata["symbol_name"] = sym["name"]
                                node.metadata["symbol_type"] = sym["type"]
                                break

                # Determine repo name from folder hierarchy
                repo_name = None
                try:
                    if db_document.folder and db_document.folder.is_repository:
                        repo_name = db_document.folder.name
                    elif db_document.folder and db_document.folder.parent and getattr(db_document.folder.parent, 'is_repository', False):
                        repo_name = db_document.folder.parent.name
                except Exception:
                    pass  # Folder relationship may not be loaded

                prepend_context_to_nodes(nodes, repo_name=repo_name)
                logger.info(f"AST code chunking produced {len(nodes)} nodes from {db_document.filename}")
            else:
                # Standard chunking for non-code files
                from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker
                rag_chunker = EnhancedRAGChunker()
                nodes = rag_chunker.chunk_documents(documents, strategy_name='auto')
                logger.info(f"Enhanced RAG chunking produced {len(nodes)} nodes from {len(documents)} documents")

                try:
                    stats = rag_chunker.get_chunking_stats()
                    logger.info(f"Chunking stats: {stats}")
                except Exception:
                    pass

            logger.info(f"Generated {len(nodes)} nodes from {len(documents)} documents")
            
            with _index_operation_lock:
                index.insert_nodes(nodes)

                logger.info(f"Persisting index for {db_document.filename}")
                progress_system.update_process(process_id, 90, f"Persisting index: {db_document.filename}")
                if progress_callback:
                    progress_callback(90, f"Persisting index: {db_document.filename}")

                with _persistence_lock:
                    persist_dir = getattr(storage_context, "persist_dir", None)
                    if not persist_dir:
                        from backend.config import INDEX_ROOT
                        persist_dir = INDEX_ROOT
                    if persist_dir and ("storage" in persist_dir and (persist_dir.endswith("/storage") or persist_dir.endswith("\\storage"))):
                        from backend.config import INDEX_ROOT
                        persist_dir = INDEX_ROOT
                        logger.warning(f"Prevented use of legacy storage folder, using {persist_dir} instead")
                    storage_context.persist(persist_dir=persist_dir)
            
            logger.info(f"Successfully indexed {file_path} with {len(nodes)} nodes")
            
        except Exception as e:
            logger.error(f"Error adding document to index: {e}", exc_info=True)
            logger.error(f"Failed to add to index: {str(e)}")
            return False
        
        logger.info(f"Indexing complete for {db_document.filename}")

        logger.info(f"Indexing completed successfully with {len(nodes)} nodes")

        progress_system.complete_process(
            process_id,
            f"Indexed {db_document.filename}: {len(nodes)} nodes created",
            additional_data={
                "nodes_created": len(nodes),
                "filename": db_document.filename
            }
        )

        if progress_callback:
            progress_callback(100, f"Indexing complete: {len(nodes)} nodes created")

        # Store file-level symbol data in Document.file_metadata
        if language and 'file_symbols' in dir() and file_symbols:
            try:
                import json as _json
                existing_metadata = {}
                if db_document.file_metadata:
                    try:
                        existing_metadata = _json.loads(db_document.file_metadata)
                    except (ValueError, TypeError):
                        pass
                existing_metadata["symbols"] = file_symbols
                existing_metadata["language"] = language
                existing_metadata["imports"] = import_names if 'import_names' in dir() else []
                existing_metadata["ast_chunked"] = True
                existing_metadata["indexing_method"] = "code_intelligence_v1"
                db_document.file_metadata = _json.dumps(existing_metadata)
                db.session.commit()
                logger.info(f"Stored {len(file_symbols)} symbols in metadata for {db_document.filename}")
            except Exception as e:
                logger.warning(f"Failed to store symbol metadata: {e}")

        gc.collect()

        logger.info(f"Successfully indexed {db_document.filename}")

        # Notify autoresearch that corpus has changed
        try:
            from backend.celery_app import celery_app as _celery
            _celery.send_task("autoresearch.on_index_complete")
        except Exception:
            pass  # autoresearch is optional

        return True

    except Exception as e:
        logger.error(f"Unexpected error during indexing: {e}", exc_info=True)
        logger.error(f"Unexpected error during indexing: {str(e)}")

        progress_system.error_process(
            process_id,
            f"Indexing failed for {db_document.filename}: {str(e)[:100]}"
        )

        return False
    
    finally:
        try:
            logger.debug(f"Cleaning up memory for {file_path} (DB ID: {db_document.id})")
            
            if 'documents' in locals():
                documents.clear()
                del documents
                logger.debug("Cleaned up documents")
            
            if 'nodes' in locals():
                nodes.clear()
                logger.debug("Cleaned up nodes")
            
            if 'node_parser' in locals():
                node_parser = None
            
            if file_size_mb > 1.0:
                collected = gc.collect()
                logger.debug(f"Garbage collected {collected} objects for large file ({file_size_mb:.2f}MB)")
            else:
                gc.collect()
                
            logger.debug("Memory cleanup completed")
        except Exception as cleanup_error:
            logger.warning(f"Memory cleanup failed: {cleanup_error}")


def _get_entity_metadata(db_document: DBDocument) -> Dict[str, Any]:
    metadata = {}
    
    fallback_metadata = {
        "content_type": "document",
        "entity_hierarchy": f"Document: {db_document.filename}",
        "entity_hierarchy_searchable": db_document.filename.lower(),
        "error_recovery": True
    }
    
    try:
        if not db or not db.session:
            logger.error(f"Database session unavailable for document {db_document.id}")
            return fallback_metadata
        
        if not db_document or not hasattr(db_document, 'id'):
            logger.error("Invalid document object provided to _get_entity_metadata")
            return fallback_metadata
            
        try:
            db.session.execute(db.text("SELECT 1"))
        except Exception as conn_error:
            logger.error(f"Database connection test failed for document {db_document.id}: {conn_error}")
            return fallback_metadata
        
        try:
            if db_document.project_id and hasattr(db_document, 'project'):
                if db_document.project is None:
                    logger.debug(f"Project relationship not loaded for document {db_document.id}, attempting to load")
                    try:
                        from backend.models import Project
                        project = db.session.query(Project).filter(
                            Project.id == db_document.project_id
                        ).first()
                        if project:
                            db_document.project = project
                    except Exception as project_load_error:
                        logger.warning(f"Failed to load project {db_document.project_id} for document {db_document.id}: {project_load_error}")
                        project = None
                else:
                    project = db_document.project
                
                if project:
                    try:
                        project_metadata = {}
                        
                        if hasattr(project, 'name') and project.name:
                            project_metadata["project_name"] = str(project.name)
                        if hasattr(project, 'description') and project.description:
                            project_metadata["project_description"] = str(project.description)
                        if hasattr(project, 'created_at') and project.created_at:
                            project_metadata["project_created_at"] = project.created_at.isoformat()
                        if hasattr(project, 'updated_at') and project.updated_at:
                            project_metadata["project_updated_at"] = project.updated_at.isoformat()
                        
                        metadata.update(project_metadata)
                        
                        try:
                            if hasattr(project, 'client_ref') and project.client_ref is not None:
                                client = project.client_ref
                                client_metadata = {}
                                
                                if hasattr(client, 'id') and client.id:
                                    client_metadata["client_id"] = str(client.id)
                                if hasattr(client, 'name') and client.name:
                                    client_metadata["client_name"] = str(client.name)
                                if hasattr(client, 'email') and client.email:
                                    client_metadata["client_email"] = str(client.email)
                                if hasattr(client, 'phone') and client.phone:
                                    client_metadata["client_phone"] = str(client.phone)
                                if hasattr(client, 'notes') and client.notes:
                                    client_metadata["client_notes"] = str(client.notes)
                                if hasattr(client, 'created_at') and client.created_at:
                                    client_metadata["client_created_at"] = client.created_at.isoformat()
                                if hasattr(client, 'updated_at') and client.updated_at:
                                    client_metadata["client_updated_at"] = client.updated_at.isoformat()
                                
                                metadata.update(client_metadata)
                                
                                try:
                                    client_searchable = [
                                        client.name or "",
                                        client.email or "",
                                        client.phone or "",
                                        client.notes or ""
                                    ]
                                    client_searchable_filtered = [item for item in client_searchable if item.strip()]
                                    if client_searchable_filtered:
                                        metadata["client_searchable_content"] = " ".join(client_searchable_filtered).lower()
                                except Exception as searchable_error:
                                    logger.warning(f"Error creating searchable client content for document {db_document.id}: {searchable_error}")
                                    
                            elif hasattr(project, 'client_id') and project.client_id:
                                try:
                                    from backend.models import Client
                                    client = db.session.query(Client).filter(
                                        Client.id == project.client_id
                                    ).first()
                                    if client:
                                        metadata["client_id"] = str(client.id)
                                        metadata["client_name"] = str(client.name or "")
                                        if client.email:
                                            metadata["client_email"] = str(client.email)
                                except Exception as client_load_error:
                                    logger.warning(f"Failed to load client {project.client_id} for document {db_document.id}: {client_load_error}")
                                    
                        except Exception as client_error:
                            logger.warning(f"Error accessing client information for document {db_document.id}: {client_error}")
                            
                    except Exception as project_attr_error:
                        logger.warning(f"Error accessing project attributes for document {db_document.id}: {project_attr_error}")
                        
        except Exception as project_error:
            logger.warning(f"Error processing project information for document {db_document.id}: {project_error}")
        
        try:
            if db_document.website_id and hasattr(db_document, 'website'):
                if db_document.website is None:
                    logger.debug(f"Website relationship not loaded for document {db_document.id}, attempting to load")
                    try:
                        from backend.models import Website
                        website = db.session.query(Website).filter(
                            Website.id == db_document.website_id
                        ).first()
                        if website:
                            db_document.website = website
                    except Exception as website_load_error:
                        logger.warning(f"Failed to load website {db_document.website_id} for document {db_document.id}: {website_load_error}")
                        website = None
                else:
                    website = db_document.website
                
                if website:
                    try:
                        website_metadata = {}
                        
                        if hasattr(website, 'id') and website.id:
                            website_metadata["website_id"] = str(website.id)
                        if hasattr(website, 'url') and website.url:
                            website_metadata["website_url"] = str(website.url)
                        if hasattr(website, 'sitemap') and website.sitemap:
                            website_metadata["website_sitemap"] = str(website.sitemap)
                        if hasattr(website, 'status') and website.status:
                            website_metadata["website_status"] = str(website.status)
                        if hasattr(website, 'last_crawled') and website.last_crawled:
                            website_metadata["website_last_crawled"] = website.last_crawled.isoformat()
                        if hasattr(website, 'created_at') and website.created_at:
                            website_metadata["website_created_at"] = website.created_at.isoformat()
                        if hasattr(website, 'updated_at') and website.updated_at:
                            website_metadata["website_updated_at"] = website.updated_at.isoformat()
                        
                        metadata.update(website_metadata)
                        
                        try:
                            if not metadata.get("client_id") and hasattr(website, 'client_ref') and website.client_ref:
                                client = website.client_ref
                                if hasattr(client, 'id') and client.id:
                                    metadata["client_id"] = str(client.id)
                                if hasattr(client, 'name') and client.name:
                                    metadata["client_name"] = str(client.name)
                                if hasattr(client, 'email') and client.email:
                                    metadata["client_email"] = str(client.email)
                                if hasattr(client, 'phone') and client.phone:
                                    metadata["client_phone"] = str(client.phone)
                                if hasattr(client, 'notes') and client.notes:
                                    metadata["client_notes"] = str(client.notes)
                                
                                try:
                                    client_searchable = [
                                        client.name or "",
                                        client.email or "",
                                        client.phone or "",
                                        client.notes or ""
                                    ]
                                    client_searchable_filtered = [item for item in client_searchable if item.strip()]
                                    if client_searchable_filtered:
                                        metadata["client_searchable_content"] = " ".join(client_searchable_filtered).lower()
                                except Exception as searchable_error:
                                    logger.warning(f"Error creating searchable client content from website for document {db_document.id}: {searchable_error}")
                                    
                        except Exception as website_client_error:
                            logger.warning(f"Error accessing client from website for document {db_document.id}: {website_client_error}")
                            
                    except Exception as website_attr_error:
                        logger.warning(f"Error accessing website attributes for document {db_document.id}: {website_attr_error}")
                        
        except Exception as website_error:
            logger.warning(f"Error processing website information for document {db_document.id}: {website_error}")
        
        try:
            if hasattr(db_document, 'type') and db_document.type:
                metadata["document_type"] = str(db_document.type)
        except Exception as type_error:
            logger.warning(f"Error accessing document type for document {db_document.id}: {type_error}")
        
        try:
            if hasattr(db_document, 'index_status'):
                metadata["document_index_status"] = str(db_document.index_status or "UNKNOWN")
        except Exception as status_error:
            logger.warning(f"Error accessing document index status for document {db_document.id}: {status_error}")
            metadata["document_index_status"] = "UNKNOWN"
        
        try:
            entity_hierarchy = []
            if metadata.get("client_name"):
                entity_hierarchy.append(f"Client: {metadata['client_name']}")
            if metadata.get("project_name"):
                entity_hierarchy.append(f"Project: {metadata['project_name']}")
            if metadata.get("website_url"):
                entity_hierarchy.append(f"Website: {metadata['website_url']}")
            
            doc_name = getattr(db_document, 'filename', 'Unknown Document')
            entity_hierarchy.append(f"Document: {doc_name}")
            
            metadata["entity_hierarchy"] = " > ".join(entity_hierarchy)
            metadata["entity_hierarchy_searchable"] = " ".join(entity_hierarchy).lower()
            
        except Exception as hierarchy_error:
            logger.warning(f"Error building entity hierarchy for document {db_document.id}: {hierarchy_error}")
            metadata["entity_hierarchy"] = f"Document: {db_document.filename}"
            metadata["entity_hierarchy_searchable"] = db_document.filename.lower()
        
        metadata["content_type"] = "document"
        
        logger.debug(f"Successfully extracted metadata for document {db_document.id}: {len(metadata)} fields")
        
        if not metadata or len(metadata) < 3:
            logger.warning(f"Metadata extraction resulted in insufficient data for document {db_document.id}, using fallback")
            return fallback_metadata
        
    except Exception as e:
        logger.error(f"Critical error extracting entity metadata for document {db_document.id}: {e}", exc_info=True)
        
        if metadata and len(metadata) > 0:
            final_metadata = {**fallback_metadata, **metadata}
            final_metadata["extraction_partial"] = True
            return final_metadata
        else:
            return fallback_metadata
    
    return metadata


def _is_valid_status_transition(current_status: str, new_status: str) -> bool:
    valid_transitions = {
        'INDEXING': ['COMPLETED', 'ERROR', 'FAILED'],
        'COMPLETED': ['INDEXING', 'ERROR'],
        'ERROR': ['INDEXING', 'COMPLETED'],
        'FAILED': ['INDEXING'],
        'PENDING': ['INDEXING', 'ERROR']
    }
    
    if current_status not in valid_transitions:
        return True
        
    return new_status in valid_transitions.get(current_status, [])

def update_document_status(
    doc_id: int, status: str, error_message: Optional[str] = None
):
    if not DBDocument or not db:
        logger.error(
            "DB/Model unavailable for status update. Ensure models.py was imported correctly."
        )
        return

    logger.info(f"Updating status in DB for Doc ID {doc_id} -> '{status}'...")
    if error_message:
        logger.debug(
            f"  Associated Error Message for Doc ID {doc_id}: {error_message[:500]}"
        )

    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Flask-SQLAlchemy auto-begins a transaction per request, so calling
            # session.begin() again would raise "A transaction is already begun".
            # Use a nested SAVEPOINT when we're already inside a transaction,
            # otherwise start a fresh one. Porting this was traced to the nested-
            # async / "Event loop is closed" errors we hit earlier — a SQLAlchemy
            # exception here can corrupt the httpx/anyio event loop downstream.
            if db.session.in_transaction():
                ctx = db.session.begin_nested()
            else:
                ctx = db.session.begin()
            with ctx:
                doc = db.session.query(DBDocument).filter(
                    DBDocument.id == doc_id
                ).order_by(DBDocument.id).with_for_update(nowait=True).first()
                
                if doc:
                    logger.debug(
                        f"  Found Doc {doc_id}. Current status: '{doc.index_status}'. Updating... (attempt {retry_count + 1})"
                    )
                    
                    if not _is_valid_status_transition(doc.index_status, status):
                        logger.warning(
                            f"Invalid status transition for Doc {doc_id}: '{doc.index_status}' -> '{status}'"
                        )
                        return
                    
                    doc.index_status = status
                    doc.error_message = error_message
                    
                    current_time = datetime.datetime.now()
                    if status == "INDEXED":
                        doc.indexed_at = current_time
                        doc.error_message = None
                    elif status == "ERROR":
                        doc.indexed_at = None
                    elif status == "INDEXING":
                        if hasattr(doc, 'updated_at'):
                            doc.updated_at = current_time
                    
                    db.session.flush()
                    
                    logger.info(f"  Successfully updated status for Doc ID {doc_id} to '{status}'.")
                    return
                    
                else:
                    logger.warning(
                        f"  Doc ID {doc_id} not found in database for status update."
                    )
                    return  # No point retrying if document doesn't exist
                    
        except Exception as e:
            retry_count += 1
            
            error_str = str(e).lower()
            is_retryable = any(keyword in error_str for keyword in [
                'deadlock', 'lock timeout', 'serialization failure', 
                'concurrent update', 'integrity constraint'
            ])
            
            if is_retryable and retry_count < max_retries:
                import time
                wait_time = 0.05 * (2 ** (retry_count - 1))
                logger.warning(
                    f"Database conflict updating Doc ID {doc_id} (attempt {retry_count}/{max_retries}). "
                    f"Retrying in {wait_time:.3f}s... Error: {e}"
                )
                time.sleep(wait_time)
                
                try:
                    db.session.rollback()
                except Exception as rollback_error:
                    logger.error(f"Rollback failed during retry: {rollback_error}")
                    
                continue
            else:
                logger.error(
                    f"Failed database status update for Doc ID {doc_id} after {retry_count} attempts: {e}", 
                    exc_info=True
                )
                
                try:
                    logger.warning(
                        "  Rolling back database session due to status update error..."
                    )
                    db.session.rollback()
                    logger.info("  Rollback successful.")
                except Exception as rb_e:
                    logger.error(f"  Database rollback failed during error handling: {rb_e}")
                
                break


def get_index_for_project(project_id: Optional[str], base_dir: str):
    global index, storage_context
    
    try:
        logger.info(f"Getting index for project_id: {project_id}")
        
        from flask import has_app_context
        if not has_app_context():
            if index is not None and storage_context is not None:
                logger.info("Using global index (no app context)")
                return index, storage_context
            else:
                from backend.config import INDEX_ROOT, PROJECT_INDEX_MODE
                index_root = INDEX_ROOT
                persist_dir = index_root
                _initialize_index(persist_dir)
                return index, storage_context
        
        result = get_or_create_index(project_id)
        index = result[0] if isinstance(result, tuple) else result

        if index is None:
            logger.error(f"Failed to get/create index for project {project_id}")
            return None, None
            
        return index, storage_context
        
    except Exception as e:
        logger.error(f"Error getting index for project {project_id}: {e}", exc_info=True)
        return None, None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logger.info("Running indexing_service.py standalone for testing.")
    pass
