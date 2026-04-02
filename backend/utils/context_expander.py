"""Expand search results with cross-file dependency context.

When search results include chunks from a code file, look up the dependency
graph and include related files (importers/importees) to give the LLM richer
cross-file context.
"""

import json
import logging
from typing import Dict, List, Optional

from backend.models import Document, Folder, db

logger = logging.getLogger(__name__)

MAX_RELATED_FILES = 3
MAX_CHUNKS_PER_RELATED = 2


def expand_with_dependencies(
    results: List[Dict],
    max_related: int = MAX_RELATED_FILES,
    max_chunks: int = MAX_CHUNKS_PER_RELATED,
) -> List[Dict]:
    """Add related-file context to search results based on dependency graph.

    Looks up the dependency graph from the folder's repo_metadata and includes
    top chunks from files that import or are imported by the result files.
    """
    if not results:
        return results

    # Collect unique file paths and folder IDs from results
    seen_paths = set()
    folder_ids = set()
    for r in results:
        meta = r.get("metadata", {})
        path = meta.get("file_path") or meta.get("source_filename")
        if path:
            seen_paths.add(path)
        fid = meta.get("folder_id")
        if fid:
            try:
                folder_ids.add(int(fid))
            except (ValueError, TypeError):
                pass

    if not folder_ids:
        return results

    # Load dependency graphs from relevant folders
    dep_graph = {}
    reverse_graph = {}

    for fid in folder_ids:
        folder = db.session.get(Folder, fid)
        if not folder or not folder.repo_metadata:
            continue
        try:
            meta = json.loads(folder.repo_metadata)
            graph = meta.get("dependency_graph", {})
            dep_graph.update(graph)
            for src, targets in graph.items():
                for t in targets:
                    if t not in reverse_graph:
                        reverse_graph[t] = []
                    reverse_graph[t].append(src)
        except (json.JSONDecodeError, TypeError):
            continue

    if not dep_graph and not reverse_graph:
        return results

    # Find related files
    related_paths = set()
    for path in seen_paths:
        for target in dep_graph.get(path, []):
            if target not in seen_paths:
                related_paths.add(target)
        for src in reverse_graph.get(path, []):
            if src not in seen_paths:
                related_paths.add(src)

    if not related_paths:
        return results

    # Fetch content from related files and add as context
    related_paths = list(related_paths)[:max_related]
    expanded = list(results)

    for path in related_paths:
        doc = Document.query.filter(Document.path == path).first()
        if not doc or not doc.content:
            continue

        lines = doc.content.splitlines()[:60]
        snippet = "\n".join(lines)

        expanded.append({
            "text": snippet,
            "score": 0.0,
            "metadata": {
                "source_filename": doc.filename,
                "file_path": doc.path,
                "document_id": str(doc.id),
                "context_type": "related_dependency",
                "language": json.loads(doc.file_metadata).get("language") if doc.file_metadata else None,
            },
        })

    return expanded
