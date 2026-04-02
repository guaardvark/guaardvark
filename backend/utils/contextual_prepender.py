"""Contextual chunk prepending for improved RAG retrieval.

Implements Anthropic's Contextual Retrieval technique: prepend a short context
string to each chunk before embedding so the embedding captures file-level and
repo-level context.
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

        node.metadata["original_text"] = node.text
        node.text = context + node.text
