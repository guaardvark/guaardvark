"""Contextual chunk prepending for improved RAG retrieval.

Implements Anthropic's Contextual Retrieval technique: prepend a short context
string to each chunk before embedding so the embedding captures file-level and
repo-level context.

Two templates, because code and prose situate a chunk differently: code by
repository/file/symbol, prose by document/section/page. Both preserve the raw
text in metadata["original_text"] so the prefix never reaches the reader as if
it were part of the source.
"""

import logging
from typing import List, Optional

from llama_index.core.schema import TextNode

logger = logging.getLogger(__name__)


def generate_chunk_context(
    file_path: str,
    repo_name: Optional[str],
    language: str,
    symbol_name: Optional[str] = None,
    symbol_type: Optional[str] = None,
) -> str:
    """Generate a context prefix for a code chunk (template mode).

    Returns a 50-100 token string that situates the chunk within the repo.
    """
    parts = [f"[{language}]"]
    if repo_name:
        parts.append(f"Repository: {repo_name}.")
    parts.append(f"File: {file_path}.")
    if symbol_name and symbol_type:
        parts.append(f"This is the {symbol_type} `{symbol_name}`.")
    return " ".join(parts) + "\n\n"


def prepend_context_to_nodes(
    nodes: List[TextNode],
    repo_name: Optional[str] = None,
) -> None:
    """Prepend contextual information to each node's text in-place.

    Preserves the original text in node.metadata["original_text"].
    Skips nodes that don't have a 'language' key in metadata.
    """
    for node in nodes:
        language = node.metadata.get("language")
        if not language:
            continue

        file_path = node.metadata.get("file_path", "unknown")
        symbol_name = node.metadata.get("symbol_name")
        symbol_type = node.metadata.get("symbol_type")

        context = generate_chunk_context(
            file_path=file_path,
            repo_name=repo_name,
            language=language,
            symbol_name=symbol_name,
            symbol_type=symbol_type,
        )

        _stash_original(node, node.text)
        node.text = context + node.text


def _stash_original(node: TextNode, text: str) -> None:
    """Keep the pre-prefix text for citation, out of the embedding.

    The raw text is worth keeping: a citation should quote what the document
    said, not the breadcrumb this module prepended. But metadata is concatenated
    ahead of the chunk before embedding, so storing it without excluding it means
    every chunk is embedded twice -- once as its text and once as its own
    metadata. Measured on a live corpus, that duplicate was 63.7% of the metadata
    string and about a third of everything sent to the model.
    """
    node.metadata["original_text"] = text
    for attr in ("excluded_embed_metadata_keys", "excluded_llm_metadata_keys"):
        current = list(getattr(node, attr, None) or [])
        if "original_text" not in current:
            current.append("original_text")
            setattr(node, attr, current)


def generate_document_context(
    source_filename: str,
    heading_path: Optional[str] = None,
    page_label: Optional[str] = None,
) -> str:
    """Generate a context prefix for a prose chunk.

    Mirrors generate_chunk_context for documents: names the file, the section it
    came from, and the page when the parser recovered one.
    """
    parts = [f"Document: {source_filename}."]
    if heading_path:
        parts.append(f"Section: {heading_path}.")
    if page_label:
        parts.append(f"Page {page_label}.")
    return " ".join(parts) + "\n\n"


def prepend_context_to_document_nodes(nodes: List[TextNode]) -> int:
    """Prepend document context to prose nodes in-place. Returns how many were changed.

    Skips nodes already carrying a prefix (original_text set) and nodes with no
    source filename -- prefixing "Document: unknown." would add tokens and no
    information.
    """
    changed = 0
    for node in nodes:
        meta = getattr(node, "metadata", None) or {}
        if "original_text" in meta:
            continue
        source = meta.get("source_filename") or meta.get("filename")
        if not source:
            continue

        context = generate_document_context(
            source_filename=source,
            heading_path=meta.get("heading_path"),
            page_label=meta.get("page_label"),
        )
        node.metadata = meta
        _stash_original(node, node.text)
        node.text = context + node.text
        changed += 1
    return changed
