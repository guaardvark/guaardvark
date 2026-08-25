"""Navigation tools for the knowledge base.

search_knowledge_base answers "find me passages about X". That is a lookup
primitive, and lookup alone is not navigation: there was no way to ask what the
corpus contains, what a document is made of, or what sits next to a passage. The
code side has had that surface for a while (get_repository_map -> list_code_files
-> read_code -> read_ast_node); documents had one search box.

These tools read the index directly from Postgres rather than the Document table,
for two reasons: the MCP server runs as a separate process with no Flask
application context, and the index is the honest answer to "what can actually be
retrieved" -- a registry row for a file that failed to chunk is not navigable.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

_MAX_TEXT = 1200


def _table() -> Tuple[Optional[str], Optional[str]]:
    """Return (qualified_table, error)."""
    try:
        from backend.services.indexing_service import (
            resolve_existing_vector_table, _vector_backend,
        )
        if _vector_backend() != "pgvector":
            return None, "These tools require the pgvector backend."
        # Discovery rather than derivation: deriving the name needs the embedding
        # model's dimension, and the MCP server is a bare subprocess with no Flask
        # context and no initialised index, so that probe returns nothing. These
        # tools are read-only and the dimension is already in the table name.
        t = resolve_existing_vector_table(None)
        if not t:
            return None, ("No knowledge index found. Index some documents first, "
                          "or check that the pgvector table exists.")
        return f"data_{t}", None
    except Exception as e:
        return None, f"Index unavailable: {e}"


def _query(sql: str, params: tuple) -> Tuple[Optional[List[tuple]], Optional[str]]:
    try:
        from backend.services.indexing_service import _pg_connect
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall(), None
        finally:
            conn.close()
    except Exception as e:
        logger.error("knowledge_tools query failed: %s", e)
        return None, str(e)[:200]


def _meta(raw) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


class ListDocumentsTool(BaseTool):
    """Enumerate the documents present in the knowledge base."""

    name = "list_documents"
    description = (
        "List the documents currently in the knowledge base, with how many passages each "
        "contributes. Use this to find out what the knowledge base actually contains before "
        "searching it. Supports paging and a name filter."
    )
    parameters = {
        "name_contains": ToolParameter(
            name="name_contains", type="string", required=False,
            description="Only list documents whose filename contains this text.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False,
            description="How many documents to return (default 40, max 200).",
        ),
        "offset": ToolParameter(
            name="offset", type="int", required=False,
            description="Skip this many documents — use to page through a long list.",
        ),
    }

    def execute(self, name_contains: str = None, limit: int = None, offset: int = None) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)
        limit = max(1, min(int(limit or 40), 200))
        offset = max(0, int(offset or 0))

        where, params = "", []
        if name_contains:
            where = "WHERE metadata_->>'source_filename' ILIKE %s"
            params.append(f"%{name_contains}%")
        params.extend([limit, offset])

        rows, qerr = _query(
            f"""SELECT metadata_->>'source_filename' AS src,
                       count(*) AS chunks,
                       count(DISTINCT metadata_->>'heading_path') AS sections,
                       max(metadata_->>'parsed_by') AS parsed_by
                FROM "{table}" {where}
                GROUP BY 1 ORDER BY 2 DESC LIMIT %s OFFSET %s""",
            tuple(params),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")

        total_rows, _ = _query(
            f"SELECT count(DISTINCT metadata_->>'source_filename') FROM \"{table}\"", ()
        )
        total = total_rows[0][0] if total_rows else len(rows)

        if not rows:
            return ToolResult(success=True, output="No documents match. The knowledge base may be empty.")

        lines = [f"KNOWLEDGE BASE — {total} document(s) indexed"
                 + (f", filtered by '{name_contains}'" if name_contains else "")
                 + f" · showing {offset + 1}-{offset + len(rows)}"]
        for src, chunks, sections, parsed_by in rows:
            extra = f", {sections} sections" if sections and sections > 1 else ""
            lines.append(f"  {src or '(unknown)'} — {chunks} passages{extra} [{parsed_by or '?'}]")
        if offset + len(rows) < total:
            lines.append(f"\n({total - offset - len(rows)} more — call again with offset={offset + len(rows)})")

        return ToolResult(success=True, output="\n".join(lines),
                          metadata={"total": total, "returned": len(rows), "offset": offset})


class DocumentOutlineTool(BaseTool):
    """Show the section structure of one indexed document."""

    name = "get_document_outline"
    description = (
        "Show the internal structure of one indexed document — its sections or pages, in order, "
        "with passage counts. Use this after list_documents to see what is inside a document "
        "before retrieving from it."
    )
    parameters = {
        "source_filename": ToolParameter(
            name="source_filename", type="string", required=True,
            description="The document filename, as shown by list_documents.",
        ),
    }

    def execute(self, source_filename: str) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)

        rows, qerr = _query(
            f"""SELECT coalesce(metadata_->>'heading_path', ''),
                       coalesce(metadata_->>'page_label', ''),
                       count(*)
                FROM "{table}"
                WHERE metadata_->>'source_filename' = %s
                GROUP BY 1, 2
                ORDER BY 2, 1""",
            (source_filename,),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")
        if not rows:
            return ToolResult(
                success=True,
                output=f"No indexed content for '{source_filename}'. Use list_documents to see available names.",
            )

        lines = [f"OUTLINE — {source_filename} ({sum(r[2] for r in rows)} passages)"]
        for heading, page, count in rows:
            label = heading or (f"page {page}" if page else "(no section)")
            loc = f" p.{page}" if page and heading else ""
            lines.append(f"  {label}{loc} — {count} passage(s)")
        return ToolResult(success=True, output="\n".join(lines),
                          metadata={"sections": len(rows)})


class ReadDocumentSectionTool(BaseTool):
    """Read the indexed passages of a specific section or page."""

    name = "read_document_section"
    description = (
        "Read the actual indexed text of one section or page of a document, without searching. "
        "Use after get_document_outline when you know which part you need."
    )
    parameters = {
        "source_filename": ToolParameter(
            name="source_filename", type="string", required=True,
            description="The document filename.",
        ),
        "heading_path": ToolParameter(
            name="heading_path", type="string", required=False,
            description="Section breadcrumb from get_document_outline (exact or partial match).",
        ),
        "page_label": ToolParameter(
            name="page_label", type="string", required=False,
            description="Page number, for paginated documents such as PDFs.",
        ),
    }

    def execute(self, source_filename: str, heading_path: str = None, page_label: str = None) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)
        if not heading_path and not page_label:
            return ToolResult(success=False,
                              error="Provide heading_path or page_label (see get_document_outline).")

        clauses = ["metadata_->>'source_filename' = %s"]
        params: List[Any] = [source_filename]
        if heading_path:
            clauses.append("metadata_->>'heading_path' ILIKE %s")
            params.append(f"%{heading_path}%")
        if page_label:
            clauses.append("metadata_->>'page_label' = %s")
            params.append(str(page_label))

        rows, qerr = _query(
            f'SELECT text, metadata_ FROM "{table}" WHERE {" AND ".join(clauses)} LIMIT 25',
            tuple(params),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")
        if not rows:
            return ToolResult(success=True, output="No passages match that section or page.")

        head = f"{source_filename}"
        if heading_path:
            head += f" · section ~ {heading_path}"
        if page_label:
            head += f" · page {page_label}"
        lines = [f"{head} — {len(rows)} passage(s)"]
        for i, (text, meta) in enumerate(rows, 1):
            m = _meta(meta)
            # Chunks are stored with a contextual prefix for embedding; show the raw text.
            body = (m.get("original_text") or text or "").strip()
            if len(body) > _MAX_TEXT:
                body = body[:_MAX_TEXT].rstrip() + "…"
            lines.append(f"\n[{i}] {m.get('heading_path') or m.get('page_label') or ''}\n{body}")
        return ToolResult(success=True, output="\n".join(lines), metadata={"passages": len(rows)})


class CorpusSummaryTool(BaseTool):
    """Retrieve corpus-level summaries produced by the RAPTOR pass."""

    name = "summarize_corpus"
    description = (
        "Get high-level summaries of what the whole knowledge base covers, rather than individual "
        "passages. Use for broad questions — overall themes, what a collection is about, how topics "
        "relate — which passage search answers poorly."
    )
    parameters = {
        "level": ToolParameter(
            name="level", type="int", required=False,
            description="Summary level: 1 is closer to the source, 2+ is broader. Default 1.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False,
            description="How many summaries to return (default 8, max 30).",
        ),
    }

    def execute(self, level: int = None, limit: int = None) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)
        level = int(level or 1)
        limit = max(1, min(int(limit or 8), 30))

        rows, qerr = _query(
            f"""SELECT text, metadata_ FROM "{table}"
                WHERE metadata_->>'content_type' = 'raptor_summary'
                  AND metadata_->>'raptor_level' = %s
                ORDER BY (metadata_->>'cluster_size')::int DESC LIMIT %s""",
            (str(level), limit),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")
        if not rows:
            return ToolResult(
                success=True,
                output=(f"No level-{level} corpus summaries exist yet. They are produced by the "
                        "RAPTOR build, which is an explicit operation, not part of indexing."),
            )

        lines = [f"CORPUS SUMMARIES — level {level}, {len(rows)} cluster(s)"]
        for i, (text, meta) in enumerate(rows, 1):
            m = _meta(meta)
            covers = m.get("covers_sources") or ""
            body = (text or "").strip()
            if len(body) > _MAX_TEXT:
                body = body[:_MAX_TEXT].rstrip() + "…"
            lines.append(f"\n[{i}] {m.get('cluster_size', '?')} passages"
                         + (f" from: {covers[:160]}" if covers else "") + f"\n{body}")
        return ToolResult(success=True, output="\n".join(lines), metadata={"summaries": len(rows)})


KNOWLEDGE_NAV_TOOLS = [
    ListDocumentsTool,
    DocumentOutlineTool,
    ReadDocumentSectionTool,
    CorpusSummaryTool,
]
