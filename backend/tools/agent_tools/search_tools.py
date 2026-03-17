#!/usr/bin/env python3
"""
Search and RAG Tools
Tools for semantic search and information retrieval
"""

import logging
from typing import Optional, List, Dict, Any

from backend.services.agent_tools import BaseTool, ToolResult, ToolParameter

logger = logging.getLogger(__name__)


class SearchCodebaseTool(BaseTool):
    """Semantic search across all indexed documents"""

    name = "search_codebase"
    description = (
        "Search the codebase using semantic search to find relevant information"
    )
    parameters = {
        "query": ToolParameter(
            name="query", type="string", required=True, description="Search query"
        ),
        "max_results": ToolParameter(
            name="max_results",
            type="int",
            required=False,
            description="Maximum number of results to return (default: 5)",
            default=5,
        ),
    }

    def execute(self, query: str, max_results: int = 5) -> ToolResult:
        """
        Semantic search using existing RAG system

        Args:
            query: Search query
            max_results: Max number of results

        Returns:
            ToolResult with search results
        """
        try:
            from backend.services.indexing_service import search_with_llamaindex

            # Execute search
            results = search_with_llamaindex(query, max_chunks=max_results)

            if results is None:
                results = []

            # Format results
            formatted_results = []
            for i, result in enumerate(results):
                if result and result.get("content"):
                    formatted_results.append(
                        {
                            "rank": i + 1,
                            "text": result.get("content", ""),
                            "source": result.get("metadata", {}).get(
                                "source_filename", "Unknown"
                            ),
                            "score": result.get("score", 0.0),
                        }
                    )

            # Create output text
            if formatted_results:
                output_lines = [
                    f"Found {len(formatted_results)} results for: {query}\n"
                ]
                for res in formatted_results:
                    output_lines.append(
                        f"[{res['rank']}] {res['source']} (score: {res['score']:.3f})"
                    )
                    output_lines.append(f"{res['text'][:200]}...")
                    output_lines.append("")
                output_text = "\n".join(output_lines)
            else:
                output_text = f"No results found for query: {query}"

            return ToolResult(
                success=True,
                output=output_text,
                metadata={
                    "query": query,
                    "result_count": len(formatted_results),
                    "results": formatted_results,
                },
            )

        except ImportError as e:
            logger.error(f"Search service not available: {e}")
            return ToolResult(success=False, error="Search system not available")
        except Exception as e:
            logger.error(f"Search failed for query '{query}': {e}", exc_info=True)
            return ToolResult(success=False, error=f"Search failed: {str(e)}")


class GrepSearchTool(BaseTool):
    """Text pattern search in files"""

    name = "grep_search"
    description = "Search for text patterns in files using grep-like functionality"
    parameters = {
        "pattern": ToolParameter(
            name="pattern",
            type="string",
            required=True,
            description="Text pattern to search for",
        ),
        "path": ToolParameter(
            name="path",
            type="string",
            required=True,
            description="File or directory path to search in",
        ),
        "case_sensitive": ToolParameter(
            name="case_sensitive",
            type="bool",
            required=False,
            description="Case sensitive search (default: False)",
            default=False,
        ),
    }

    def execute(
        self, pattern: str, path: str, case_sensitive: bool = False
    ) -> ToolResult:
        """
        Search for pattern in files

        Args:
            pattern: Pattern to search for
            path: Path to search in
            case_sensitive: Case sensitivity flag

        Returns:
            ToolResult with matching lines
        """
        try:
            import re
            from pathlib import Path

            search_path = Path(path).resolve()

            if not search_path.exists():
                return ToolResult(success=False, error=f"Path not found: {path}")

            # Compile regex pattern
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                return ToolResult(
                    success=False, error=f"Invalid regex pattern: {str(e)}"
                )

            matches = []

            # Search in files
            if search_path.is_file():
                files_to_search = [search_path]
            else:
                # Search all text files in directory
                files_to_search = [
                    f
                    for f in search_path.rglob("*")
                    if f.is_file()
                    and f.suffix
                    in [
                        ".py",
                        ".js",
                        ".jsx",
                        ".ts",
                        ".tsx",
                        ".txt",
                        ".md",
                        ".json",
                        ".xml",
                        ".html",
                        ".css",
                    ]
                ]

            for file_path in files_to_search:
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                matches.append(
                                    {
                                        "file": str(
                                            file_path.relative_to(search_path.parent)
                                        ),
                                        "line_number": line_num,
                                        "line": line.strip(),
                                    }
                                )

                                # Limit matches
                                if len(matches) >= 100:
                                    break
                except Exception:
                    continue

                if len(matches) >= 100:
                    break

            # Format output
            if matches:
                output_lines = [
                    f"Found {len(matches)} matches for pattern: {pattern}\n"
                ]
                for match in matches[:50]:  # Show first 50
                    output_lines.append(
                        f"{match['file']}:{match['line_number']}: {match['line']}"
                    )

                if len(matches) > 50:
                    output_lines.append(f"\n... and {len(matches) - 50} more matches")

                output_text = "\n".join(output_lines)
            else:
                output_text = f"No matches found for pattern: {pattern}"

            return ToolResult(
                success=True,
                output=output_text,
                metadata={
                    "pattern": pattern,
                    "path": str(search_path),
                    "match_count": len(matches),
                    "case_sensitive": case_sensitive,
                    "matches": matches[:50],  # Return first 50 in metadata
                },
            )

        except Exception as e:
            logger.error(f"Grep search failed: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Search failed: {str(e)}")
