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
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

_MAX_TEXT = 1200
# Characters of passages one read_document_section call returns (inside its
# observation_chars with the header).
SECTION_PAGE_CHARS = 5200

# RAPTOR writes corpus-level summaries into the same table as the documents they
# were built from (backend/services/raptor_service.py). They are retrievable
# passages, not documents, and their source_filename is a synthetic
# "[corpus summary L<n>#<i>]", so listing them invents documents the user never
# added. Either marker identifies one.
_NOT_A_SUMMARY = (
    "metadata_->>'content_type' IS DISTINCT FROM 'raptor_summary' "
    "AND metadata_->>'parsed_by' IS DISTINCT FROM 'raptor'"
)


def _table() -> Tuple[Optional[str], Optional[str]]:
    """Return (qualified_table, error)."""
    try:
        from backend.services.indexing_service import locate_vector_table
        # The table search reads, or the reason there is none to read. These
        # tools must show what search_knowledge_base can retrieve, so a table
        # that is missing, or one of several that cannot be told apart while the
        # embedding model is unreachable, is reported and not guessed at.
        found = locate_vector_table(None)
        if found.table:
            return f"data_{found.table}", None
        if found.reason == "not_pgvector":
            return None, "These tools require the pgvector backend."
        return None, found.detail
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


# The outline's label for passages that carry neither a heading nor a page, and
# the heading_path value read_document_section accepts for exactly those rows.
NO_SECTION = "(no section)"


def _section_label(heading: Optional[str], page: Optional[str]) -> str:
    """How the outline names a passage's place; read_document_section accepts it back."""
    return heading or (f"page {page}" if page else NO_SECTION)


def _meta(raw) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


# Which file a passage came from. source_filename alone does not say: two
# uploads in different folders or projects can share a name. Every passage
# carries the stored document id 'doc_<n>_<hash>', where <n> is the file's row in
# the documents table, the same for all of a file's pages and sections. Passages
# without such an id (nothing a file upload produces) share the empty key.
_DOC_KEY = "coalesce(substring(metadata_->>'document_id' from '^doc_([0-9]+)_'), '')"


def _contains(text: str) -> str:
    """An ILIKE pattern that matches ``text`` anywhere, its own '%', '_' and
    backslash taken literally. The clause using it must say ESCAPE '\\'."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _shown_path(file_path: Optional[str]) -> str:
    """A stored file path in a form that tells two same-named files apart without
    printing the machine's directory layout: relative to the uploads folder when
    the file is in it, else its folder and name."""
    if not file_path:
        return ""
    path = os.path.normpath(str(file_path))
    try:
        from backend.config import UPLOAD_DIR
        root = os.path.normpath(str(UPLOAD_DIR))
        if path.startswith(root + os.sep):
            return path[len(root) + 1:]
    except Exception:
        pass
    return os.sep.join(path.split(os.sep)[-2:])


def _document_key(document_id: Any) -> Tuple[Optional[str], Optional[str]]:
    """(key, error) for a document_id argument: the number list_documents prints,
    or the stored 'doc_<n>_<hash>' form of it. (None, None) when none was given."""
    if document_id is None or str(document_id).strip() == "":
        return None, None
    match = re.fullmatch(r"(?:doc_)?(\d+)(?:_\w*)?", str(document_id).strip())
    if not match:
        return None, f"document_id must be the number list_documents prints, e.g. 12, not '{document_id}'."
    return str(int(match.group(1))), None


def _document_line(key: str, path: Optional[str], passages: int) -> str:
    shown = _shown_path(path)
    label = f"document_id {key}" if key else "no document_id (cannot be chosen on its own)"
    return f"  {label}" + (f" — {shown}" if shown else "") + f" ({passages} passage(s))"


def _choose_document(table: str, source_filename: str, document_id: Any):
    """Settle which indexed file a filename (and optional document_id) means.

    Returns (key, shown path, early result). The early result is set when the
    call cannot go on: a bad document_id, a name that is not indexed, a
    document_id the name does not have, or a name several files share with no
    document_id to choose between them. Otherwise key is the document key to
    filter on, or None when the name belongs to one file and needs no filter.
    """
    key, problem = _document_key(document_id)
    if problem:
        return None, None, ToolResult(success=False, error=problem)
    documents, qerr = _query(
        f"""SELECT {_DOC_KEY} AS doc, max(metadata_->>'file_path'), count(*)
            FROM "{table}"
            WHERE metadata_->>'source_filename' = %s
            GROUP BY 1 ORDER BY 3 DESC, 1""",
        (source_filename,),
    )
    if qerr:
        return None, None, ToolResult(success=False, error=f"Query failed: {qerr}")
    if not documents:
        return None, None, ToolResult(
            success=True,
            output=f"No indexed content for '{source_filename}'. Use list_documents to see available names.",
        )
    listed = "\n".join(_document_line(doc, path, count) for doc, path, count in documents)
    found = {"documents": [{"document_id": doc, "path": _shown_path(path), "passages": count}
                           for doc, path, count in documents]}
    if key is not None:
        for doc, path, _count in documents:
            if doc == key:
                return key, _shown_path(path), None
        return None, None, ToolResult(
            success=True,
            output=f"'{source_filename}' has no document_id {key}. Indexed under that name:\n{listed}",
            metadata=found,
        )
    if len(documents) > 1:
        return None, None, ToolResult(
            success=True,
            output=(f"'{source_filename}' is the name of {len(documents)} indexed files, so it does "
                    f"not say which one is meant. Call again with document_id:\n{listed}"),
            metadata={"ambiguous": True, **found},
        )
    return None, None, None


class ListDocumentsTool(BaseTool):
    """Enumerate the documents present in the knowledge base."""

    name = "list_documents"
    read_only = True
    observation_chars = 4000  # one line per document; 500 held about six
    description = (
        "List the documents in the local knowledge base, most passages first: one line per file with "
        "its passage count, its section count when above one, and the parser that read it, under a "
        "header giving how many documents match. Use it to see what is indexed, or to get the exact "
        "filename get_document_outline and read_document_section take. When several files share a "
        "name (different folders or projects) each gets its own line ending in 'document_id <n>' and "
        "its folder; pass that number to those tools to choose one. Covers every project on the "
        "default shared index; an install set to per-project indexes lists only documents that "
        "belong to no project. "
        "name_contains keeps filenames containing that text; limit and offset page through the list, "
        "and when more remain the reply ends with the offset for the next page. To find content by topic use "
        "search_knowledge_base; corpus summaries are in summarize_corpus, not here."
    )
    parameters = {
        "name_contains": ToolParameter(
            name="name_contains", type="string", required=False,
            description="Only list documents whose filename contains this text, case-insensitive and taken literally (no wildcards), e.g. 'manual' or '.pdf'.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=40, minimum=1, maximum=200,
            description="Documents per page, 1-200 (default 40).",
        ),
        "offset": ToolParameter(
            name="offset", type="int", required=False, default=0, minimum=0,
            description="How many documents to skip, for paging (default 0).",
        ),
    }

    def execute(self, name_contains: str = None, limit: int = None, offset: int = None) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)
        limit = max(1, min(int(limit or 40), 200))
        offset = max(0, int(offset or 0))

        conditions, filter_params = [_NOT_A_SUMMARY], []
        if name_contains:
            conditions.append("metadata_->>'source_filename' ILIKE %s ESCAPE '\\'")
            filter_params.append(_contains(name_contains))
        where = "WHERE " + " AND ".join(conditions)
        params = filter_params + [limit, offset]

        # One row per file, not per filename: the document key keeps two files
        # of the same name apart. same_name counts the files sharing a name
        # across the whole listing, so a line is marked even when its namesake
        # is on another page.
        rows, qerr = _query(
            f"""SELECT src, chunks, sections, parsed_by, doc, path,
                       count(*) OVER (PARTITION BY src) AS same_name
                FROM (SELECT metadata_->>'source_filename' AS src,
                             count(*) AS chunks,
                             count(DISTINCT metadata_->>'heading_path') AS sections,
                             max(metadata_->>'parsed_by') AS parsed_by,
                             {_DOC_KEY} AS doc,
                             max(metadata_->>'file_path') AS path
                      FROM "{table}" {where}
                      GROUP BY 1, 5) documents
                ORDER BY chunks DESC, src, doc LIMIT %s OFFSET %s""",
            tuple(params),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")

        # The total counts what the filter matches, so paging hints add up.
        total_rows, count_err = _query(
            f"SELECT count(DISTINCT (metadata_->>'source_filename', {_DOC_KEY})) FROM \"{table}\" {where}",
            tuple(filter_params),
        )
        # A failed count used to fall back to the page length, so "how big is the
        # knowledge base" was answered with "as many as fit on this page". The
        # page is still useful; the total is reported as unknown, not invented.
        if count_err:
            total = None
            logger.warning("list_documents: corpus count failed: %s", count_err)
        else:
            total = total_rows[0][0] if total_rows else 0

        if not rows:
            if offset and total:
                return ToolResult(success=True, output=f"No documents at offset {offset}; {total} match in all.")
            if name_contains:
                return ToolResult(success=True, output=f"No indexed document's filename contains '{name_contains}'.")
            return ToolResult(success=True, output="The knowledge base has no documents yet.")

        head = (f"KNOWLEDGE BASE — {total} document(s)" + (" match" if name_contains else " indexed")
                if total is not None
                else f"KNOWLEDGE BASE — document count unavailable ({count_err})")
        lines = [head
                 + (f", filtered by '{name_contains}'" if name_contains else "")
                 + f" · showing {offset + 1}-{offset + len(rows)}"]
        shared = []
        for src, chunks, sections, parsed_by, doc, path, same_name in rows:
            extra = f", {sections} sections" if sections and sections > 1 else ""
            line = f"  {src or '(unknown)'} — {chunks} passages{extra} [{parsed_by or '?'}]"
            if same_name and same_name > 1:
                shown = _shown_path(path)
                line += (f" · document_id {doc}" if doc else " · no document_id") + (f" · {shown}" if shown else "")
                if src not in shared:
                    shared.append(src)
            lines.append(line)
        if shared:
            lines.append(
                "\nA name shown with a document_id belongs to more than one file: pass that "
                "document_id to get_document_outline or read_document_section to choose one."
            )
        if total is not None and offset + len(rows) < total:
            lines.append(f"\n({total - offset - len(rows)} more — call again with offset={offset + len(rows)})")
        elif total is None and len(rows) == limit:
            lines.append(f"\n(there may be more — call again with offset={offset + len(rows)})")

        return ToolResult(success=True, output="\n".join(lines),
                          metadata={"total": total, "returned": len(rows), "offset": offset,
                                    "shared_names": shared})


class DocumentOutlineTool(BaseTool):
    """Show the section structure of one indexed document."""

    name = "get_document_outline"
    read_only = True
    observation_chars = 3000  # a heading per line
    description = (
        "Show the structure of one indexed document: its sections (heading paths such as 'Setup > "
        "Install') and pages in the order they appear, each with a passage count, under a header with "
        "the document's total. Use it after list_documents and before read_document_section, which "
        "takes a heading path, a page label or '(no section)' exactly as this outline prints them "
        "('(no section)' covers passages with neither a heading nor a page, as in a plain .txt file). "
        "An unknown filename returns a short notice, not an error. When several indexed files "
        "share the filename, the reply lists them with their document_id and no outline: call "
        "again with document_id. To find a topic across documents use search_knowledge_base."
    )
    parameters = {
        "source_filename": ToolParameter(
            name="source_filename", type="string", required=True,
            description="Exact filename as list_documents prints it, e.g. 'handbook.pdf' (case-sensitive).",
        ),
        "document_id": ToolParameter(
            name="document_id", type="int", required=False, minimum=1,
            description="Only needed when several files share the filename: the number list_documents prints after 'document_id' on that file's line.",
        ),
    }

    def execute(self, source_filename: str, document_id: Any = None) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)
        key, shown, early = _choose_document(table, source_filename, document_id)
        if early:
            return early

        clauses, params = ["metadata_->>'source_filename' = %s"], [source_filename]
        if key is not None:
            clauses.append(f"{_DOC_KEY} = %s")
            params.append(key)
        rows, qerr = _query(
            f"""SELECT coalesce(metadata_->>'heading_path', ''),
                       coalesce(metadata_->>'page_label', ''),
                       count(*)
                FROM "{table}"
                WHERE {" AND ".join(clauses)}
                GROUP BY 1, 2
                ORDER BY min(id)""",
            tuple(params),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")
        if not rows:
            return ToolResult(
                success=True,
                output=f"No indexed content for '{source_filename}'. Use list_documents to see available names.",
            )

        name = source_filename
        if key is not None:
            name += f" · document_id {key}" + (f" · {shown}" if shown else "")
        lines = [f"OUTLINE — {name} ({sum(r[2] for r in rows)} passages)"]
        for heading, page, count in rows:
            label = _section_label(heading, page)
            loc = f" p.{page}" if page and heading else ""
            lines.append(f"  {label}{loc} — {count} passage(s)")
        return ToolResult(success=True, output="\n".join(lines),
                          metadata={"sections": len(rows)})


class ReadDocumentSectionTool(BaseTool):
    """Read the indexed passages of a specific section or page."""

    name = "read_document_section"
    read_only = True
    # A page of passages plus the header; SECTION_PAGE_CHARS keeps the page
    # inside it, so the offset the reply gives starts at the first passage
    # the model did not see.
    observation_chars = 6000
    description = (
        "Read the stored text of an indexed document, without searching, in document order. Pass "
        "source_filename plus heading_path or page_label from get_document_outline to read one "
        "section or page; giving both narrows to that section on that page. heading_path "
        "'(no section)' reads the passages the outline lists under that label (no heading and no "
        "page, e.g. a plain .txt file); any other heading_path matches every section whose path "
        "contains the text (case-insensitive), so a short value can return several sections. With "
        "neither, it reads the whole document from the start. Returns the passages that fit in one "
        "page (up to 25), each cut at 1,200 characters and labelled with its section or page as the "
        "outline names it; the header gives the total and the offset for the next call. When several indexed "
        "files share the filename, the reply lists them with their document_id and no text: call "
        "again with document_id. To locate a topic first "
        "use search_knowledge_base; for a file that is not indexed, process_file."
    )
    parameters = {
        "source_filename": ToolParameter(
            name="source_filename", type="string", required=True,
            description="Exact filename as list_documents prints it (case-sensitive).",
        ),
        "document_id": ToolParameter(
            name="document_id", type="int", required=False, minimum=1,
            description="Only needed when several files share the filename: the number list_documents prints after 'document_id' on that file's line.",
        ),
        "heading_path": ToolParameter(
            name="heading_path", type="string", required=False,
            description="Section path as get_document_outline shows it, e.g. 'Installation > Requirements'; any section whose path contains this text, taken literally, matches. '(no section)' reads the passages that have neither a heading nor a page. Leave out together with page_label to read the whole document.",
        ),
        "page_label": ToolParameter(
            name="page_label", type="string", required=False,
            description="Page label as get_document_outline shows it, e.g. '12' for the line 'page 12' (exact match).",
        ),
        "offset": ToolParameter(
            name="offset", type="int", required=False, default=0, minimum=0,
            description="Passages to skip, to read past the first 25 (default 0).",
        ),
    }

    def execute(self, source_filename: str, heading_path: str = None, page_label: str = None,
                offset: int = None, document_id: Any = None) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)
        key, shown, early = _choose_document(table, source_filename, document_id)
        if early:
            return early
        heading_path = (heading_path or "").strip()
        page_label = str(page_label).strip() if page_label is not None else ""
        no_section = heading_path.lower() == NO_SECTION

        clauses = ["metadata_->>'source_filename' = %s"]
        params: List[Any] = [source_filename]
        if key is not None:
            clauses.append(f"{_DOC_KEY} = %s")
            params.append(key)
        if no_section:
            # The outline's '(no section)' group: ILIKE never matches a NULL
            # heading, so these rows are selected by their missing metadata.
            clauses.append("coalesce(metadata_->>'heading_path', '') = ''")
            if not page_label:
                clauses.append("coalesce(metadata_->>'page_label', '') = ''")
        elif heading_path:
            clauses.append("metadata_->>'heading_path' ILIKE %s ESCAPE '\\'")
            params.append(_contains(heading_path))
        if page_label:
            clauses.append("metadata_->>'page_label' = %s")
            params.append(page_label)

        offset = max(0, int(offset or 0))
        where = " AND ".join(clauses)
        rows, qerr = _query(
            f'SELECT text, metadata_ FROM "{table}" WHERE {where} ORDER BY id LIMIT 25 OFFSET %s',
            tuple(params) + (offset,),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")
        total_rows, _ = _query(f'SELECT count(*) FROM "{table}" WHERE {where}', tuple(params))
        total = total_rows[0][0] if total_rows else None
        if not rows:
            if offset and total:
                return ToolResult(success=True, output=f"No passages at offset {offset}; {total} match in all.")
            if not heading_path and not page_label:
                return ToolResult(
                    success=True,
                    output=f"No indexed content for '{source_filename}'. Use list_documents to see available names.",
                )
            return ToolResult(success=True, output="No passages match that section or page.")

        head = f"{source_filename}"
        if key is not None:
            head += f" · document_id {key}" + (f" · {shown}" if shown else "")
        if no_section:
            head += f" · {NO_SECTION}"
        elif heading_path:
            head += f" · section ~ {heading_path}"
        if page_label:
            head += f" · page {page_label}"
        if not heading_path and not page_label:
            head += " · whole document"
        passages, used = [], 0
        for i, (text, meta) in enumerate(rows, 1):
            m = _meta(meta)
            # Chunks are stored with a contextual prefix for embedding; show the raw text.
            body = (m.get("original_text") or text or "").strip()
            if len(body) > _MAX_TEXT:
                body = body[:_MAX_TEXT].rstrip() + "…"
            label = _section_label(m.get("heading_path"), m.get("page_label"))
            block = f"\n[{offset + i}] {label}\n{body}"
            if passages and used + len(block) > SECTION_PAGE_CHARS:
                break
            passages.append(block)
            used += len(block)
        shown_to = offset + len(passages)
        span = f"passages {offset + 1}-{shown_to}" + (f" of {total}" if total is not None else "")
        lines = [f"{head} — {span}"]
        if total is not None and shown_to < total:
            lines.append(f"(more: call again with offset={shown_to})")
        lines.extend(passages)
        return ToolResult(success=True, output="\n".join(lines),
                          metadata={"passages": len(passages), "next_offset": shown_to
                                    if total is not None and shown_to < total else None})


class CorpusSummaryTool(BaseTool):
    """Retrieve corpus-level summaries produced by the RAPTOR pass."""

    name = "summarize_corpus"
    read_only = True
    observation_chars = 3000  # the summaries are the answer
    description = (
        "Return precomputed summaries of the knowledge base, written by the local LLM when a summary "
        "build last ran (Settings > Build summaries). Until one has run the tool says none exist, and "
        "documents added since are not reflected. One summary per cluster of related passages, "
        "largest first, each with its size, the files it draws on (listed up to 160 characters) and up to 1,200 characters of "
        "text. Use it for broad questions (main themes, what a collection is about) before "
        "searching; for facts and quotable text use search_knowledge_base."
    )
    parameters = {
        "level": ToolParameter(
            name="level", type="int", required=False, default=1, minimum=1,
            description="1 (default): summaries of groups of related passages. 2: broader summaries of the level-1 summaries, whose sources show as summary ids.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=8, minimum=1, maximum=30,
            description="How many summaries to return, largest clusters first, 1-30 (default 8).",
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
