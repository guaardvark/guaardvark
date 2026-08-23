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
