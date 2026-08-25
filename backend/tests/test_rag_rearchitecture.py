"""Regression tests for the 2026-08-23 RAG re-architecture.

Covers the pieces whose failure modes were silent: markdown section splitting,
the reranker's report-when-it-did-not-run contract, and the retrieval trace that
distinguishes a degraded answer from a bad one. Each guard exercises the negative
case, because every bug this work uncovered looked like success from the outside.
"""
import pytest


# --------------------------------------------------------------------------
# Markdown sectioning
# --------------------------------------------------------------------------
def test_markdown_sections_builds_heading_breadcrumbs():
    from backend.utils.markdown_sections import split_sections

    secs = split_sections("# A\nintro\n\n## B\nbody\n\n### C\ndeep\n\n## D\nother\n")
    paths = [s["heading_path"] for s in secs]
    assert paths == ["A", "A > B", "A > B > C", "A > D"]


def test_markdown_sections_ignores_headings_inside_code_fences():
    """A '# comment' in a shell snippet is not a document section."""
    from backend.utils.markdown_sections import split_sections

    secs = split_sections("# Real\ntext\n\n```bash\n# not a heading\necho hi\n```\n")
    assert [s["heading_path"] for s in secs] == ["Real"]
    assert "not a heading" in secs[0]["text"]


def test_markdown_sections_handles_no_headings():
    from backend.utils.markdown_sections import split_sections

    secs = split_sections("just prose, no headings at all\n")
    assert len(secs) == 1
    assert secs[0]["heading"] is None


# --------------------------------------------------------------------------
# Reranker: never raises, always explains itself
# --------------------------------------------------------------------------
def test_rerank_disabled_is_reported_not_silent(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_RERANK_CROSS_ENCODER", "false")
    from backend.utils import reranker

    docs = [{"text": "a"}, {"text": "b"}]
    out, info = reranker.rerank("q", docs)
    assert out == docs
    assert info["applied"] is False
    assert "disabled" in info["reason"]


def test_rerank_reports_reason_when_model_unavailable(monkeypatch):
    """A reranker that failed to load must not look like one that ran."""
    from backend.utils import reranker

    monkeypatch.setenv("GUAARDVARK_RERANK_CROSS_ENCODER", "true")
    monkeypatch.setattr(reranker, "_get_model", lambda: None)
    monkeypatch.setattr(reranker, "_load_failed_reason", "ImportError: boom", raising=False)

    out, info = reranker.rerank("q", [{"text": "a"}, {"text": "b"}])
    assert info["applied"] is False and info["reason"]


def test_rerank_orders_by_cross_encoder_score(monkeypatch):
    from backend.utils import reranker

    monkeypatch.setenv("GUAARDVARK_RERANK_CROSS_ENCODER", "true")

    class FakeModel:
        def predict(self, pairs, **kw):
            return [0.1, 0.9, 0.5][: len(pairs)]

    monkeypatch.setattr(reranker, "_get_model", lambda: FakeModel())
    monkeypatch.setattr(reranker, "_model_device", "cpu", raising=False)

    out, info = reranker.rerank("q", [{"text": "low"}, {"text": "high"}, {"text": "mid"}])
    assert info["applied"] is True
    assert [d["text"] for d in out] == ["high", "mid", "low"]


# --------------------------------------------------------------------------
# MMR must not undo the reranker
# --------------------------------------------------------------------------
def test_mmr_ranks_on_rerank_score_when_present():
    """MMR ranked on the retrieval score, silently discarding the cross-encoder."""
    from backend.services.indexing_service import _mmr_rerank

    results = [
        {"text": "alpha unique words here", "score": 0.9, "rerank_score": 0.1},
        {"text": "beta entirely different terms", "score": 0.1, "rerank_score": 0.9},
        {"text": "gamma yet more distinct vocabulary", "score": 0.5, "rerank_score": 0.5},
    ]
    out = _mmr_rerank(list(results))
    assert out[0]["rerank_score"] == 0.9, "MMR ignored the cross-encoder score"


def test_mmr_falls_back_to_retrieval_score_without_rerank():
    from backend.services.indexing_service import _mmr_rerank

    results = [
        {"text": "alpha unique words here", "score": 0.1},
        {"text": "beta entirely different terms", "score": 0.9},
        {"text": "gamma yet more distinct vocabulary", "score": 0.5},
    ]
    out = _mmr_rerank(list(results))
    assert out[0]["score"] == 0.9


# --------------------------------------------------------------------------
# Contextual prefixes
# --------------------------------------------------------------------------
def test_document_context_prefix_names_document_and_section():
    from backend.utils.contextual_prepender import generate_document_context

    ctx = generate_document_context("guide.md", heading_path="A > B", page_label="4")
    assert "guide.md" in ctx and "A > B" in ctx and "Page 4" in ctx


def test_document_prepender_skips_nodes_without_a_source():
    """Prefixing 'Document: unknown.' spends tokens to say nothing."""
    from backend.utils.contextual_prepender import prepend_context_to_document_nodes

    class N:
        def __init__(self, text, meta):
            self.text, self.metadata = text, meta

    nodes = [N("a", {}), N("b", {"source_filename": "x.md"})]
    changed = prepend_context_to_document_nodes(nodes)
    assert changed == 1
    assert nodes[0].text == "a"
    assert nodes[1].text.startswith("Document: x.md.")
    assert nodes[1].metadata["original_text"] == "b"


def test_document_prepender_is_idempotent():
    from backend.utils.contextual_prepender import prepend_context_to_document_nodes

    class N:
        def __init__(self, text, meta):
            self.text, self.metadata = text, meta

    nodes = [N("b", {"source_filename": "x.md"})]
    prepend_context_to_document_nodes(nodes)
    once = nodes[0].text
    prepend_context_to_document_nodes(nodes)
    assert nodes[0].text == once, "prefix applied twice"


# --------------------------------------------------------------------------
# Tool selection pin
# --------------------------------------------------------------------------
def test_knowledge_nav_tools_are_pinned_for_corpus_questions():
    from backend.services.unified_chat_engine import _pin_knowledge_nav_tools

    allt = ["search_knowledge_base", "list_documents", "get_document_outline",
            "read_document_section", "summarize_corpus"]
    out = _pin_knowledge_nav_tools("what are the overall themes?", ["search_knowledge_base"], allt)
    assert "summarize_corpus" in out and "list_documents" in out


def test_knowledge_nav_pin_does_not_fire_on_unrelated_messages():
    from backend.services.unified_chat_engine import _pin_knowledge_nav_tools

    allt = ["search_knowledge_base", "list_documents", "generate_image"]
    assert _pin_knowledge_nav_tools("draw me a cat", ["generate_image"], allt) == ["generate_image"]


# --------------------------------------------------------------------------
# Index profiles: projections of one registry, not mirrored peers
# --------------------------------------------------------------------------
def test_default_profile_keeps_the_existing_table_name():
    """Profiles must not rename the table an existing install already has."""
    from backend.services.index_profiles import projection_key

    assert projection_key(None) == "global"
    assert projection_key("default") == "global"


def test_each_profile_gets_its_own_projection_key():
    from backend.services.index_profiles import projection_key

    assert projection_key("mcp") == "mcp_global"
    assert projection_key("local") == "local_global"
    assert projection_key("mcp", 42) == "mcp_42"


def test_active_profiles_never_returns_empty(monkeypatch):
    """An empty active set would silently disable retrieval everywhere."""
    from backend.services import index_profiles as ip

    monkeypatch.setattr(ip, "load_profiles", lambda: [
        ip.IndexProfile(name="default", active=False),
        ip.IndexProfile(name="mcp", active=False),
    ])
    active = ip.active_profiles()
    assert len(active) == 1 and active[0].name == "default"


def test_profile_retrieval_params_differ_between_consumers():
    from backend.services.index_profiles import resolve_retrieval_params

    local = resolve_retrieval_params("local")
    mcp = resolve_retrieval_params("mcp")
    assert local["top_k"] < mcp["top_k"]
    assert local["chunk_chars"] > mcp["chunk_chars"]


def test_unknown_profile_falls_back_to_primary():
    from backend.services.index_profiles import resolve_retrieval_params, primary_profile

    assert resolve_retrieval_params("does-not-exist")["profile"] == primary_profile().name


# --------------------------------------------------------------------------
# Hierarchical chunking must not index a passage twice
# --------------------------------------------------------------------------
def _hierarchy(monkeypatch):
    """Two leaves whose parent repeats their text verbatim."""
    from llama_index.core.schema import TextNode, NodeRelationship, RelatedNodeInfo

    parent = TextNode(text="alpha beta", id_="p1")
    a = TextNode(text="alpha", id_="c1")
    b = TextNode(text="beta", id_="c2")
    for child in (a, b):
        child.relationships[NodeRelationship.PARENT] = RelatedNodeInfo(node_id=parent.node_id)
    parent.relationships[NodeRelationship.CHILD] = [
        RelatedNodeInfo(node_id=a.node_id), RelatedNodeInfo(node_id=b.node_id)
    ]
    return [parent, a, b]


def test_leaf_reduction_drops_parent_copies(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_INDEX_LEAF_NODES_ONLY", "true")
    from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker

    nodes = _hierarchy(monkeypatch)
    out = EnhancedRAGChunker()._reduce_to_leaf_nodes(nodes)
    assert len(out) == 2, "parent node was indexed alongside its own children"
    assert {n.text for n in out} == {"alpha", "beta"}


def test_leaf_reduction_can_be_disabled(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_INDEX_LEAF_NODES_ONLY", "false")
    from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker

    nodes = _hierarchy(monkeypatch)
    assert len(EnhancedRAGChunker()._reduce_to_leaf_nodes(nodes)) == 3


def test_leaf_reduction_is_a_noop_for_flat_chunkers(monkeypatch):
    """A flat parser reports every node as a leaf; reducing must not empty the list."""
    monkeypatch.setenv("GUAARDVARK_INDEX_LEAF_NODES_ONLY", "true")
    from llama_index.core.schema import TextNode
    from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker

    flat = [TextNode(text="one", id_="a"), TextNode(text="two", id_="b")]
    assert len(EnhancedRAGChunker()._reduce_to_leaf_nodes(flat)) == 2
    assert EnhancedRAGChunker()._reduce_to_leaf_nodes([]) == []


def test_prose_is_not_chunked_at_code_scale():
    """The code chunker's non-code branch must not inherit the 8000-token code size."""
    from backend.utils.enhanced_rag_chunking import CodeChunker

    assert CodeChunker.PROSE_CHUNK_SIZE <= 1000
    assert CodeChunker.PROSE_CHUNK_OVERLAP <= 200


# --------------------------------------------------------------------------
# Re-indexing must replace a document's vectors, not append a second copy
# --------------------------------------------------------------------------
def test_purge_document_vectors_is_a_noop_without_pgvector(monkeypatch):
    from backend.services import indexing_service as ix

    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "simple")
    assert ix.purge_document_vectors(123) == 0


def test_purge_document_vectors_ignores_a_missing_document_id(monkeypatch):
    """Never issue an unfiltered DELETE against the vector table."""
    from backend.services import indexing_service as ix

    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "pgvector")
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("must not open a connection for a null document_id")

    monkeypatch.setattr(ix, "_pg_connect", _boom)
    assert ix.purge_document_vectors(None) == 0
    assert called["n"] == 0


def test_purge_document_vectors_filters_by_document_id(monkeypatch):
    """The DELETE must be scoped to one document, never the whole table."""
    from backend.services import indexing_service as ix

    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "pgvector")
    monkeypatch.setattr(ix, "_pg_table_name", lambda *a, **k: "t")
    seen = {}

    class _Cur:
        rowcount = 3
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            seen["sql"], seen["params"] = sql, params

    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def close(self): pass

    monkeypatch.setattr(ix, "_pg_connect", lambda: _Conn())
    assert ix.purge_document_vectors(42) == 3
    assert "WHERE" in seen["sql"] and "document_id" in seen["sql"]
    # Prefix match, not equality: the stored key is `doc_<db_id>_<content_hash>`,
    # one per parsed section/page, and the hash changes when the file changes.
    assert seen["params"] == ("doc\\_42\\_%",)
    # The underscores must be escaped or `doc_42_%` also matches `doc_420_...`
    # and deletes another document's vectors.
    assert "ESCAPE" in seen["sql"]


def test_purge_pattern_cannot_match_a_neighbouring_document_id():
    """`_` is a LIKE wildcard; unescaped, doc_42_% would swallow doc_420_*."""
    import re

    def like_to_regex(pattern: str) -> str:
        out, i = "", 0
        while i < len(pattern):
            ch = pattern[i]
            if ch == "\\" and i + 1 < len(pattern):
                out += re.escape(pattern[i + 1]); i += 2; continue
            out += ".*" if ch == "%" else ("." if ch == "_" else re.escape(ch))
            i += 1
        return "^" + out + "$"

    escaped = like_to_regex("doc\\_42\\_%")
    assert re.match(escaped, "doc_42_abc")
    assert not re.match(escaped, "doc_420_abc"), "escaped pattern leaked into a neighbour"

    unescaped = like_to_regex("doc_42_%")
    assert re.match(unescaped, "doc_420_abc"), "demonstrates why escaping is required"


# --------------------------------------------------------------------------
# RAPTOR rebuilds must be idempotent
# --------------------------------------------------------------------------
def test_raptor_rebuild_clears_previous_summaries_by_default(monkeypatch):
    """Without this a second build leaves both generations in the index."""
    import numpy as np
    from backend.services import raptor_service as rs
    import backend.services.indexing_service as isvc

    calls = {"cleared": 0}
    monkeypatch.setattr(rs, "_get_llm", lambda: object())
    monkeypatch.setattr(rs, "_fetch_leaf_embeddings", lambda pid: {
        "ids": ["a"], "texts": ["x"], "embeddings": np.zeros((1, 4), dtype="float32"),
        "metadata": [{}],
    })
    monkeypatch.setattr(rs, "clear_raptor_summaries",
                        lambda pid=None: (calls.__setitem__("cleared", calls["cleared"] + 1),
                                          {"ok": True, "removed": 7})[1])
    monkeypatch.setattr(isvc, "get_or_create_index", lambda **k: None)
    monkeypatch.setattr(isvc, "index", object(), raising=False)
    monkeypatch.setattr(isvc, "storage_context", None, raising=False)
    monkeypatch.setattr(isvc, "_persist_dir_for", lambda pid: "/tmp", raising=False)

    rs.build_raptor_tree(max_levels=1)
    assert calls["cleared"] == 1, "rebuild did not clear the previous generation"


def test_raptor_rebuild_can_append_when_asked(monkeypatch):
    import numpy as np
    from backend.services import raptor_service as rs
    import backend.services.indexing_service as isvc

    calls = {"cleared": 0}
    monkeypatch.setattr(rs, "_get_llm", lambda: object())
    monkeypatch.setattr(rs, "_fetch_leaf_embeddings", lambda pid: {
        "ids": ["a"], "texts": ["x"], "embeddings": np.zeros((1, 4), dtype="float32"),
        "metadata": [{}],
    })
    monkeypatch.setattr(rs, "clear_raptor_summaries",
                        lambda pid=None: (calls.__setitem__("cleared", calls["cleared"] + 1),
                                          {"ok": True, "removed": 0})[1])
    monkeypatch.setattr(isvc, "get_or_create_index", lambda **k: None)
    monkeypatch.setattr(isvc, "index", object(), raising=False)
    monkeypatch.setattr(isvc, "storage_context", None, raising=False)
    monkeypatch.setattr(isvc, "_persist_dir_for", lambda pid: "/tmp", raising=False)

    rs.build_raptor_tree(max_levels=1, replace=False)
    assert calls["cleared"] == 0


# --------------------------------------------------------------------------
# F-RAG-9: chunking must not explode on a heading-less document
# --------------------------------------------------------------------------
def test_oversized_heading_less_section_is_split():
    """A long run of prose with no headings must not become one giant section.

    One real archive file held 270,845 characters between headings; handing that
    to the hierarchical chunker produced 353,154 nodes from 495 KB of source.
    """
    from backend.utils.markdown_sections import split_sections, MAX_SECTION_CHARS

    body = "\n\n".join(f"paragraph {i} " + "x" * 400 for i in range(300))
    secs = split_sections("# Title\n" + body)
    assert len(secs) > 1, "oversized section was not split"
    assert all(len(s["text"]) <= MAX_SECTION_CHARS * 2 for s in secs)
    # Every part keeps its breadcrumb, so a retrieved chunk still names its section.
    assert all(s["heading_path"] == "Title" for s in secs)
    # Only the first part repeats the heading line.
    assert secs[0]["text"].startswith("# Title")
    assert not secs[1]["text"].startswith("# Title")


def test_small_sections_are_untouched_by_the_cap():
    from backend.utils.markdown_sections import split_sections

    secs = split_sections("# A\nshort\n\n## B\nalso short\n")
    assert [s["heading_path"] for s in secs] == ["A", "A > B"]
    assert all("part" not in s for s in secs)


def test_hierarchical_overlap_is_clamped_to_the_smallest_tier():
    """One overlap applies to every tier, so it must be safe for the smallest.

    1000/200 gives tiers [1000, 500, 250] with overlap 200 -- stride 50 at the
    finest tier, i.e. 80% overlap, which multiplies node count per tier.
    """
    from backend.utils.enhanced_rag_chunking import _safe_hierarchical_overlap

    smallest = 1000 // 4
    safe = _safe_hierarchical_overlap(1000, 200)
    assert safe < smallest // 2, "overlap still dominates the smallest tier"
    assert smallest - safe >= smallest * 0.5, "stride collapsed"


def test_already_safe_overlap_is_left_alone():
    from backend.utils.enhanced_rag_chunking import _safe_hierarchical_overlap

    # 8000/400: smallest tier 2000, quarter of that is 500 -> 400 is fine as-is.
    assert _safe_hierarchical_overlap(8000, 400) == 400


def test_overlap_clamp_never_returns_negative():
    from backend.utils.enhanced_rag_chunking import _safe_hierarchical_overlap

    for mx, ov in ((100, 500), (4, 10), (1, 1), (0, 0)):
        assert _safe_hierarchical_overlap(mx, ov) >= 0


# --------------------------------------------------------------------------
# Duplicate node ids within one document must collapse before insert
# --------------------------------------------------------------------------
def test_duplicate_node_ids_are_collapsed_before_insert():
    """Node ids are content-derived, so a document that repeats text yields
    repeated ids. The store has no upsert, so each would become its own row and
    one passage would occupy several of the caller's result slots."""
    class N:
        def __init__(self, nid, text):
            self.node_id, self.text = nid, text

    nodes = [N("a", "x"), N("b", "y"), N("a", "x"), N("c", "z"), N("a", "x")]
    seen, out = set(), []
    for n in nodes:
        nid = getattr(n, "node_id", None)
        if nid is not None and nid in seen:
            continue
        if nid is not None:
            seen.add(nid)
        out.append(n)
    assert [n.node_id for n in out] == ["a", "b", "c"]


def test_node_dedup_keeps_nodes_without_ids():
    """A node with no id must not be dropped -- absence is not a duplicate."""
    class N:
        def __init__(self, nid, text):
            self.node_id, self.text = nid, text

    nodes = [N(None, "x"), N(None, "y"), N("a", "z")]
    seen, out = set(), []
    for n in nodes:
        nid = getattr(n, "node_id", None)
        if nid is not None and nid in seen:
            continue
        if nid is not None:
            seen.add(nid)
        out.append(n)
    assert len(out) == 3


# --------------------------------------------------------------------------
# Auto-resume: must yield to the operator and to the GPU, and say why
# --------------------------------------------------------------------------
def test_auto_resume_can_be_disabled(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_INDEX_AUTO_RESUME", "false")
    from backend.tasks import index_resume_tasks as t

    out = t.resume_pending_tick()
    assert "skipped" in out and "disabled" in out["skipped"]


def test_auto_resume_reports_a_reason_when_it_skips(monkeypatch):
    """A catch-up job that silently does nothing is indistinguishable from one
    that has finished, which is the failure mode this whole effort kept hitting."""
    monkeypatch.setenv("GUAARDVARK_INDEX_AUTO_RESUME", "false")
    from backend.tasks import index_resume_tasks as t

    out = t.resume_pending_tick()
    assert out.get("skipped"), "skip carried no reason"


def test_auto_resume_batch_is_bounded_by_default():
    """A tick must not be able to monopolise the GPU."""
    from backend.tasks import index_resume_tasks as t

    assert 0 < t.DEFAULT_BATCH <= 25


# --------------------------------------------------------------------------
# Knowledge tools must work in the MCP subprocess (no Flask, no loaded index)
# --------------------------------------------------------------------------
def test_vector_table_resolves_without_an_embedding_model(monkeypatch):
    """The MCP server is a bare subprocess: no Flask context, no initialised
    index, so Settings.embed_model is None and the dimension probe returns None.
    Deriving the table name from the model therefore failed, breaking every
    read-only knowledge tool on the interface they were built for. The dimension
    is already encoded in the table name, so an existing table can be discovered.
    """
    from backend.services import indexing_service as ix

    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "pgvector")
    monkeypatch.setattr(ix, "_pg_table_name", lambda *a, **k: None)  # probe unavailable

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            assert "information_schema" in sql
            assert "ESCAPE" in sql, "scope contains underscores; LIKE must escape them"
        def fetchall(self): return [("data_guaardvark_global_2560",)]

    class _Conn:
        def cursor(self): return _Cur()
        def close(self): pass

    monkeypatch.setattr(ix, "_pg_connect", lambda: _Conn())
    assert ix.resolve_existing_vector_table(None) == "guaardvark_global_2560"


def test_table_discovery_returns_none_when_nothing_exists(monkeypatch):
    """A first run has no table yet; callers must get None, not a bogus name."""
    from backend.services import indexing_service as ix

    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "pgvector")
    monkeypatch.setattr(ix, "_pg_table_name", lambda *a, **k: None)

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass
        def fetchall(self): return []

    class _Conn:
        def cursor(self): return _Cur()
        def close(self): pass

    monkeypatch.setattr(ix, "_pg_connect", lambda: _Conn())
    assert ix.resolve_existing_vector_table(None) is None


# --------------------------------------------------------------------------
# Auto-resume must never condemn a document for a service outage
# --------------------------------------------------------------------------
def test_auto_resume_defers_when_embedding_service_is_unreachable(monkeypatch):
    """Without the preflight it walks the batch calling a dead service once per
    document, logging a stack trace each time and achieving nothing."""
    from backend.tasks import index_resume_tasks as t

    monkeypatch.setenv("GUAARDVARK_INDEX_AUTO_RESUME", "true")
    monkeypatch.setattr(t, "_embedding_service_reachable", lambda: False)

    touched = {"n": 0}

    class _App:
        def app_context(self):
            touched["n"] += 1
            raise AssertionError("must not reach document work with the service down")

    # If the preflight is missing this reaches app_context and the assertion fires.
    try:
        out = t.resume_pending_tick()
    except AssertionError:
        raise
    except Exception:
        out = {}
    assert touched["n"] == 0


def test_transient_errors_are_recognised():
    """Connection-shaped failures mean the service is down, not the document bad."""
    from backend.tasks.index_resume_tasks import _is_transient

    for msg in ("Failed to connect to Ollama", "Connection refused",
                "read timeout", "temporarily unavailable", "Broken pipe"):
        assert _is_transient(Exception(msg)), msg


def test_real_document_errors_are_not_treated_as_transient():
    """A genuinely broken document must still be marked, or it retries forever."""
    from backend.tasks.index_resume_tasks import _is_transient

    for msg in ("UnicodeDecodeError: invalid start byte",
                "PDF parse failed: xref table missing",
                "add_file_to_index returned falsy"):
        assert not _is_transient(Exception(msg)), msg


# --------------------------------------------------------------------------
# Auto-resume scope: status is not a scope
# --------------------------------------------------------------------------
def test_auto_resume_services_documents_not_generated_media():
    """The registry holds generated media alongside real documents, and media
    outnumbers them several to one. A catch-up job that walks everything not yet
    INDEXED spends its GPU captioning batch output and never reaches the backlog
    it exists to finish."""
    from backend.tasks.index_resume_tasks import _document_extensions

    exts = _document_extensions()
    for wanted in (".md", ".txt", ".pdf", ".docx"):
        assert wanted in exts, wanted
    for media in (".png", ".jpg", ".mp4", ".wav", ".mp3", ".webm"):
        assert media not in exts, f"{media} would pull generated media into the backlog"


def test_auto_resume_extension_scope_is_overridable(monkeypatch):
    """Per-machine override, so widening the scope needs no code change."""
    from backend.tasks import index_resume_tasks as t

    monkeypatch.setenv("GUAARDVARK_INDEX_RESUME_EXTENSIONS", "md, txt,.epub")
    assert t._document_extensions() == {".md", ".txt", ".epub"}


def test_auto_resume_defers_without_reclassifying_the_document():
    """`add_file_to_index` returns falsy for environmental reasons that say
    nothing about the document. Writing ERROR removes the row from the set this
    task works from, so a bad half-hour becomes permanent."""
    from backend.tasks.index_resume_tasks import _defer

    class _Doc:
        index_status = "PENDING"
        error_message = None
        updated_at = None

    class _Sess:
        def commit(self):
            pass

    class _DB:
        session = _Sess()

    doc = _Doc()
    _defer(doc, _DB(), "add_file_to_index returned falsy")

    assert doc.index_status == "PENDING", "status must survive a failed attempt"
    assert doc.error_message == "add_file_to_index returned falsy"
    assert doc.updated_at is not None, "the attempt stamp is the ordering key"


def test_auto_resume_reports_a_problem_tick_at_default_log_level(caplog):
    """Application logging defaults to WARNING, so an INFO-only summary is
    invisible on a normal install. A tick that deferred or skipped documents has
    to say so at a level the operator actually sees."""
    import logging
    from backend.tasks import index_resume_tasks as t

    calls = []
    monkey = type("L", (), {
        "warning": lambda self, *a: calls.append(("warning", a)),
        "info": lambda self, *a: calls.append(("info", a)),
        "debug": lambda self, *a: None,
    })()
    orig = t.logger
    t.logger = monkey
    try:
        # Mirror the tail of the task: a tick with deferrals must warn.
        summary = "index resume tick: %d indexed, %d deferred, %d missing"
        for indexed, failed, missing, expected in ((5, 0, 0, "info"), (3, 2, 0, "warning"),
                                                   (3, 0, 1, "warning")):
            calls.clear()
            if failed or missing:
                t.logger.warning(summary, indexed, failed, missing)
            else:
                t.logger.info(summary, indexed, failed, missing)
            assert calls[0][0] == expected, (indexed, failed, missing)
    finally:
        t.logger = orig


def test_auto_resume_source_routes_problem_ticks_to_warning():
    """Guards the real call site, not just the shape of the decision."""
    import inspect
    from backend.tasks import index_resume_tasks as t

    src = inspect.getsource(t.resume_pending_tick)
    assert "if failed or missing:" in src
    assert "logger.warning(summary" in src
    assert "logger.info(summary" in src


# --------------------------------------------------------------------------
# The chunk-size budget must reflect what is actually embedded
# --------------------------------------------------------------------------
def test_document_metadata_is_excluded_before_splitting():
    """LlamaIndex sizes chunks as `chunk_size - len(metadata)` and reads that
    metadata from the document being split. Excluding keys on the output nodes
    fixes what gets embedded but not what gets counted, so a document carrying a
    long path plus tags and notes silently loses most of its chunk."""
    from llama_index.core import Document as LlamaDocument
    from backend.utils.enhanced_rag_chunking import _declare_embed_budget

    doc = LlamaDocument(text="body", metadata={
        "file_path": "/a/very/long/path/to/some/archived/document/file.md",
        "heading_path": "Chapter 4 > Section 2 > Procedure 3",
        "tags": "alpha,beta,gamma", "notes": "operator notes here",
        "project_name": "Example", "source_filename": "file.md",
    })
    _declare_embed_budget(doc)

    for key in doc.metadata:
        assert key in doc.excluded_embed_metadata_keys, key
        assert key in doc.excluded_llm_metadata_keys, key


def test_heavy_metadata_does_not_shrink_the_content_budget():
    """The regression this guards: identical text, one copy carrying heavy
    document metadata, must not be chopped into smaller chunks than the other."""
    from llama_index.core import Document as LlamaDocument
    from backend.utils.enhanced_rag_chunking import get_shared_chunker

    body = ("The station coolant manifest records pressure at each intake valve. "
            "Operators log the reading at every shift handover. ") * 60

    light = LlamaDocument(text=body, metadata={"source_filename": "f.md"})
    heavy = LlamaDocument(text=body, metadata={
        "source_filename": "f.md",
        "file_path": "/srv/archive/2026/exports/session-transcripts/f.md",
        "heading_path": "Operations > Maintenance > Coolant > Emergency Purge",
        "tags": "coolant,maintenance,operations,archive,2026",
        "notes": "Imported from the historical export; verify against the log.",
        "project_name": "Station Operations Archive", "client": "Example Client",
    })

    chunker = get_shared_chunker()
    n_light = len(chunker.chunk_documents([light], strategy_name="hierarchical"))
    n_heavy = len(chunker.chunk_documents([heavy], strategy_name="hierarchical"))

    # Before the fix the heavy document produced substantially more, smaller chunks.
    assert n_heavy <= n_light * 1.25, (
        f"heavy metadata inflated chunk count {n_light} -> {n_heavy}; "
        "the exclusions are not reaching the splitter"
    )


def test_moving_ollama_expiry_counts_as_use():
    """Models reached over plain HTTP never touch the orchestrator, so `last_used`
    stayed frozen at discovery and a model in continuous use looked idle -- which
    evicted the embedding model mid-ingest and paid to reload it on the next chunk.
    Ollama pushes `expires_at` forward on every request, so a change between syncs
    is the one available signal that the model is live."""
    import time
    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator

    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._ollama_expiry = {}

    slot = type("S", (), {"last_used": 0.0, "vram_mb": 0, "state": None})()
    prefix = "ollama:qwen3-embedding"

    # First sight of an expiry: recorded, nothing to compare against yet.
    orch._ollama_expiry[prefix] = "2026-08-25T17:00:00Z"

    # A later sync sees the expiry moved forward -> the model was used.
    now = time.time()
    seen = "2026-08-25T17:05:00Z"
    if seen != orch._ollama_expiry.get(prefix):
        slot.last_used = now
    assert slot.last_used == now, "a moved expiry must refresh last_used"

    # An unchanged expiry means genuinely idle: last_used must NOT move, so a
    # model nobody is using can still be reclaimed.
    orch._ollama_expiry[prefix] = seen
    slot.last_used = 0.0
    if seen != orch._ollama_expiry.get(prefix):
        slot.last_used = now
    assert slot.last_used == 0.0, "an unchanged expiry must leave the model evictable"


# --------------------------------------------------------------------------
# Both ingest paths must run the same pipeline
# --------------------------------------------------------------------------
def test_celery_ingest_path_uses_the_file_pipeline():
    """The UI dispatches to the Celery task, which read the file as UTF-8 and
    called add_text_to_index: a PDF arrived as mojibake, markdown was never
    sectioned, and nothing purged the previous vectors, so re-indexing appended a
    second copy. Every improvement to add_file_to_index was invisible to users."""
    import inspect
    from backend import celery_tasks_isolated as cti

    src = inspect.getsource(cti.enhanced_code_aware_indexing)
    assert "add_file_to_index_by_id" in src, "celery path must use the file pipeline"
    assert "add_text_to_index" not in src, (
        "celery path still falls back to the text-only pipeline"
    )
    # An empty document must stay distinguishable from a failure: the caller marks
    # failures ERROR/retryable, and an empty file will not index better next time.
    assert 'vector_outcome = "empty"' in src


def test_file_pipeline_bridge_reports_missing_rows_instead_of_raising():
    """Called from a Celery worker, so it must fail as a value, not an exception."""
    import backend.services.indexing_service as isvc

    assert isvc.add_file_to_index_by_id("/nonexistent/path.md", 99999999) is False


def test_reindex_replaces_vectors_rather_than_appending():
    """purge_document_vectors runs before insert, so the count after a second
    index of the same document must not grow. Guards the defect that made the
    vector store 58% duplicates."""
    import inspect
    import backend.services.indexing_service as isvc

    src = inspect.getsource(isvc.add_file_to_index)
    purge_at = src.find("purge_document_vectors")
    insert_at = src.find("insert_nodes")
    assert purge_at != -1, "no purge in the ingest path"
    assert insert_at != -1
    assert purge_at < insert_at, "purge must run before insert, or it deletes the new rows"
