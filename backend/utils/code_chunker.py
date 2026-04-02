"""AST-aware code chunking using LlamaIndex CodeSplitter with tree-sitter."""

import logging
import os
from typing import List, Optional

from llama_index.core.schema import TextNode

logger = logging.getLogger(__name__)

CODE_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c_sharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".html": "html",
    ".css": "css",
    ".sh": "bash",
    ".bash": "bash",
    ".sql": "sql",
}

# Languages confirmed to work with LlamaIndex CodeSplitter
_CODESPLITTER_LANGUAGES = {
    "python", "javascript", "typescript", "java", "go", "rust",
    "c", "cpp", "ruby", "php", "c_sharp", "html", "css", "bash",
}


class CodeAwareChunker:
    """Chunk code files using tree-sitter AST parsing via LlamaIndex CodeSplitter.

    Falls back to line-based splitting for unsupported languages.
    """

    def __init__(self, chunk_lines: int = 60, chunk_lines_overlap: int = 10, max_chars: int = 3000):
        self.chunk_lines = chunk_lines
        self.chunk_lines_overlap = chunk_lines_overlap
        self.max_chars = max_chars

    def is_code_file(self, filename: str) -> bool:
        """Check if filename is a recognized code file."""
        ext = os.path.splitext(filename)[1].lower()
        return ext in CODE_LANGUAGE_MAP

    def get_language(self, filename: str) -> Optional[str]:
        """Get the tree-sitter language name for a filename."""
        ext = os.path.splitext(filename)[1].lower()
        return CODE_LANGUAGE_MAP.get(ext)

    def chunk_code(self, code: str, language: str, file_path: str) -> List[TextNode]:
        """Chunk code using AST-aware splitting.

        Args:
            code: Source code content.
            language: tree-sitter language name (e.g., "python", "javascript").
            file_path: Original file path (stored in metadata).

        Returns:
            List of TextNode with code chunks and metadata.
        """
        if language in _CODESPLITTER_LANGUAGES:
            try:
                return self._ast_chunk(code, language, file_path)
            except Exception as e:
                logger.warning(
                    f"AST chunking failed for {file_path} ({language}): {e}. "
                    f"Falling back to line-based chunking."
                )

        return self._line_based_chunk(code, language, file_path)

    def _ast_chunk(self, code: str, language: str, file_path: str) -> List[TextNode]:
        """Chunk using LlamaIndex CodeSplitter (tree-sitter)."""
        from llama_index.core.node_parser import CodeSplitter
        from llama_index.core import Document as LlamaDocument

        splitter = CodeSplitter(
            language=language,
            chunk_lines=self.chunk_lines,
            chunk_lines_overlap=self.chunk_lines_overlap,
            max_chars=self.max_chars,
        )

        doc = LlamaDocument(text=code, metadata={"file_path": file_path, "language": language})
        nodes = splitter.get_nodes_from_documents([doc])

        for node in nodes:
            if not node.metadata:
                node.metadata = {}
            node.metadata["language"] = language
            node.metadata["file_path"] = file_path
            node.metadata["chunking_method"] = "ast_tree_sitter"

        return nodes

    def _line_based_chunk(self, code: str, language: str, file_path: str) -> List[TextNode]:
        """Fallback: split code by line count with overlap."""
        lines = code.splitlines(keepends=True)
        nodes = []
        start = 0
        while start < len(lines):
            end = min(start + self.chunk_lines, len(lines))
            chunk_text = "".join(lines[start:end])
            if chunk_text.strip():
                node = TextNode(
                    text=chunk_text,
                    metadata={
                        "language": language,
                        "file_path": file_path,
                        "chunking_method": "line_based_fallback",
                        "line_start": start + 1,
                        "line_end": end,
                    },
                )
                nodes.append(node)
            start += self.chunk_lines - self.chunk_lines_overlap

        if not nodes and code.strip():
            nodes.append(TextNode(
                text=code,
                metadata={
                    "language": language,
                    "file_path": file_path,
                    "chunking_method": "single_chunk_fallback",
                },
            ))

        return nodes
