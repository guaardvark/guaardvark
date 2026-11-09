"""The prompts an index's vectors were embedded with.

A model's document prompt (config.EMBEDDING_PROMPTS) only helps an index whose
passages were embedded with it, and a query has to be embedded to match what
the index holds. So the prompt pair is recorded on the index, in the
embedding_meta.json that already names its model, and every client that writes
to or searches the index takes its prompts from that record.

- An index with no record was built before prompts were recorded. Its passages
  carry no prompt, so both sides stay unprompted until it is rebuilt.
- An index with no vector rows yet is new: the pair a new index uses for the
  active model (get_embedding_prompts(..., for_index=True)) is recorded the
  first time anyone asks.
- A rebuild (an embedding model switch, or "Re-index for better search") records
  that pair before the documents are queued.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PROMPTS_KEY = "prompts"
NO_PROMPTS: Tuple[Optional[str], Optional[str]] = (None, None)

# A "no record" answer depends on whether the tables hold rows, so it is
# re-checked this often; a recorded answer changes only with the file.
NO_RECORD_TTL_S = 10.0

_lock = threading.Lock()
_cache: dict = {"key": None, "value": NO_PROMPTS, "recorded": False, "at": 0.0}
_same_model_cache: dict = {}


def meta_path() -> str:
    """embedding_meta.json of the global index root, where the record lives.

    One record per installation: a rebuild empties or re-queues every table of
    the width, and one process-wide client embeds for all of them.
    """
    from backend.config import INDEX_ROOT
    return os.path.join(os.getenv("GUAARDVARK_INDEX_ROOT", INDEX_ROOT), "embedding_meta.json")


def read_meta(path: Optional[str] = None) -> dict:
    try:
        with open(path or meta_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_meta(updates: dict, path: Optional[str] = None) -> None:
    """Merge ``updates`` into embedding_meta.json, replacing the file atomically.

    Web and worker processes read it while another writes; a reader must never
    see half a file.
    """
    path = path or meta_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = read_meta(path)
    data.update(updates)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _norm(p: Optional[str]) -> Optional[str]:
    return p.strip() if isinstance(p, str) and p.strip() else None


def _same(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    key = (a, b)
    if key not in _same_model_cache:
        try:
            from backend.utils.ollama_resource_manager import same_model
            _same_model_cache[key] = same_model(a, b) is True
        except Exception:
            return False
    return _same_model_cache[key]


def _active_model() -> str:
    from backend.config import get_active_embedding_model
    return get_active_embedding_model()


def recorded_prompts(model_name: str, path: Optional[str] = None) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """(query, document) recorded for ``model_name``, or None when there is no
    record for it. Reads the file only, so it is safe at import time."""
    rec = read_meta(path).get(PROMPTS_KEY)
    if not isinstance(rec, dict) or not _same(rec.get("model"), model_name):
        return None
    return _norm(rec.get("query")), _norm(rec.get("document"))


def record_declared_prompts(model_name: Optional[str] = None, path: Optional[str] = None):
    """Record the prompts a new index uses for the model, and return them.

    Only for an index that is empty or is about to have every document
    embedded again; anything else would label old vectors with a new prompt.
    """
    from backend.config import get_embedding_prompts
    model = model_name or _active_model()
    query, document = get_embedding_prompts(model, for_index=True)
    updates = {PROMPTS_KEY: {
        "model": model, "query": query, "document": document,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }}
    if "embedding_model" not in read_meta(path):
        updates["embedding_model"] = model
    write_meta(updates, path)
    with _lock:
        _cache["key"] = None
    logger.info("Index embedding prompts recorded for %s: query=%r document=%r", model, query, document)
    return query, document


def _vector_rows_exist() -> Optional[bool]:
    """Whether any vector table holds a row; None when that cannot be read."""
    if os.environ.get("GUAARDVARK_VECTOR_STORE", "pgvector").lower() != "pgvector":
        return _simple_store_has_vectors()
    try:
        from backend.services.indexing_service import _pg_connect, _test_table_prefix
        mine = f"data_guaardvark_{_test_table_prefix()}"
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = current_schema() AND tablename LIKE %s",
                    ("data_guaardvark%",),
                )
                tables = [r[0] for r in cur.fetchall() if r[0].startswith(mine)
                          and (mine != "data_guaardvark_" or not r[0].startswith("data_guaardvark_test_"))]
                for t in tables:
                    cur.execute(f'SELECT EXISTS (SELECT 1 FROM "{t}")')
                    if cur.fetchone()[0]:
                        return True
                return False
        finally:
            conn.close()
    except Exception as e:
        logger.debug("Could not tell whether the vector tables hold rows: %s", e)
        return None


def _simple_store_has_vectors() -> Optional[bool]:
    path = os.path.join(os.path.dirname(meta_path()), "default__vector_store.json")
    try:
        if not os.path.exists(path):
            return False
        if os.path.getsize(path) > 65536:
            return True
        with open(path) as f:
            return bool((json.load(f) or {}).get("embedding_dict"))
    except Exception:
        return None


def index_prompts(model_name: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(query_prompt, document_prompt) to embed with for the index, None for none.

    The recorded pair when there is one for the active model; the pair a new
    index uses, recorded now, when the index holds no vectors; otherwise none.
    """
    model = model_name or _active_model()
    path = meta_path()
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = None
    now = time.monotonic()
    with _lock:
        if _cache["key"] == (path, mtime, model) and (
                _cache["recorded"] or now - _cache["at"] < NO_RECORD_TTL_S):
            return _cache["value"]

    value = recorded_prompts(model, path)
    recorded = value is not None
    if not recorded:
        if _vector_rows_exist() is False:
            value, recorded = record_declared_prompts(model, path), True
            try:
                mtime = os.stat(path).st_mtime_ns
            except OSError:
                mtime = None
        else:
            value = NO_PROMPTS
    with _lock:
        _cache.update(key=(path, mtime, model), value=value, recorded=recorded, at=now)
    return value


def prompt_status(model_name: Optional[str] = None) -> dict:
    """What the index embeds with, against what a new index would for the model.

    ``reindex_suggested`` is true when a new index would use a document prompt
    this one was built without: only a rebuild changes the stored passages.
    """
    from backend.config import get_embedding_prompts
    model = model_name or _active_model()
    new_query, new_document = get_embedding_prompts(model, for_index=True)
    query, document = index_prompts(model)
    return {
        "model": model,
        "new_index": {"query": new_query, "document": new_document},
        "index": {"query": query, "document": document,
                  "recorded": recorded_prompts(model) is not None},
        "reindex_suggested": bool(new_document) and _norm(document) != _norm(new_document),
    }
