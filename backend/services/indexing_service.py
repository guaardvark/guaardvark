
import datetime
import gc
import json
import hashlib
import logging
import re
import os
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

from backend.utils.experiment_context import get_experiment_config, get_active_rag_params
import backend.utils.llama_index_local_config

# Per edge-portability audit: remove unconditional CUDA_VISIBLE_DEVICES at
# import time (causes "device 0 does not exist" on CPU/ARM boxes). Only set
# for GPU hosts; workers stay CPU.
if os.environ.get('CELERY_WORKER_MODE', 'false').lower() == 'true':
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    logger.info("CUDA disabled for Celery worker - using CPU")
else:
    try:
        import subprocess
        if subprocess.run(['nvidia-smi'], capture_output=True, timeout=3).returncode == 0:
            os.environ['CUDA_VISIBLE_DEVICES'] = '0'
            logger.info("CUDA enabled for indexing service - using GPU acceleration")
        else:
            os.environ['CUDA_VISIBLE_DEVICES'] = ''
            logger.info("No NVIDIA GPU detected - indexing using CPU")
    except Exception:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        logger.info("GPU probe failed - indexing using CPU for safety")
    
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

SimpleVectorStore = None  # Fallback store (JSON, in-memory) used when GUAARDVARK_VECTOR_STORE=simple. The default backend is pgvector — see _make_vector_store and docs/ARCHITECTURE.md.
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

# --- ingest phase timing --------------------------------------------------
# Half of a measured 93-minute ingest could not be attributed to any phase: the
# only timestamps available -- progress events and `indexed_at` -- do not bracket
# parsing, and they miss the gap between documents entirely. Optimising against a
# model that explains half the clock is guesswork, so the pipeline times itself.
#
# Timings are kept in module state rather than logged, because application logging
# defaults to WARNING (BACKEND_LOG_LEVEL) and a routine measurement is not a
# warning. A benchmark harness runs in-process and reads them directly.
_LAST_PHASE_TIMINGS: Dict[str, Any] = {}



# A full gc.collect() costs roughly 250 ms in this process: the heap holds
# LlamaIndex, torch and a resident model, so the collector walks a very large
# object graph. Two unconditional calls per document made that the single largest
# fixed cost of ingesting a small file -- measured at 0.49 s of a 0.50 s document,
# dwarfing parsing, chunking and the embedding itself.
#
# Python collects cycles on its own; these calls exist for the large transient
# objects a big file leaves behind. So run them when that is actually the case,
# and otherwise amortise across a batch.
_GC_EVERY_N_DOCS = int(os.environ.get("GUAARDVARK_INDEX_GC_EVERY", "25"))
_GC_LARGE_FILE_MB = float(os.environ.get("GUAARDVARK_INDEX_GC_LARGE_MB", "1.0"))
_docs_since_gc = 0


def _maybe_collect(file_size_mb: float = 0.0) -> None:
    """Collect after a large file, or once every _GC_EVERY_N_DOCS documents."""
    global _docs_since_gc
    _docs_since_gc += 1
    if file_size_mb > _GC_LARGE_FILE_MB or _docs_since_gc >= _GC_EVERY_N_DOCS:
        _docs_since_gc = 0
        gc.collect()


@contextmanager
def _phase(name: str, into: Dict[str, float]):
    """Accumulate wall-clock milliseconds for one ingest phase."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        into[name] = into.get(name, 0.0) + (time.perf_counter() - t0) * 1000.0


def get_last_phase_timings() -> Dict[str, Any]:
    """Phase timings for the most recently indexed document."""
    return dict(_LAST_PHASE_TIMINGS)


class _EmbedClock:
    """Separates embedding time from vector-store time inside `insert_nodes`.

    LlamaIndex emits embedding start/end events around the model call, so
    subscribing is enough to split the two -- no monkeypatching, and it keeps
    working if the insert path changes underneath.
    """

    def __init__(self):
        self.ms = 0.0
        self.calls = 0
        self._starts = {}

    def handle(self, event):
        name = type(event).__name__
        if name == "EmbeddingStartEvent":
            self._starts[getattr(event, "span_id", None)] = time.perf_counter()
        elif name == "EmbeddingEndEvent":
            t0 = self._starts.pop(getattr(event, "span_id", None), None)
            if t0 is not None:
                self.ms += (time.perf_counter() - t0) * 1000.0
                self.calls += 1


# BM25 retriever cache. BM25Retriever.from_defaults() re-tokenizes the ENTIRE docstore, so
# rebuilding it on every query is expensive. Cache keyed on (id(docstore), doc_count):
# id(docstore) changes on reindex/reload, doc_count changes on in-place insert_nodes() — so
# the cache self-invalidates on both adds and reindex without instrumenting every mutation
# site. Guarded by the same _index_operation_lock as the index globals.
_bm25_cache: dict = {}  # id(docstore) -> {"doc_count": int, "top_k": int, "retriever": BM25Retriever}




def _needs_docstore_nodes(vstore) -> bool:
    """Whether the docstore must hold node copies for retrieval to work.

    `store_nodes_override=True` mirrors every node into a SimpleDocumentStore, and
    that docstore is what `storage_context.persist()` rewrites -- in full, as JSON,
    on every ingested document. On a file-backed store it is not optional: BM25
    reads from it, and SimpleVectorStore does not keep text.

    With pgvector it is pure cost. The rows already carry their text and a tsvector,
    the keyword leg reads them straight from SQL, and the mirror only exists to be
    serialised again. Returning False here is what stops the per-document rewrite
    from growing with the corpus.
    """
    return type(vstore).__name__ != "PGVectorStore"


class PostgresSparseRetriever:
    """Keyword retrieval straight from Postgres, replacing BM25 over the docstore.

    BM25Retriever reads a `SimpleDocumentStore`, which only holds nodes because
    `store_nodes_override=True` keeps it populated -- and keeping it populated is
    what forced a full rewrite of a JSON file on every ingested document. Postgres
    already maintains a `tsvector` column and a GIN index over the same rows
    (PGVectorStore is created with hybrid_search=True), so the keyword leg can be
    served from there and the docstore can go.

    Two deliberate choices about the SQL:

    `websearch_to_tsquery` rather than llama-index's own sparse path, which
    OR-joins every term and then ranks every match. On a large corpus an OR over
    common words matches a large fraction of the table, and `ORDER BY rank LIMIT k`
    has to score all of it. websearch_to_tsquery is AND-by-default and understands
    quoted phrases, so the candidate set stays small.

    `ts_rank_cd` with normalisation 34 -- that is 2 (divide by document length)
    combined with 32 (rank / (rank + 1)). The length term is the important half and
    was found the hard way: without it, a short chunk holding the answer ranks below
    eight longer near-identical siblings that mention the same words more often, and
    a planted fact inside a code block dropped out of the results entirely. Length
    normalisation is one of the things BM25 does for free; bare cover-density
    ranking does not. This is still not BM25 -- there is no IDF saturation and no
    k1/b -- but fusion min-max normalises each leg independently, so only the
    ordering matters, not the scale.

    Unlike BM25Retriever this can filter, so project scoping happens in SQL rather
    than by over-fetching and discarding afterwards.
    """

    def __init__(self, table: str, top_k: int = 10, filters: Optional[Dict[str, Any]] = None):
        self.table = table
        self.top_k = top_k
        self.filters = filters or {}

    # Terms too common to be worth OR-ing over a large corpus; an OR containing one
    # of these matches most of the table and makes the ranking do all the work.
    _STOPISH = frozenset("""a an and are as at be by for from has have how in is it
        its of on or that the this to was what when where which who why with""".split())

    def _tsquery_or(self, query: str) -> Optional[str]:
        """An OR query over the useful terms, or None if nothing is left."""
        terms = [t for t in re.findall(r"[A-Za-z0-9_]{2,}", query.lower())
                 if t not in self._STOPISH]
        # Deduplicate, keep order.
        seen, out = set(), []
        for t in terms:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return " | ".join(out[:12]) or None

    def _run(self, match_sql: str, match_arg: str):
        where = [match_sql]
        # Placeholder order must match the statement below: the rank expression in
        # the SELECT, then the match in the WHERE, then any filters, then the limit.
        params: List[Any] = [match_arg, match_arg]
        for key, value in self.filters.items():
            where.append("metadata_->>%s = %s")
            params.extend([key, str(value)])
        params.append(self.top_k)
        rank_fn = match_sql.split(" @@ ")[1].replace("%s", "%s")
        sql = (
            f"SELECT node_id, text, metadata_, "
            f"ts_rank_cd(text_search_tsv, {rank_fn}, 34) AS rank "
            f'FROM "data_{self.table}" WHERE ' + " AND ".join(where) +
            " ORDER BY rank DESC LIMIT %s"
        )
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        finally:
            conn.close()

    def _rows(self, query: str):
        """Precise first; broaden only when nothing matched at all.

        websearch_to_tsquery is AND-by-default, which is the right default at scale:
        an OR over common words matches a large fraction of the table and forces the
        ranker to score all of it.

        Broadening to an OR looks like an obvious improvement -- it turns one hit into
        eight -- and measurably makes retrieval worse. The vector leg already supplies
        recall; what fusion needs from the keyword leg is precision. Eight loosely
        matched chunks crowd out the one the vector leg ranked correctly, and on the
        validation corpus that cost a planted fact that had been returned at rank 1
        (a value inside a code block, whose near-identical sibling chunks all score
        similarly on the broadened query).

        So the OR is a fallback for "no keyword match at all", not a way to fill the
        result list.
        """
        rows = self._run("text_search_tsv @@ websearch_to_tsquery('english', %s)", query)
        if rows:
            return rows
        or_query = self._tsquery_or(query)
        if not or_query:
            return rows
        try:
            broadened = self._run("text_search_tsv @@ to_tsquery('english', %s)", or_query)
        except Exception as e:
            logger.debug("Sparse OR broadening failed (%s); keeping strict results", e)
            return rows
        # Prefer the broader set only if it genuinely found more.
        return broadened if len(broadened) > len(rows) else rows

    def retrieve(self, query) -> List[Any]:
        from llama_index.core.schema import NodeWithScore, TextNode
        text = query if isinstance(query, str) else getattr(query, "query_str", str(query))
        if not (text or "").strip():
            return []
        try:
            rows = self._rows(text)
        except Exception as e:
            logger.warning("Sparse (postgres) retrieval failed: %s", e)
            return []
        out = []
        for node_id, node_text, meta, rank in rows:
            meta = meta if isinstance(meta, dict) else (json.loads(meta) if meta else {})
            meta.pop("_node_content", None)
            meta.pop("_node_type", None)
            out.append(NodeWithScore(
                node=TextNode(id_=node_id, text=node_text or "", metadata=meta),
                score=float(rank or 0.0),
            ))
        return out

    # QueryFusionRetriever calls both of these.
    def _retrieve(self, query) -> List[Any]:
        return self.retrieve(query)

    async def _aretrieve(self, query) -> List[Any]:
        return self.retrieve(query)

    async def aretrieve(self, query) -> List[Any]:
        return self.retrieve(query)



def _get_sparse_retriever(storage_ctx, top_k: int, project_id=None, profile: Optional[str] = None):
    """The keyword leg: Postgres full-text when available, BM25 as the fallback.

    Returns None when neither can serve, which the caller treats as "vector only"
    and records in the trace -- a silently missing leg would look like a relevance
    regression rather than a missing retriever.
    """
    table = None
    try:
        table = resolve_existing_vector_table(project_id, profile)
    except Exception:
        table = None
    if table:
        filters = {}
        if project_id is not None:
            filters["project_id_str"] = str(project_id)
        return PostgresSparseRetriever(table, top_k=top_k, filters=filters)

    # File-backed store: fall back to the docstore-driven BM25.
    try:
        return _get_cached_bm25_retriever(storage_ctx.docstore, top_k)
    except Exception:
        return None


def _get_cached_bm25_retriever(docstore, similarity_top_k: int):
    """Return a cached BM25Retriever for this docstore, rebuilding only when the docstore
    object identity or its document count changes. Returns None if BM25 is unavailable."""
    try:
        from llama_index.retrievers.bm25 import BM25Retriever
    except Exception as e:
        # Not just ImportError: bm25s imports jax at module scope and guards only
        # (ImportError, RuntimeError), so a jax/numpy ABI mismatch surfaces here as
        # AttributeError. Catching narrowly is how hybrid search died unnoticed.
        logger.warning(
            "BM25 unavailable (%s: %s) — hybrid search will degrade to vector-only",
            e.__class__.__name__, str(e)[:160],
        )
        return None
    try:
        doc_count = len(getattr(docstore, "docs", {}) or {})
    except Exception:
        doc_count = -1
    ds_id = id(docstore)
    with _index_operation_lock:
        cached = _bm25_cache.get(ds_id)
        if cached and cached["doc_count"] == doc_count and cached["top_k"] == similarity_top_k:
            return cached["retriever"]
        retriever = BM25Retriever.from_defaults(docstore=docstore, similarity_top_k=similarity_top_k)
        _bm25_cache[ds_id] = {"doc_count": doc_count, "top_k": similarity_top_k, "retriever": retriever}
        return retriever


def _adaptive_alpha(query: str, base_alpha: float) -> float:
    """Query-aware hybrid weight (vector weight). Keyword/identifier/quoted/short queries
    lean toward BM25 (lower vector weight); long prose leans toward vector. `base_alpha`
    (the env GUAARDVARK_HYBRID_SEARCH_ALPHA) is the anchor/override. Pure CPU heuristic —
    calibrate the bands with the RAG eval harness."""
    import re
    q = (query or "").strip()
    n = len(q.split())
    keywordish = (
        n <= 3
        or '"' in q or "'" in q
        or bool(re.search(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", q))   # dotted.path
        or bool(re.search(r"[a-z0-9]_[a-z0-9]", q))              # snake_case
        or bool(re.search(r"[a-z][A-Z]", q))                      # camelCase
    )
    if keywordish:
        return max(0.1, min(base_alpha, 0.25))
    if n >= 12:  # long prose → lean vector
        return min(0.7, max(base_alpha, 0.55))
    return base_alpha


def _mmr_rerank(results: list, top_k: int = 8, lambda_: float = 0.7) -> list:
    """CPU-only MMR reranker over the already-retrieved top candidates. Balances relevance
    against diversity (token-Jaccard overlap) to demote near-redundant chunks. Zero VRAM,
    no model — safe on CPU/Pi. On any failure returns `results` as-is.

    Relevance is the cross-encoder score when one is present, else the retrieval score:
    ranking on the weaker signal would silently undo the reranker that just ran."""
    try:
        if not results or len(results) <= 2:
            return results
        import re as _re
        working = results[:top_k]
        tail = results[top_k:]

        def _rel_score(r):
            if not isinstance(r, dict):
                return 0.0
            v = r.get("rerank_score")
            if v is None:
                v = r.get("score", 0.0)
            return float(v or 0.0)

        scores = [_rel_score(r) for r in working]
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0
        rel = [(s - lo) / span for s in scores]  # normalize relevance to [0,1]

        def _toks(r):
            t = r.get("text", "") if isinstance(r, dict) else ""
            return set(_re.findall(r"[a-z0-9]+", t.lower()))
        tok_sets = [_toks(r) for r in working]

        def _jacc(a, b):
            if not a or not b:
                return 0.0
            union = len(a | b)
            return (len(a & b) / union) if union else 0.0

        remaining = list(range(len(working)))
        first = max(remaining, key=lambda i: rel[i])
        selected = [first]
        remaining.remove(first)
        while remaining:
            best_i, best_mmr = None, None
            for i in remaining:
                max_sim = max((_jacc(tok_sets[i], tok_sets[j]) for j in selected), default=0.0)
                mmr = lambda_ * rel[i] - (1.0 - lambda_) * max_sim
                if best_mmr is None or mmr > best_mmr:
                    best_mmr, best_i = mmr, i
            selected.append(best_i)
            remaining.remove(best_i)

        return [working[i] for i in selected] + tail
    except Exception as e:
        logger.debug(f"MMR rerank skipped: {e}")
        return results


def _under_resource_pressure() -> bool:
    """True when running the vector (embedding) leg of retrieval is risky right now: GPU
    present but VRAM headroom low, OR no GPU and system RAM is low. Callers fall back to
    BM25-only (CPU, no embedding) instead of thrashing. Best-effort → False if undeterminable."""
    try:
        from backend.services.gpu_resource_coordinator import has_gpu, get_available_vram
        if has_gpu():
            info = get_available_vram()
            if info.get("success"):
                return info.get("available_mb", 0) < int(os.environ.get("GUAARDVARK_RAG_MIN_VRAM_MB", "1500"))
            return False
        import psutil
        return psutil.virtual_memory().percent >= float(os.environ.get("GUAARDVARK_RAG_MAX_RAM_PCT", "92"))
    except Exception:
        return False


# Query-embedding cache: (model, query) -> (vector, ts). Repeated/identical retrieval queries
# skip the embed call. Bounded LRU + TTL; keyed by active model so a model change can't serve
# stale vectors. Helps CPU-only hosts most (where embedding is slowest).
import time as _time
from collections import OrderedDict as _OrderedDict
_query_embed_cache = _OrderedDict()
_QUERY_EMBED_CACHE_MAX = int(os.environ.get("GUAARDVARK_QUERY_EMBED_CACHE_SIZE", "256"))
_QUERY_EMBED_CACHE_TTL = float(os.environ.get("GUAARDVARK_QUERY_EMBED_CACHE_TTL", "300"))


def _get_cached_query_embedding(query: str):
    """Return the query-side embedding for `query`, cached with TTL. Uses Settings.embed_model
    .get_query_embedding — same model and query-side semantics as the retriever, so the vector
    is in the index's space (important for asymmetric models). None on failure → the retriever
    embeds it itself."""
    try:
        from llama_index.core import Settings
        embed_model = getattr(Settings, "embed_model", None)
        if embed_model is None:
            return None
        from backend.config import get_active_embedding_model
        key = (get_active_embedding_model(), query)
        now = _time.time()
        with _index_operation_lock:
            hit = _query_embed_cache.get(key)
            if hit is not None:
                vec, ts = hit
                if now - ts <= _QUERY_EMBED_CACHE_TTL:
                    _query_embed_cache.move_to_end(key)
                    return vec
                del _query_embed_cache[key]
        vec = embed_model.get_query_embedding(query)
        if not vec:
            return None
        with _index_operation_lock:
            _query_embed_cache[key] = (vec, now)
            _query_embed_cache.move_to_end(key)
            while len(_query_embed_cache) > _QUERY_EMBED_CACHE_MAX:
                _query_embed_cache.popitem(last=False)
        return vec
    except Exception as e:
        logger.debug(f"Query-embed cache skipped: {e}")
        return None


def _persist_dir_for(project_id=None) -> str:
    """Resolve the on-disk index directory for a project (mirrors get_or_create_index)."""
    from backend.config import INDEX_ROOT, PROJECT_INDEX_MODE
    index_mode = os.getenv("GUAARDVARK_PROJECT_INDEX_MODE", PROJECT_INDEX_MODE)
    index_root = os.getenv("GUAARDVARK_INDEX_ROOT", INDEX_ROOT)
    if index_mode == "per_project" and project_id:
        return os.path.join(index_root, str(project_id))
    return index_root


_embed_dim_cache: Dict[str, int] = {}


def _active_embed_dim() -> Optional[int]:
    """Embedding width of the active model, probed once per model and cached.

    pgvector bakes the dimension into the column type, so this has to be known
    before the table exists. Probing beats a hardcoded map: the operator can point
    the system at any Ollama embedding model.
    """
    override = os.environ.get("GUAARDVARK_EMBEDDING_DIM", "").strip()
    if override:
        try:
            return int(override)
        except ValueError:
            logger.warning("GUAARDVARK_EMBEDDING_DIM=%r is not an integer; ignoring", override)
    try:
        from backend.config import get_active_embedding_model
        model = get_active_embedding_model()
    except Exception:
        model = "unknown"
    if model in _embed_dim_cache:
        return _embed_dim_cache[model]
    try:
        from llama_index.core import Settings
        embed_model = getattr(Settings, "embed_model", None)
        if embed_model is None:
            return None
        dim = len(embed_model.get_query_embedding("dimension probe"))
        _embed_dim_cache[model] = dim
        logger.info("Embedding dimension for %s: %d", model, dim)
        return dim
    except Exception as e:
        logger.warning("Could not probe embedding dimension: %s", e)
        return None


def _vector_backend() -> str:
    return os.environ.get("GUAARDVARK_VECTOR_STORE", "pgvector").lower()


def _pg_table_name(project_id=None, profile: Optional[str] = None) -> Optional[str]:
    """Per (profile, scope, dimension) table.

    Putting the dimension in the name means switching embedding models lands in a
    different table instead of contaminating an existing one -- the failure the
    name-based dimension lock and _sanitize_vector_store_dimensions exist to paper
    over on the JSON store. The profile makes each projection its own table, so
    two profiles over the same corpus cannot read each other's vectors.

    The default profile resolves to the bare scope, so an installation that never
    touches profiles keeps the table name it already has.
    """
    dim = _active_embed_dim()
    if not dim:
        return None
    try:
        from backend.services.index_profiles import projection_key
        scope = projection_key(profile, project_id)
    except Exception:
        scope = str(project_id) if project_id else "global"
    scope = re.sub(r"[^A-Za-z0-9_]", "_", scope)[:60]
    return f"guaardvark_{scope}_{dim}"


# Set when the configured vector store could not be built and an EMPTY in-memory
# store was substituted. Retrieval still answers in that state, which is the whole
# danger: every result looks normal while the real index is not being consulted.
# Read by search_with_llamaindex so the trace says so.
_vector_store_fallback_reason: Optional[str] = None
# Latch so the warning below is emitted once per process, not per query.
_fallback_warned = False


def vector_store_fallback_reason() -> Optional[str]:
    """Why the configured vector store is not in use, or None when it is."""
    return _vector_store_fallback_reason


def _fall_back_to_simple(reason: str):
    global _vector_store_fallback_reason
    _vector_store_fallback_reason = reason
    logger.warning(
        "pgvector requested but %s — using an EMPTY SimpleVectorStore; "
        "the persisted index will NOT be consulted", reason,
    )
    return SimpleVectorStore() if SimpleVectorStore else None


def _make_vector_store(project_id=None, profile: Optional[str] = None):
    """Build the configured vector store. Returns None to mean 'use the default'."""
    global _vector_store_fallback_reason
    backend = _vector_backend()
    if backend != "pgvector":
        _vector_store_fallback_reason = None
        return SimpleVectorStore() if SimpleVectorStore else None

    table = _pg_table_name(project_id, profile)
    dim = _active_embed_dim()
    if not table or not dim:
        return _fall_back_to_simple(
            "the embedding dimension is unknown (embedding backend unreachable? "
            "set GUAARDVARK_EMBEDDING_DIM to pin it)"
        )

    try:
        from llama_index.vector_stores.postgres import PGVectorStore
        from backend.config import DATABASE_URL
        m = re.match(r"postgresql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)", DATABASE_URL)
        if not m:
            raise ValueError("DATABASE_URL is not a parseable postgresql:// URL")
        user, password, host, port, database = m.groups()
        store = PGVectorStore.from_params(
            host=host, port=port, database=database, user=user, password=password,
            table_name=table,
            embed_dim=dim,
            hybrid_search=True,
            text_search_config="english",
            # pgvector's hnsw index rejects vector columns above 2000 dimensions.
            # halfvec (16-bit) raises the ceiling to 4000 and indexes fine at 2560.
            # The precision loss is immaterial for cosine ranking; being unindexed
            # is not -- it would mean a sequential scan of every row per query.
            use_halfvec=(dim > 2000),
            hnsw_kwargs={
                "hnsw_m": 16,
                "hnsw_ef_construction": 64,
                "hnsw_ef_search": 40,
                # halfvec columns need the halfvec opclass; the vector one is rejected.
                "hnsw_dist_method": "halfvec_cosine_ops" if dim > 2000 else "vector_cosine_ops",
            },
        )
        logger.info("Vector store: pgvector table data_%s (dim=%d)", table, dim)
        _ensure_document_id_index(table)
        _vector_store_fallback_reason = None
        return store
    except Exception as e:
        return _fall_back_to_simple(
            f"it is unavailable ({e.__class__.__name__}: {str(e)[:180]})"
        )



def _ensure_document_id_index(table: str) -> None:
    """Index the document_id prefix that re-indexing deletes by.

    Re-indexing a document first removes its existing vectors, matching
    `metadata_->>'document_id' LIKE 'doc\\_<id>\\_%'`. PGVectorStore builds an
    HNSW index on the embedding and a GIN index on the text, but nothing on that
    expression, so the delete was a sequential scan of the whole table -- and
    because `metadata_` is large enough to be TOASTed, every row had to be
    detoasted to evaluate it. Measured at 55,844 rows: 591 ms per document, and
    growing linearly, so the cost of ingesting one document rose with the size of
    the corpus already ingested.

    `text_pattern_ops` is required: on a non-C collation the default operator
    class cannot serve a prefix LIKE. Created here so a fresh install gets it
    without a migration step; IF NOT EXISTS makes it idempotent.
    """
    ddl = (
        f'CREATE INDEX IF NOT EXISTS "{table}_docid_prefix" '
        f'ON "data_{table}" ((metadata_->>\'document_id\') text_pattern_ops)'
    )
    try:
        conn = _pg_connect()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(ddl)
        finally:
            conn.close()
    except Exception as e:
        # Not fatal: without it re-indexing is slower, not wrong.
        logger.warning("Could not ensure document_id index on data_%s: %s", table, e)


def _pg_connect():
    from backend.config import DATABASE_URL
    m = re.match(r"postgresql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)", DATABASE_URL)
    if not m:
        raise ValueError("DATABASE_URL is not a parseable postgresql:// URL")
    import psycopg2
    user, password, host, port, database = m.groups()
    return psycopg2.connect(host=host, port=port, dbname=database, user=user, password=password)


def purge_document_vectors(document_id, project_id=None, profile: Optional[str] = None) -> int:
    """Remove a document's existing vectors before re-indexing it. Returns rows removed.

    The JSON store kept embeddings in a dict keyed by node id, so re-indexing a file
    overwrote its nodes in place. pgvector does not: `add()` INSERTs, so every
    re-index appended a second full copy of the document. Measured before this
    existed: 74,451 rows for 30,945 distinct nodes -- 58% of the index was
    duplicates, one chunk stored 135 times -- which inflates storage, slows every
    query, and lets one passage occupy several of the caller's result slots.

    Deletes by the `document_id` metadata stamped on every node at ingest, which
    covers all of a file's parsed documents at once (a PDF contributes one per page).
    """
    if _vector_backend() != "pgvector" or document_id is None:
        return 0
    table = _pg_table_name(project_id, profile)
    if not table:
        return 0
    # The stored key is the LlamaIndex document id, `doc_<db_id>_<content_hash>` --
    # NOT the bare database id. One file yields several of them (a PDF contributes
    # one per page), and the hash changes whenever the file's content changes, which
    # is exactly when a re-index happens. So match the `doc_<db_id>_` prefix rather
    # than an exact id, or a re-index of edited content leaves the old copy behind.
    #
    # The underscores must be escaped: `_` is a LIKE wildcard, so an unescaped
    # `doc_2216_%` would also match `doc_22160_...` and delete another document's
    # vectors.
    pattern = "doc\\_{}\\_%".format(str(document_id).replace("\\", "\\\\"))
    try:
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f'DELETE FROM "data_{table}" '
                    f'WHERE metadata_->>\'document_id\' LIKE %s ESCAPE \'\\\'',
                    (pattern,),
                )
                removed = cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()
        if removed:
            logger.info("Re-index: removed %d existing vector(s) for document %s",
                        removed, document_id)
        return removed
    except Exception as e:
        logger.warning("Could not purge existing vectors for document %s: %s", document_id, e)
        return 0


def resolve_existing_vector_table(project_id=None, profile: Optional[str] = None) -> Optional[str]:
    """Name of the vector table for a scope, WITHOUT needing an embedding model.

    `_pg_table_name` derives the name from the active model's dimension, which
    means probing the model. That is fine inside the app, but the MCP server runs
    as a bare subprocess with no Flask context and no initialised index, so the
    probe returns None and every read-only knowledge tool fails on an interface
    built specifically for MCP.

    The dimension is already encoded in the table name, so an existing table can
    simply be looked up. Falls back to the derived name when nothing is found, so
    a first-run caller still gets the table it is about to create.
    """
    try:
        derived = _pg_table_name(project_id, profile)
    except Exception:
        derived = None
    if derived:
        return derived
    if _vector_backend() != "pgvector":
        return None

    try:
        from backend.services.index_profiles import projection_key
        scope = projection_key(profile, project_id)
    except Exception:
        scope = str(project_id) if project_id else "global"
    scope = re.sub(r"[^A-Za-z0-9_]", "_", scope)[:60]

    try:
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                # Escaped: `_` is a LIKE wildcard and the scope contains them.
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name LIKE %s ESCAPE '\\' "
                    "ORDER BY table_name",
                    ("data\\_guaardvark\\_" + scope.replace("_", "\\_") + "\\_%",),
                )
                rows = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.debug("vector table discovery failed: %s", e)
        return None

    if not rows:
        return None
    # Strip the "data_" prefix the store adds, to match _pg_table_name's contract.
    return rows[0][len("data_"):] if rows[0].startswith("data_") else rows[0]


def drop_vector_store(project_id=None, profile: Optional[str] = None) -> Dict[str, Any]:
    """Drop the backing vector table for a scope.

    Resetting the index used to mean deleting JSON files. With the vectors in
    Postgres that would leave every embedding orphaned while the index looked
    empty, so the reset path has to reach the database too.
    """
    if _vector_backend() != "pgvector":
        return {"backend": "simple", "dropped": False}
    table = _pg_table_name(project_id, profile)
    if not table:
        return {"backend": "pgvector", "dropped": False, "error": "dimension unknown"}
    full = f"data_{table}"
    try:
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f'DROP TABLE IF EXISTS "{full}"')
            conn.commit()
        finally:
            conn.close()
        logger.info("Dropped vector table %s", full)
        return {"backend": "pgvector", "dropped": True, "table": full}
    except Exception as e:
        logger.error("Failed dropping vector table %s: %s", full, e)
        return {"backend": "pgvector", "dropped": False, "table": full, "error": str(e)[:200]}


def vector_store_stats(project_id=None, profile: Optional[str] = None) -> Dict[str, Any]:
    """Row count and on-disk size of the backing vector store."""
    if _vector_backend() != "pgvector":
        return {"backend": "simple"}
    table = _pg_table_name(project_id, profile)
    if not table:
        return {"backend": "pgvector", "error": "dimension unknown"}
    full = f"data_{table}"
    try:
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (f"public.{full}",))
                if cur.fetchone()[0] is None:
                    return {"backend": "pgvector", "table": full, "exists": False,
                            "rows": 0, "size_bytes": 0}
                cur.execute(f'SELECT count(*) FROM "{full}"')
                rows = cur.fetchone()[0]
                cur.execute("SELECT pg_total_relation_size(%s)", (f"public.{full}",))
                size = cur.fetchone()[0]
            return {"backend": "pgvector", "table": full, "exists": True,
                    "rows": rows, "size_bytes": int(size or 0),
                    "embed_dim": _active_embed_dim()}
        finally:
            conn.close()
    except Exception as e:
        return {"backend": "pgvector", "table": full, "error": str(e)[:200]}


def _index_embedding_meta_path(persist_dir: str) -> str:
    return os.path.join(persist_dir, "embedding_meta.json")


def _record_index_embedding_model(project_id=None):
    """Stamp the active embedding model onto the index dir (best-effort). Called whenever
    content is indexed, so the sidecar reflects the model the index was actually built with."""
    try:
        from backend.config import get_active_embedding_model
        persist_dir = _persist_dir_for(project_id)
        os.makedirs(persist_dir, exist_ok=True)
        with open(_index_embedding_meta_path(persist_dir), "w") as f:
            json.dump({"embedding_model": get_active_embedding_model()}, f)
    except Exception as e:
        logger.debug(f"Could not record index embedding model: {e}")


def _check_index_embedding_model(project_id=None) -> bool:
    """True if the index's embedding model matches the active one (dimension-lock). On a
    mismatch, logs an actionable 'reindex required' message and returns False so the caller
    skips vector search instead of hitting a silent dimension-mismatch error. Backfills the
    sidecar when missing (a pre-existing index is assumed to match the current model)."""
    try:
        from backend.config import get_active_embedding_model
        active = get_active_embedding_model()
        path = _index_embedding_meta_path(_persist_dir_for(project_id))
        if not os.path.exists(path):
            _record_index_embedding_model(project_id)  # backfill for pre-existing indexes
            return True
        with open(path) as f:
            stored = (json.load(f) or {}).get("embedding_model")
        if stored and stored != active:
            logger.warning(
                "RAG index was built with embedding model '%s' but the active model is '%s'. "
                "Vector search is disabled until the index is rebuilt (Settings -> reset/reindex). "
                "Returning no results to avoid dimension-mismatch errors.",
                stored, active,
            )
            return False
        return True
    except Exception as e:
        logger.debug(f"Index embedding-model check skipped: {e}")
        return True
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

def _sanitize_vector_store_dimensions(storage_context_obj, persist_dir: Optional[str] = None) -> int:
    """Prune embeddings whose dimension != the majority dimension in the store.

    A model switch (e.g. bge-m3 1024-dim -> qwen3-embedding 2560-dim) can leave a
    few stale-dim vectors behind. SimpleVectorStore.query() does np.array(all
    embeddings) and raises "setting an array element with a sequence ... inhomogeneous
    shape" on mixed dims, which kills the ENTIRE vector leg of hybrid search (RAG then
    silently degrades to nothing). The dimension-lock checks the model NAME, not
    per-vector dims, so it misses intra-store contamination. This prunes the minority
    so search survives, and re-persists. Returns the number of vectors removed.
    Wrapped non-fatally — a sanitizer failure must never block index load.
    """
    removed = 0
    try:
        # Only meaningful for SimpleVectorStore: it keeps a plain dict of embeddings and
        # np.array() over mixed dimensions raises. A dimensioned backend (pgvector) makes
        # the failure impossible -- the column has one width, and a model change lands in
        # a differently-named table.
        if _vector_backend() != "simple":
            return 0
        from collections import Counter
        stores = []
        vs = getattr(storage_context_obj, "vector_store", None)
        if vs is not None:
            stores.append(vs)
        vstores = getattr(storage_context_obj, "vector_stores", None)
        if isinstance(vstores, dict):
            stores.extend(vstores.values())

        seen: set = set()
        for store in stores:
            data = getattr(store, "data", None) or getattr(store, "_data", None)
            emb = getattr(data, "embedding_dict", None)
            if not emb or id(emb) in seen:
                continue
            seen.add(id(emb))
            dims = Counter(len(v) for v in emb.values() if isinstance(v, (list, tuple)))
            if len(dims) <= 1:
                continue  # homogeneous — nothing to fix
            majority_dim = dims.most_common(1)[0][0]
            bad = [k for k, v in emb.items()
                   if not isinstance(v, (list, tuple)) or len(v) != majority_dim]
            if not bad:
                continue
            t2r = getattr(data, "text_id_to_ref_doc_id", None)
            meta = getattr(data, "metadata_dict", None)
            for k in bad:
                emb.pop(k, None)
                if isinstance(t2r, dict):
                    t2r.pop(k, None)
                if isinstance(meta, dict):
                    meta.pop(k, None)
            removed += len(bad)
            logger.warning(
                "[DIM-SANITIZE] Pruned %d stale-dimension vector(s) (kept dim=%d, "
                "dropped minority %s) — stale embedding-model leftovers that would crash "
                "vector search. A full reindex is recommended to restore that content.",
                len(bad), majority_dim, dict(dims),
            )
        if removed and persist_dir:
            try:
                storage_context_obj.persist(persist_dir=persist_dir)
                logger.warning("[DIM-SANITIZE] Persisted cleaned vector store to %s", persist_dir)
            except Exception as e:
                logger.error("[DIM-SANITIZE] Failed to persist cleaned store: %s", e)
    except Exception as e:
        logger.error("[DIM-SANITIZE] sanitizer error (non-fatal): %s", e)
    return removed


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

    # The vector store is addressed by scope, not by directory. In global mode the
    # persist dir IS the index root; in per_project mode it is a child named for the
    # project, so the directory name is the scope.
    from backend.config import INDEX_ROOT as _INDEX_ROOT
    _project_scope_hint = (
        None
        if os.path.abspath(abs_storage_path) == os.path.abspath(_INDEX_ROOT)
        else os.path.basename(abs_storage_path.rstrip("/\\"))
    )

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

                _vs = _make_vector_store(_project_scope_hint)
                if _vs is not None:
                    storage_defaults["vector_store"] = _vs

                storage_context_instance = StorageContext.from_defaults(**storage_defaults)

                index_instance = VectorStoreIndex.from_documents(
                    [],
                    storage_context=storage_context_instance,
                    store_nodes_override=_needs_docstore_nodes(_vs),
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
            _load_kwargs = {"persist_dir": abs_storage_path}
            _vs_load = _make_vector_store(_project_scope_hint)
            if _vs_load is not None:
                _load_kwargs["vector_store"] = _vs_load
            storage_context_instance = StorageContext.from_defaults(**_load_kwargs)
            index_instance = load_index_from_storage(
                storage_context_instance,
                store_nodes_override=_needs_docstore_nodes(_vs_load)
            )

            index = index_instance
            storage_context = storage_context_instance
            # Self-heal mixed-dimension contamination from a past embedding-model switch
            # before any query hits np.array(embeddings) and crashes the vector leg.
            _sanitize_vector_store_dimensions(storage_context_instance, abs_storage_path)
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

                    _vs = _make_vector_store(_project_scope_hint)
                    if _vs is not None:
                        storage_defaults["vector_store"] = _vs

                    storage_context_instance = StorageContext.from_defaults(**storage_defaults)
                    index_instance = VectorStoreIndex.from_documents(
                        [],
                        storage_context=storage_context_instance,
                        store_nodes_override=_needs_docstore_nodes(_vs),
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


def deduplicate_chunks(chunks: list, similarity_threshold: float = None) -> list:
    """Remove near-duplicate retrieved chunks based on embedding similarity.

    Embeds with the ACTIVE embedding model (same vector space as the index) via the
    EmbeddingRouter's single batched call — not a hardcoded second model in an O(N)
    loop. The threshold is model-aware (cosine distributions differ per model). Dedup
    is best-effort: on any failure or shape mismatch the original chunks are returned
    unchanged (we never drop retrieval results because dedup couldn't run).
    """
    if len(chunks) <= 1:
        return chunks

    try:
        from backend.config import get_active_embedding_model, get_dedup_threshold
        from backend.utils.embedding_router import get_embedding_router

        active_model = get_active_embedding_model()
        threshold = similarity_threshold if similarity_threshold is not None else get_dedup_threshold(active_model)

        overlay = get_active_rag_params()
        if "dedup_threshold" in overlay:
            threshold = overlay["dedup_threshold"]

        texts = [
            (c.get("text", "") if isinstance(c, dict) else getattr(c, "text", str(c)))[:500]
            for c in chunks
        ]

        # One batched call against the active model (correct vector space), instead of
        # N sequential calls to a hardcoded model.
        embeddings = get_embedding_router().get_embeddings_batch(texts)

        # Negative-case guard: router must return one non-empty vector per text. Anything
        # else (down router, partial batch) → skip dedup rather than build a bad matrix.
        if (not embeddings or len(embeddings) != len(texts)
                or any(not e for e in embeddings)):
            logger.warning(
                "Chunk dedup skipped: embedding shape mismatch "
                f"(got {len(embeddings) if embeddings else 0}, expected {len(texts)}, model={active_model})"
            )
            return chunks

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
            logger.info(
                f"Deduplicated {len(chunks)} chunks to {len(deduped)} "
                f"(model={active_model}, threshold={threshold})"
            )
        return deduped

    except Exception as e:
        logger.warning(f"Chunk deduplication failed: {e}")
        return chunks


def _expand_query(query: str) -> Optional[str]:
    """One cheap LLM paraphrase for the query_expansion param. Fail-soft: None."""
    try:
        from flask import current_app
        llm = current_app.config.get("LLAMA_INDEX_LLM")
        if llm is None:
            return None
        resp = llm.complete(
            "Rewrite this search query using different words but the same meaning. "
            "Return ONLY the rewritten query, nothing else.\nQuery: " + query[:500]
        )
        text = str(resp).strip().strip('"')
        if text and text.lower() != query.lower():
            return text[:500]
    except Exception as e:
        logger.debug(f"Query expansion failed: {e}")
    return None


def search_with_llamaindex(
    query: str,
    max_chunks: Optional[int] = None,
    project_id: Optional[int] = None,
    filters: Optional[Dict[str, Any]] = None,
    with_trace: bool = False,
    profile: Optional[str] = None,
):
    """Hybrid retrieval over the knowledge index.

    Returns a list of {text, score, metadata, node_id} chunks. When `with_trace`
    is set, returns {"results": [...], "trace": {...}} instead — the trace records
    which retrieval legs actually ran, so a degraded answer is distinguishable
    from a bad one. `filters` are metadata equality filters ANDed with the
    project scope.
    """
    global index

    trace: Dict[str, Any] = {
        "legs": [],
        "fusion": None,
        "degraded": False,
        "degraded_reason": None,
        "eff_alpha": None,
        "base_alpha": None,
        "top_k": None,
        "embedding_model": None,
        "embedding_dims": None,
        "mmr_applied": False,
        "rerank": None,
        "dedup_removed": 0,
        "profile": None,
        "project_scope": "global" if project_id is None else str(project_id),
        "filters": dict(filters) if filters else {},
        "returned": 0,
        "error": None,
    }

    def _out(results):
        trace["returned"] = len(results)
        return {"results": results, "trace": trace} if with_trace else results

    try:
        with _index_operation_lock:
            local_index = index
            if local_index is None:
                logger.warning("search_with_llamaindex: Index not available, attempting to load...")
                get_or_create_index(project_id=str(project_id) if project_id else None)
                local_index = index

        if local_index is None:
            logger.error("search_with_llamaindex: Failed to load index")
            trace["error"] = "index_unavailable"
            return _out([])

        if not query or not isinstance(query, str):
            logger.warning("search_with_llamaindex: Invalid query input")
            trace["error"] = "invalid_query"
            return _out([])

        # Dimension-lock: refuse vector search if the index was built with a different
        # embedding model (proactive + actionable, vs. the old silent post-hoc empty return).
        if not _check_index_embedding_model(project_id):
            trace["error"] = "embedding_model_mismatch"
            trace["degraded"] = True
            trace["degraded_reason"] = (
                "index built with a different embedding model; vector search refused"
            )
            return _out([])

        # The configured store may have been swapped for an empty in-memory one at
        # build time. Retrieval keeps working in that state and returns ordinary
        # looking results from nothing, so it has to be said out loud here.
        _vs_fallback = vector_store_fallback_reason()
        if _vs_fallback:
            trace["vector_store"] = "simple_fallback"
            trace["degraded"] = True
            trace["degraded_reason"] = (
                f"persisted vector index NOT in use — {_vs_fallback}"
            )
            # The trace alone is not enough: with_trace=True is passed by exactly
            # one caller in the tree, so chat, the generation pipeline and the
            # eval harness all take the bare list and never see `degraded`. Log it
            # once per process at WARNING so the condition is at least visible
            # somewhere an operator looks.
            global _fallback_warned
            if not _fallback_warned:
                _fallback_warned = True
                logger.warning(
                    "RAG degraded: answering from an EMPTY in-memory vector store "
                    "— %s. The persisted index is NOT being consulted.", _vs_fallback,
                )

        # Layered RAG params (autoresearch): experiment override > explicit
        # max_chunks argument > promoted active config > legacy default (5).
        # The overlay already merges promoted+experiment (experiment wins) and
        # clamps values; an empty overlay means "behave exactly as pre-layer".
        overlay = get_active_rag_params()
        exp_config = get_experiment_config()
        if exp_config and "top_k" in exp_config:
            effective_top_k = overlay["top_k"]
        elif max_chunks is not None:
            effective_top_k = max(1, min(int(max_chunks), 50))
        elif "top_k" in overlay:
            effective_top_k = overlay["top_k"]
        else:
            effective_top_k = 5

        prof_params: Dict[str, Any] = {}
        try:
            from backend.services.index_profiles import resolve_retrieval_params
            prof_params = resolve_retrieval_params(profile)
            trace["profile"] = prof_params.get("profile")
        except Exception as e:
            logger.debug("index profile unavailable (%s); using global params", e)

        # Precedence: experiment > explicit max_chunks > profile > overlay > default.
        # A profile must not override an autoresearch experiment, or the experiment
        # would be measuring the profile instead of the change under test.
        if prof_params and max_chunks is None and not (exp_config and "top_k" in exp_config) \
                and "top_k" not in overlay:
            effective_top_k = int(prof_params.get("top_k") or effective_top_k)

        trace["top_k"] = effective_top_k
        try:
            from backend.config import get_active_embedding_model
            trace["embedding_model"] = get_active_embedding_model()
        except Exception:
            pass

        # Metadata scope: project id (when scoped) ANDed with any caller-supplied
        # equality filters. A filter the store cannot apply must not be silently
        # dropped -- it is recorded in the trace so the caller can see it failed.
        _filter_pairs = []
        if project_id is not None:
            _filter_pairs.append(("project_id", str(project_id)))
        for _k, _v in (filters or {}).items():
            if _v is not None:
                _filter_pairs.append((str(_k), str(_v)))

        # Filtering happens after fusion (the sparse leg cannot filter), so retrieve a
        # wider pool when a filter is set -- otherwise an unfiltered top_k can arrive
        # entirely non-matching and the caller gets nothing for no good reason.
        try:
            from backend.utils.reranker import is_enabled as _rerank_enabled
            _widen_for_rerank = _rerank_enabled()
        except Exception:
            _widen_for_rerank = False
        # A reranker handed exactly top_k candidates cannot improve anything, and a
        # post-fusion filter needs spare candidates to survive. Both want a wider pool.
        _widened = bool(_filter_pairs or _widen_for_rerank)
        candidate_top_k = min(50, effective_top_k * 4) if _widened else effective_top_k
        trace["candidate_top_k"] = candidate_top_k

        if _filter_pairs:
            try:
                from llama_index.core.vector_stores.types import MetadataFilters, MetadataFilter, FilterOperator
                metadata_filters = MetadataFilters(
                    filters=[
                        MetadataFilter(key=_k, value=_v, operator=FilterOperator.EQ)
                        for _k, _v in _filter_pairs
                    ]
                )
                base_retriever = local_index.as_retriever(similarity_top_k=candidate_top_k, filters=metadata_filters)
                trace["filters_applied"] = True
            except Exception:
                logger.debug("search_with_llamaindex: MetadataFilters not available, falling back to unfiltered")
                base_retriever = local_index.as_retriever(similarity_top_k=candidate_top_k)
                trace["filters_applied"] = False
                trace["degraded"] = True
                trace["degraded_reason"] = "metadata filters unavailable; results are UNFILTERED"
        else:
            base_retriever = local_index.as_retriever(similarity_top_k=candidate_top_k)
            trace["filters_applied"] = None
        # Hybrid search: add BM25 retrieval alongside vector search.
        # Promoted/experiment alpha wins; env var is the legacy default.
        hybrid_alpha = overlay.get(
            "hybrid_search_alpha",
            float(os.environ.get("GUAARDVARK_HYBRID_SEARCH_ALPHA", "0.3")),
        )
        retriever = base_retriever  # Default to vector-only
        use_query_embedding = True  # False only when we fall back to BM25-only (no vector leg)
        trace["base_alpha"] = hybrid_alpha
        trace["legs"] = ["vector"]
        trace["fusion"] = "vector_only"

        if hybrid_alpha > 0.0 and storage_context is not None:
            try:
                from llama_index.core.retrievers import QueryFusionRetriever

                bm25_retriever = _get_sparse_retriever(
                    storage_context, candidate_top_k, project_id=project_id, profile=profile
                )
                if bm25_retriever is None:
                    raise ImportError("no sparse retriever available")

                # Resource-pressure fallback: under VRAM/RAM pressure, skip the vector leg
                # entirely (no query embedding) and serve BM25-only rather than thrash.
                if _under_resource_pressure():
                    logger.warning(
                        "RAG degraded: resource pressure → BM25-only retrieval (vector embedding skipped)"
                    )
                    retriever = bm25_retriever
                    use_query_embedding = False  # don't embed the query under pressure
                    trace["legs"] = ["bm25"]
                    trace["fusion"] = "bm25_only"
                    trace["degraded"] = True
                    trace["degraded_reason"] = (
                        "resource pressure (low free VRAM or high RAM): vector leg skipped, "
                        "BM25-only results"
                    )
                else:
                    # Effective vector weight (alpha). Adaptive per-query unless disabled.
                    eff_alpha = hybrid_alpha
                    if os.environ.get("GUAARDVARK_HYBRID_ADAPTIVE_ALPHA", "true").lower() == "true":
                        eff_alpha = _adaptive_alpha(query if isinstance(query, str) else "", hybrid_alpha)
                    eff_alpha = max(0.0, min(1.0, eff_alpha))

                    try:
                        # relative_score is the only fusion mode that honors retriever_weights.
                        # Order matches retrievers=[vector, bm25] → [eff_alpha, 1-eff_alpha].
                        retriever = QueryFusionRetriever(
                            retrievers=[base_retriever, bm25_retriever],
                            similarity_top_k=candidate_top_k,
                            num_queries=1,
                            mode="relative_score",
                            retriever_weights=[eff_alpha, 1.0 - eff_alpha],
                        )
                        trace["legs"] = ["vector", "bm25"]
                        trace["fusion"] = "relative_score"
                        trace["eff_alpha"] = eff_alpha
                    except (TypeError, ValueError) as weight_err:
                        # Older llama-index without relative_score/retriever_weights → plain RRF.
                        logger.debug(f"Weighted fusion unavailable ({weight_err}); using reciprocal_rerank")
                        retriever = QueryFusionRetriever(
                            retrievers=[base_retriever, bm25_retriever],
                            similarity_top_k=candidate_top_k,
                            num_queries=1,
                            mode="reciprocal_rerank",
                        )
                        trace["legs"] = ["vector", "bm25"]
                        trace["fusion"] = "reciprocal_rerank"
                        trace["eff_alpha"] = None
                    logger.info(
                        f"Hybrid search (vector={eff_alpha:.2f}, bm25={1.0 - eff_alpha:.2f}, base_alpha={hybrid_alpha})"
                    )
            except ImportError:
                logger.warning("BM25Retriever not available, using vector-only search")
                trace["degraded"] = True
                trace["degraded_reason"] = (
                    "hybrid requested (alpha=%.2f) but BM25 is unavailable; vector-only results"
                    % hybrid_alpha
                )
            except Exception as e:
                logger.warning(f"Hybrid search setup failed, using vector-only: {e}")
                trace["degraded"] = True
                trace["degraded_reason"] = (
                    "hybrid requested (alpha=%.2f) but the sparse leg failed (%s); vector-only results"
                    % (hybrid_alpha, str(e)[:120])
                )

        from llama_index.core.schema import QueryBundle

        if isinstance(query, str):
            query_bundle = QueryBundle(query_str=query)
            # Reuse a cached query embedding so the vector leg skips re-embedding. Skipped
            # under resource pressure (BM25-only path embeds nothing).
            if use_query_embedding:
                cached_vec = _get_cached_query_embedding(query)
                if cached_vec is not None:
                    query_bundle.embedding = cached_vec
                    try:
                        trace["embedding_dims"] = len(cached_vec)
                    except TypeError:
                        pass
        else:
            query_bundle = query

        nodes = retriever.retrieve(query_bundle)

        # query_expansion (autoresearch param, default off): one LLM paraphrase,
        # union the two retrievals; downstream dedup removes the overlap.
        if overlay.get("query_expansion") and isinstance(query, str):
            expanded = _expand_query(query)
            if expanded:
                try:
                    nodes.extend(retriever.retrieve(QueryBundle(query_str=expanded)))
                except Exception as e:
                    logger.debug(f"Expanded-query retrieval failed: {e}")

        # Metadata filters must hold for every returned node, not just the ones the
        # vector store filtered. BM25Retriever has no filter support, so fusion
        # re-admits nodes the vector leg excluded -- including nodes from other
        # projects. Enforce the scope here, where both legs have already landed.
        if _filter_pairs:
            def _passes(nws):
                n = nws.node if hasattr(nws, "node") else nws
                md = getattr(n, "metadata", None) or {}
                return all(str(md.get(k)) == v for k, v in _filter_pairs)

            _pre_filter = len(nodes)
            nodes = [n for n in nodes if _passes(n)]
            trace["filtered_out"] = _pre_filter - len(nodes)
            trace["filters_applied"] = True

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
            
        logger.info(
            f"search_with_llamaindex retrieved {len(results)} results "
            f"(query_len={len(query)}, project_id={project_id})"
        )

        # Deduplicate near-identical chunks
        _pre_dedup = len(results)
        results = deduplicate_chunks(results)
        trace["dedup_removed"] = _pre_dedup - len(results)

        # Cross-encoder rerank: re-score query+passage together, which the bi-encoder
        # and BM25 legs cannot do. Runs before MMR on purpose -- this decides what is
        # relevant, MMR then decides what is diverse. Never raises; if it did not run,
        # the trace says why.
        try:
            from backend.utils.reranker import rerank as _ce_rerank
            if prof_params.get("rerank") is False:
                _ce_info = {"applied": False, "reason": f"disabled by profile '{prof_params.get('profile')}'"}
            else:
                results, _ce_info = _ce_rerank(query if isinstance(query, str) else "", results)
            trace["rerank"] = _ce_info
            if not _ce_info.get("applied") and _ce_info.get("reason") and len(results) >= 2:
                reason = _ce_info["reason"]
                # "disabled" and "too few candidates" are configuration, not failure.
                if not reason.startswith("disabled") and not reason.startswith("fewer than"):
                    trace["degraded"] = True
                    trace["degraded_reason"] = f"cross-encoder rerank unavailable ({reason})"
        except Exception as e:
            trace["rerank"] = {"applied": False, "reason": f"import failed: {e}"}

        # CPU-only MMR rerank of the top candidates (relevance × diversity). Zero VRAM.
        # Env var is the operator's master allow; the tunable param decides per-query
        # (defaults on, matching pre-layer behavior).
        if (os.environ.get("GUAARDVARK_RERANK_ENABLED", "true").lower() == "true"
                and overlay.get("reranking_enabled", True)):
            results = _mmr_rerank(results)
            trace["mmr_applied"] = True

        # Expand results with cross-file dependency context
        try:
            from backend.utils.context_expander import expand_with_dependencies
            results = expand_with_dependencies(results)
        except Exception as e:
            logger.debug(f"Context expansion skipped: {e}")

        # context_window_chunks: how many chunks the CALLER receives, distinct
        # from top_k (candidate pool fed to dedup/rerank). Only applies when the
        # caller didn't pass an explicit max_chunks cap of its own. The default
        # of 3 preserves chat's pre-layer behavior (it used to pass max_chunks=3).
        if max_chunks is None:
            cwc = overlay.get("context_window_chunks",
                              prof_params.get("context_window_chunks", 3))
            results = results[:cwc]
        elif _widened:
            # The pool was widened for filtering/reranking; honour the requested count.
            results = results[:effective_top_k]

        # Fallback: if project-scoped search returned 0 results, retry with global scope
        if not results and project_id is not None:
            logger.info(f"search_with_llamaindex: No project-scoped results, falling back to global search")
            _fb = search_with_llamaindex(
                query, max_chunks=max_chunks, project_id=None,
                filters=filters, with_trace=True,
            )
            _fb_trace = _fb["trace"]
            _fb_trace["project_scope"] = "global_fallback"
            _fb_trace["fallback_from_project"] = str(project_id)
            return {"results": _fb["results"], "trace": _fb_trace} if with_trace else _fb["results"]

        return _out(results)

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
        trace["error"] = err_msg[:200]
        trace["degraded"] = True
        trace["degraded_reason"] = "retrieval raised; empty result set"
        return _out([])




def purge_nodes_by_metadata(filters: Dict[str, Any], profile: Optional[str] = None) -> int:
    """Remove nodes whose metadata matches every key/value in `filters`.

    `purge_document_vectors` keys off a document id, which only exists for content
    that came from a file. Text indexed directly -- a repository summary, a client
    profile, an extracted relationship -- has no document row, so re-running its
    producer used to leave the previous version in the index next to the new one.
    That is worse than stale storage: the old summary is still retrievable, still
    scores well, and now competes with the current one at query time.

    Callers pass the metadata keys that identify their content (see
    `add_text_to_index(replace_where=...)`), and this removes the previous copy.
    """
    if not filters:
        return 0
    table = resolve_existing_vector_table(None, profile)
    if not table:
        return 0
    clauses, params = [], []
    for key, value in filters.items():
        clauses.append("metadata_->>%s = %s")
        params.extend([str(key), str(value)])
    sql = f'DELETE FROM "data_{table}" WHERE ' + " AND ".join(clauses)
    try:
        conn = _pg_connect()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.rowcount or 0
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Could not purge nodes by metadata %s: %s", filters, e)
        return 0


def add_file_to_index_by_id(file_path: str, document_id) -> bool:
    """Run the full ingest pipeline for a file, given only a document id.

    The Celery ingest path is written to avoid Flask, so it cannot hold the ORM
    object `add_file_to_index` needs. Rather than skip the pipeline it called
    `add_text_to_index` with the file read as UTF-8 -- which means a PDF or DOCX
    arrived as mojibake, markdown was never sectioned, and nothing purged the
    previous vectors, so re-indexing appended a second copy instead of replacing
    it. The UI dispatches to that path, so every improvement made to
    `add_file_to_index` was invisible to the people actually using the product.

    This bridges the two: open a context, load the row, run the real pipeline.
    """
    try:
        from backend.app import get_or_create_app
        app = get_or_create_app()
    except Exception as e:
        logger.warning("Cannot reach an app context to index document %s: %s", document_id, e)
        return False

    with app.app_context():
        from backend.models import db, Document as DBDocument
        try:
            doc = db.session.get(DBDocument, int(document_id))
        except Exception as e:
            logger.warning("Could not load document %s: %s", document_id, e)
            return False
        if doc is None:
            logger.warning("No document row %s to index", document_id)
            return False
        return bool(add_file_to_index(file_path, doc))


def add_text_to_index(text: str, metadata: Dict[str, Any], project_id: Optional[str] = None,
                      replace_where: Optional[List[str]] = None) -> Optional[bool]:
    """Add text to the vector index.

    `replace_where` names the metadata keys that identify this content, e.g.
    ["source", "folder_id"]. Nodes matching those values are removed first, so
    re-running a producer replaces its previous output instead of leaving a rival
    copy behind. Without it, re-analysing a repository left the old architectural
    summary in the index, still retrievable and still competing with the new one.
    Omitted, behaviour is unchanged and the text is simply appended.

    Returns: True = indexed; False = a real failure (no index / exception);
    None = nothing to index (content produced no chunkable text — a benign skip,
    not an error). None stays falsy so existing truthiness checks are unchanged,
    but callers that care can distinguish empty (None) from failed (False).
    """
    global index, storage_context

    try:
        if replace_where:
            # Not every caller passes a plain dict -- metadata_indexing_service builds a
            # small object with attributes -- so read defensively rather than assume.
            def _meta_get(key):
                if isinstance(metadata, dict):
                    return metadata.get(key)
                return getattr(metadata, key, None)

            _identity = {k: _meta_get(k) for k in replace_where if _meta_get(k) is not None}
            if _identity:
                _removed = purge_nodes_by_metadata(_identity)
                if _removed:
                    logger.info("Replaced %d previously indexed node(s) for %s",
                                _removed, _identity)
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
        
        from backend.utils.enhanced_rag_chunking import get_shared_chunker
        rag_chunker = get_shared_chunker()

        nodes = rag_chunker.chunk_documents([document], strategy_name='auto')
        
        with _index_operation_lock:
            if not nodes or len(nodes) == 0:
                logger.warning("add_text_to_index: content produced no nodes — nothing to index")
                return None  # empty, not a failure
            
            valid_nodes = []
            for node in nodes:
                if hasattr(node, 'text') and node.text and hasattr(node, 'metadata'):
                    valid_nodes.append(node)
                else:
                    logger.warning(f"BUG FIX 3: Skipping invalid node: {type(node)}")
            
            if not valid_nodes:
                logger.warning("add_text_to_index: all nodes were empty/invalid — nothing to index")
                return None  # empty content, not a failure

            local_index.insert_nodes(valid_nodes)
            _record_index_embedding_model(project_id)  # stamp the model the index was built with

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
        _maybe_collect()



def _md_supports(file_extension: str) -> bool:
    try:
        from backend.utils.markdown_sections import supports
        return supports(file_extension)
    except Exception:
        return False


def _md_load(file_path: str, filename: str, doc_cls):
    try:
        from backend.utils.markdown_sections import load_documents
        return load_documents(file_path, filename, doc_cls)
    except Exception as e:
        logger.warning("Markdown loader unavailable for %s: %s", filename, e)
        return None


def _docling_supports(file_extension: str) -> bool:
    try:
        from backend.utils.docling_loader import supports
        return supports(file_extension)
    except Exception:
        return False


def _docling_load(file_path: str, filename: str, doc_cls):
    try:
        from backend.utils.docling_loader import load_documents
        return load_documents(file_path, filename, doc_cls)
    except Exception as e:
        logger.warning("Docling loader unavailable for %s: %s", filename, e)
        return None


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

        # Docling runs ahead of EnhancedFileProcessor for the layout-bearing formats.
        # The adapter also claims .pdf/.docx, but it returns flat text; Docling returns
        # reading order, section headers and page provenance, which is what makes a
        # retrieved chunk citable. It returns None when it cannot help, so the adapter
        # and the legacy readers below remain the fallback chain.
        if _docling_supports(file_extension):
            _dl_docs = _docling_load(str(path_obj), filename, LlamaDocument)
            if _dl_docs:
                for _d in _dl_docs:
                    _d.metadata.setdefault("client", client)
                    _d.metadata.setdefault("upload_date", upload_date)
                logger.info("Docling handled %s (%d page docs)", filename, len(_dl_docs))
                return _dl_docs

        # Markdown: split on headings so each section carries a breadcrumb. Previously
        # .md fell all the way through to SimpleDirectoryReader as one flat blob.
        if _md_supports(file_extension):
            _md_docs = _md_load(str(path_obj), filename, LlamaDocument)
            if _md_docs:
                for _d in _md_docs:
                    _d.metadata.setdefault("client", client)
                    _d.metadata.setdefault("upload_date", upload_date)
                return _md_docs

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
        elif file_extension == ".pdf" and _docling_supports(file_extension):
            # Docling first: recovers reading order, section headers and per-item page
            # provenance. Returns None (not an exception) when it cannot help, so the
            # PDFReader path below still runs rather than dropping the file.
            _dl = _docling_load(str(path_obj), filename, LlamaDocument)
            if _dl:
                documents.extend(_dl)
            elif PDFReaderClass:
                try:
                    logger.debug(f"Docling unavailable for {filename}; using PDFReader")
                    pdf_reader = PDFReaderClass()
                    loaded_docs = pdf_reader.load_data(file=path_obj)
                    for doc in loaded_docs:
                        doc.metadata = doc.metadata or {}
                        doc.metadata["source_filename"] = filename
                        doc.metadata["file_path"] = str(path_obj)
                        doc.metadata["parsed_by"] = "PDFReader"
                    documents.extend(loaded_docs)
                except Exception as e:
                    logger.error(f"Failed to parse PDF {filename}: {e}", exc_info=True)
                    return []

        elif file_extension in (".docx", ".pptx") and _docling_supports(file_extension):
            _dl = _docling_load(str(path_obj), filename, LlamaDocument)
            if _dl:
                documents.extend(_dl)

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

    timings: Dict[str, float] = {}
    _embed_clock = _EmbedClock()

    _lazy_load_llamaindex()
    _lazy_load_optional_components()

    with _phase("index_init_ms", timings):
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
            with _phase("parse_ms", timings):
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

            doc.id_ = f"doc_{db_document.id}_{hashlib.sha256((doc.text or '').encode('utf-8')).hexdigest()[:16]}"
        
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
                from backend.utils.enhanced_rag_chunking import get_shared_chunker
                rag_chunker = get_shared_chunker()
                with _phase("chunk_ms", timings):
                    nodes = rag_chunker.chunk_documents(documents, strategy_name='auto')
                logger.info(f"Enhanced RAG chunking produced {len(nodes)} nodes from {len(documents)} documents")

                # Contextual Retrieval for prose, mirroring the code branch above: the
                # embedded text names its document, section and page, so a chunk lifted
                # out of the middle of a file still says where it came from. Raw text is
                # preserved in metadata["original_text"].
                try:
                    from backend.utils.contextual_prepender import prepend_context_to_document_nodes
                    _ctx_n = prepend_context_to_document_nodes(nodes)
                    logger.info(f"Contextual prefix applied to {_ctx_n}/{len(nodes)} prose nodes")
                except Exception as e:
                    logger.warning(f"Document context prepending skipped: {e}")

                try:
                    stats = rag_chunker.get_chunking_stats()
                    logger.info(f"Chunking stats: {stats}")
                except Exception:
                    pass

            logger.info(f"Generated {len(nodes)} nodes from {len(documents)} documents")
            
            # Replace, don't append: pgvector INSERTs rather than upserting by node id,
            # so without this a re-index leaves the previous copy in place alongside
            # the new one.
            try:
                with _phase("purge_ms", timings):
                    purge_document_vectors(getattr(db_document, "id", None),
                                           getattr(db_document, "project_id", None))
            except Exception as _pe:
                logger.warning("Pre-insert purge skipped: %s", _pe)

            # Collapse duplicate node ids before inserting. Node ids are derived from
            # (document, section, chunk text), so a document that genuinely repeats
            # content -- a code inventory full of ``` fences, say -- yields several
            # chunks with the SAME id. The vector store has no upsert, so each becomes
            # its own row, and one passage then occupies several of the caller's
            # result slots. Observed: one file at 1,638 rows for 796 distinct ids,
            # with a single id stored 11 times.
            _seen_ids, _deduped = set(), []
            for _n in nodes:
                _nid = getattr(_n, "node_id", None)
                if _nid is not None and _nid in _seen_ids:
                    continue
                if _nid is not None:
                    _seen_ids.add(_nid)
                _deduped.append(_n)
            if len(_deduped) != len(nodes):
                logger.info("Collapsed %d duplicate node id(s) before insert",
                            len(nodes) - len(_deduped))
                nodes = _deduped

            with _index_operation_lock:
                _dispatcher = None
                try:
                    from llama_index.core.instrumentation import get_dispatcher
                    _dispatcher = get_dispatcher()
                    _dispatcher.add_event_handler(_embed_clock)
                except Exception:
                    pass
                try:
                    with _phase("insert_ms", timings):
                        index.insert_nodes(nodes)
                finally:
                    if _dispatcher is not None:
                        try:
                            _dispatcher.event_handlers.remove(_embed_clock)
                        except Exception:
                            pass
                _record_index_embedding_model(getattr(db_document, "project_id", None))  # stamp model

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
                    with _phase("persist_ms", timings):
                        storage_context.persist(persist_dir=persist_dir)
            
            timings["embed_ms"] = round(_embed_clock.ms, 1)
            timings["embed_calls"] = _embed_clock.calls
            timings["vstore_ms"] = round(timings.get("insert_ms", 0.0) - _embed_clock.ms, 1)
            timings["nodes"] = len(nodes)
            timings["document_id"] = getattr(db_document, "id", None)
            timings["filename"] = getattr(db_document, "filename", None)
            _LAST_PHASE_TIMINGS.clear()
            _LAST_PHASE_TIMINGS.update({k: (round(v, 1) if isinstance(v, float) else v)
                                        for k, v in timings.items()})

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
            
            _maybe_collect(file_size_mb)
                
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

def _session_in_transaction() -> bool:
    """Whether the current DB session is already inside a transaction.

    SQLAlchemy 2.0's `scoped_session` does NOT proxy `in_transaction()` (only the
    real Session does), so `db.session.in_transaction()` raises AttributeError and
    used to abort every status update -> every HTTP-triggered index. Calling the
    scoped_session (`db.session()`) returns the real Session, which has it. Mirrors
    the guarded pattern in backend/utils/context_bridge.py.
    """
    try:
        sess = db.session() if callable(db.session) else db.session
        return bool(sess.in_transaction())
    except Exception:
        return False


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
            if _session_in_transaction():
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


def is_indexing_paused() -> bool:
    """Runtime check for the user-facing 'Pause Indexing' toggle in Settings.
    When true, new indexing work (embedding/vectorization) should be skipped
    to reduce machine/GPU load. Pending documents stay PENDING.
    """
    try:
        from backend.models import SystemSetting
        # Use a fresh query to avoid stale sessions in Celery workers
        row = SystemSetting.query.get("indexing_paused")
        if row:
            val = str(getattr(row, "value", "") or "").strip().lower()
            return val in ("true", "1", "yes", "on")
        return False
    except Exception as e:
        # Never crash indexing path over a settings read
        logger.debug(f"is_indexing_paused check failed (non-fatal): {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logger.info("Running indexing_service.py standalone for testing.")
    pass
