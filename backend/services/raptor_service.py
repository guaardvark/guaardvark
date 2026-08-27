"""RAPTOR: recursive clustering and summarisation over the indexed corpus.

Top-k retrieval answers "where is X stated". It is structurally poor at "how did
this evolve", "what themes recur", "what is this corpus about" -- the answer to
those is not contained in any k chunks, so no reranker can rescue it.

RAPTOR clusters the leaf chunks, writes an LLM summary per cluster, indexes those
summaries as nodes, and recurses on them. A query about themes then retrieves a
level-2 summary the same way it would retrieve a leaf, because the summaries live
in the same index.

Build cost is real: one LLM call per cluster per level, competing with image and
video generation for the same GPU. It is therefore an explicit operation, never a
side effect of ingest.
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

SUMMARY_CONTENT_TYPE = "raptor_summary"

SUMMARY_PROMPT = """Below are related excerpts from a document collection.

Write a dense factual summary of what they collectively cover. State the concrete
subjects, decisions and claims present. Do not add information that is not in the
excerpts, and do not editorialise.

Excerpts:
{context}

Summary:"""


def _get_llm():
    """The LLM to summarise with, with thinking disabled.

    Summarisation wants a dense restatement of text it has been handed, not
    reasoning, and chain-of-thought is not free here. Measured on this project's
    workstation against an identical prompt, three runs each:

        thinking on  -> 18.6 s / 20.7 s / 30.0 s
        thinking off ->  0.48 s / 0.48 s / 0.45 s

    ~45x, and the no-thinking answer was *longer* (168 vs 75 chars) and no worse.
    Across a build this is the difference between minutes and most of a day. The
    shared `Settings.llm` is copied rather than mutated so turning thinking off
    for summaries does not turn it off for chat.
    """
    llm = None
    try:
        from llama_index.core import Settings
        llm = getattr(Settings, "llm", None)
    except Exception:
        pass
    if llm is None:
        try:
            from flask import current_app
            llm = current_app.config.get("LLAMA_INDEX_LLM")
        except Exception:
            return None
    if llm is None:
        return None
    # Only Ollama-backed LLMs carry `thinking`; anything else is returned as-is.
    if getattr(llm, "thinking", None) in (None, True) and hasattr(llm, "model_copy"):
        try:
            return llm.model_copy(update={"thinking": False})
        except Exception:
            logger.debug("RAPTOR: could not disable thinking on %s", type(llm).__name__)
    return llm


def _fetch_leaf_embeddings(project_id=None) -> Dict[str, Any]:
    """Read node ids, text and embeddings straight from pgvector.

    Re-embedding to cluster would double the GPU cost for vectors that already
    exist a few centimetres away in Postgres.
    """
    import numpy as np
    from backend.services.indexing_service import _pg_connect, _pg_table_name, _vector_backend

    if _vector_backend() != "pgvector":
        return {"error": "RAPTOR build currently requires the pgvector backend"}

    table = _pg_table_name(project_id)
    if not table:
        return {"error": "embedding dimension unknown"}

    conn = _pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT node_id, text, embedding::text, metadata_ FROM "data_{table}" '
                f"WHERE metadata_->>'content_type' IS DISTINCT FROM %s",
                (SUMMARY_CONTENT_TYPE,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return {"error": "no leaf nodes indexed"}

    ids, texts, vecs, metas = [], [], [], []
    for node_id, text, emb_text, meta in rows:
        if not emb_text:
            continue
        try:
            vec = np.fromstring(emb_text.strip("[]"), sep=",", dtype=np.float32)
        except Exception:
            continue
        if vec.size == 0:
            continue
        ids.append(node_id)
        texts.append(text or "")
        vecs.append(vec)
        metas.append(meta or {})

    if not vecs:
        return {"error": "no parseable embeddings"}
    return {"ids": ids, "texts": texts, "embeddings": np.vstack(vecs), "metadata": metas}


def _cluster(embeddings, target_size: int) -> List[List[int]]:
    """Group row indices into clusters of roughly `target_size`."""
    import numpy as np
    from sklearn.cluster import KMeans

    n = embeddings.shape[0]
    k = max(2, min(n // max(2, target_size), n - 1))
    if n <= 2:
        return [list(range(n))]

    # Cosine space: normalise so euclidean k-means approximates angular distance,
    # which is what the retriever ranks by.
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(embeddings / norms)

    groups: Dict[int, List[int]] = {}
    for idx, label in enumerate(km.labels_):
        groups.setdefault(int(label), []).append(idx)
    return [g for g in groups.values() if g]


def build_raptor_tree(
    project_id=None,
    max_levels: int = 2,
    target_cluster_size: int = 8,
    max_chars_per_cluster: int = 12000,
    progress_cb: Optional[Callable[[str], None]] = None,
    replace: bool = True,
) -> Dict[str, Any]:
    """Build summary levels over the indexed corpus and insert them into the index.

    Returns a report; never raises.

    `replace` (default true) clears existing summaries first, because the vector
    store appends rather than upserting: a second build without it leaves both
    generations in the index, and a query then retrieves two summaries of the same
    cluster. Requiring the caller to remember `clear_raptor_summaries()` made
    correctness depend on a docstring. Pass replace=False only to add levels on
    top of a tree that is known to be current.
    """
    from llama_index.core.schema import TextNode
    import backend.services.indexing_service as isvc

    def note(msg: str):
        logger.info("RAPTOR: %s", msg)
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    llm = _get_llm()
    if llm is None:
        return {"ok": False, "error": "no LLM available for summarisation"}

    data = _fetch_leaf_embeddings(project_id)
    if "error" in data:
        return {"ok": False, "error": data["error"]}

    isvc.get_or_create_index(project_id=str(project_id) if project_id else None)
    if isvc.index is None:
        return {"ok": False, "error": "index unavailable"}

    if replace:
        cleared = clear_raptor_summaries(project_id)
        if cleared.get("removed"):
            note(f"cleared {cleared['removed']} summary node(s) from the previous build")

    import numpy as np
    texts = data["texts"]
    metas = data["metadata"]
    embeddings = data["embeddings"]
    note(f"{len(texts)} leaf nodes, embedding dim {embeddings.shape[1]}")

    created: List[Dict[str, Any]] = []
    level_texts, level_embeddings, level_metas = texts, embeddings, metas

    for level in range(1, max_levels + 1):
        if len(level_texts) < 3:
            note(f"level {level}: only {len(level_texts)} inputs — stopping")
            break

        clusters = _cluster(level_embeddings, target_cluster_size)
        note(f"level {level}: {len(level_texts)} inputs → {len(clusters)} clusters")

        summary_nodes, summary_texts = [], []
        for ci, member_idx in enumerate(clusters):
            if len(member_idx) < 2:
                continue
            joined, used = [], 0
            for i in member_idx:
                t = (level_texts[i] or "").strip()
                if not t:
                    continue
                if used + len(t) > max_chars_per_cluster:
                    t = t[: max(0, max_chars_per_cluster - used)]
                joined.append(t)
                used += len(t)
                if used >= max_chars_per_cluster:
                    break
            if not joined:
                continue

            try:
                resp = llm.complete(SUMMARY_PROMPT.format(context="\n\n---\n\n".join(joined)))
                summary = str(resp).strip()
            except Exception as e:
                logger.warning("RAPTOR: summarisation failed for cluster %d: %s", ci, e)
                continue
            if not summary:
                continue

            sources = sorted({
                (level_metas[i] or {}).get("source_filename")
                for i in member_idx
                if (level_metas[i] or {}).get("source_filename")
            })
            node_meta = {
                "content_type": SUMMARY_CONTENT_TYPE,
                "raptor_level": level,
                "cluster_size": len(member_idx),
                "source_filename": f"[corpus summary L{level}#{ci}]",
                "covers_sources": ", ".join(sources[:20]),
                "parsed_by": "raptor",
            }
            # Scope is enforced by which table is read, so this is not what keeps a
            # summary out of another project. It is here so a summary answers the
            # same metadata questions its leaves do: anything that filters on
            # project would otherwise drop every corpus-level answer while keeping
            # the documents it was built from.
            if project_id is not None:
                node_meta["project_id"] = str(project_id)
                node_meta["project_id_str"] = str(project_id)
            summary_nodes.append(TextNode(text=summary, metadata=node_meta))
            summary_texts.append(summary)

        if not summary_nodes:
            note(f"level {level}: produced no summaries — stopping")
            break

        with isvc._index_operation_lock:
            isvc.index.insert_nodes(summary_nodes)
        created.append({"level": level, "summaries": len(summary_nodes)})
        note(f"level {level}: inserted {len(summary_nodes)} summaries")

        # Recurse over the summaries: embed them once for the next clustering pass.
        try:
            from llama_index.core import Settings
            embed_model = Settings.embed_model
            level_embeddings = np.vstack(
                [np.asarray(embed_model.get_text_embedding(t), dtype=np.float32) for t in summary_texts]
            )
        except Exception as e:
            note(f"level {level}: could not embed summaries for the next level ({e}) — stopping")
            break
        level_texts = summary_texts
        level_metas = [n.metadata for n in summary_nodes]

    try:
        isvc.storage_context.persist(persist_dir=isvc._persist_dir_for(project_id))
    except Exception as e:
        logger.warning("RAPTOR: persist failed: %s", e)

    return {"ok": True, "levels": created,
            "total_summaries": sum(c["summaries"] for c in created)}


def clear_raptor_summaries(project_id=None) -> Dict[str, Any]:
    """Delete summary nodes so a tree can be rebuilt from the leaves."""
    from backend.services.indexing_service import _pg_connect, _pg_table_name, _vector_backend
    if _vector_backend() != "pgvector":
        return {"ok": False, "error": "requires the pgvector backend"}
    table = _pg_table_name(project_id)
    if not table:
        return {"ok": False, "error": "embedding dimension unknown"}
    try:
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f'DELETE FROM "data_{table}" WHERE metadata_->>\'content_type\' = %s',
                    (SUMMARY_CONTENT_TYPE,),
                )
                removed = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "removed": removed}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
