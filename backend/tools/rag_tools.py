import logging
import time
from typing import Any, Dict, List

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.indexing_service import search_with_llamaindex
from backend.utils.backend_http import is_mcp_transport, run_tool_in_backend

logger = logging.getLogger(__name__)

# Per-chunk cap for the rendered block. Chunks are quoted verbatim (never
# LLM-summarized) so the caller can cite them; this only bounds how much of a
# long chunk is shown before an explicit truncation marker.
_CHUNK_CHARS = 600


def _render(query: str, results: List[Dict[str, Any]], trace: Dict[str, Any]) -> str:
    """Render results as a compact, citable text block.

    The provenance banner comes first on purpose: consumers truncate tool output
    (the chat engine at 2000 chars), and a degradation notice that gets cut off
    is worse than none at all.
    """
    legs = "+".join(trace.get("legs") or []) or "none"
    fusion = trace.get("fusion") or "n/a"
    alpha = trace.get("eff_alpha")
    model = trace.get("embedding_model") or "unknown"
    dims = trace.get("embedding_dims")

    head = [f'KNOWLEDGE BASE — {len(results)} result(s) for "{query}"']
    detail = f"retrieval: {legs} ({fusion}"
    if alpha is not None:
        detail += f", alpha={alpha:.2f}"
    detail += f") · embed: {model}"
    if dims:
        detail += f" ({dims}d)"
    rr = trace.get("rerank") or {}
    if rr.get("applied"):
        detail += f" · rerank: {rr.get('model')} on {rr.get('device')}"
    elif rr.get("reason"):
        detail += f" · rerank: OFF ({rr['reason']})"
    if trace.get("mmr_applied"):
        detail += " · MMR"
    if trace.get("filters_applied"):
        detail += f" · filters={trace.get('filters')}"
    head.append(detail)

    # A project-scoped question answered from outside that project is the one
    # result a caller must not mistake for a normal one: the passages may belong
    # to a different client. Say so before the passages, not in a trace nobody
    # reads.
    if trace.get("project_scope") == "global_fallback":
        head.append(
            f"!! OUT OF SCOPE — no matches in project "
            f"{trace.get('fallback_from_project')}; these passages come from "
            f"elsewhere in the index and may belong to another client"
        )
    if trace.get("degraded"):
        head.append(f"!! DEGRADED — {trace.get('degraded_reason')}")
    if trace.get("error"):
        head.append(f"!! ERROR — {trace['error']}")

    if not results:
        head.append("")
        head.append("No matching content. The index may be empty, or the filters too narrow.")
        # Names of projects, clients and the like are never indexed text; a
        # search for one comes back empty here even when the record exists.
        head.append(
            f"If \"{query}\" is the name of something in Guaardvark (a project, client, "
            f"document, video, note, Cast member...), find_records with name=\"{query}\" checks "
            "those directly; find_files checks file names on disk."
        )
        return "\n".join(head)

    body = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata") or {}
        src = meta.get("source_filename") or meta.get("file_path") or "unknown source"
        page = meta.get("page_label")
        loc = f" p.{page}" if page else ""
        # Two numbers with different meanings. When the cross-encoder ran, its
        # relevance score is what the ranking starts from; the retrieval (fusion)
        # score is from the step before and does not follow the order shown.
        score = r.get("score")
        rerank = r.get("rerank_score")
        shown = []
        if isinstance(rerank, (int, float)):
            shown.append(f"rerank {rerank:.3f}")
        if isinstance(score, (int, float)):
            shown.append(f"retrieval {score:.3f}" if shown else f"score {score:.3f}")
        score_s = f" ({' · '.join(shown)})" if shown else ""
        text = (r.get("text") or "").strip()
        if len(text) > _CHUNK_CHARS:
            text = text[:_CHUNK_CHARS].rstrip() + f"… [+{len(r['text']) - _CHUNK_CHARS} chars]"
        body.append(f"\n[{i}] {src}{loc}{score_s}\n{text}")

    return "\n".join(head) + "\n" + "\n".join(body)


class KnowledgeSearchTool(BaseTool):
    """
    Tool for searching the internal knowledge base (RAG).
    Use this to retrieve information about the codebase, architecture, specific repositories,
    or any documents that have been indexed.
    """

    name = "search_knowledge_base"
    read_only = True
    # The passages are the answer: at the 500 default the model read the
    # header and the start of one passage.
    observation_chars = 4000
    description = (
        "Search the user's indexed documents and code repositories (the local knowledge base) by "
        "meaning and keywords. Returns the top passages (the configured number, 3 on a stock install, "
        "up to 50 via top_k), each with its filename, page when known and its scores: 'rerank' (the "
        "reranker's relevance score, which the ranking starts from, shown when it ran) and "
        "'retrieval' (the earlier search score, which does not follow the order shown); passages "
        "usually open with an index label, e.g. 'Document: <file>. Section: <path>.' for documents or "
        "'[python] File: <path>.' for code, before the source text. Once corpus "
        "summaries have been built, results can include LLM-written summaries named "
        "'[corpus summary ...]': cite only real filenames. Results are ranked, not filtered, so an "
        "off-topic question still gets the nearest passages; a header line flags degraded retrieval. "
        "Needs the Guaardvark backend running. For Guaardvark's own source use search_codebase; for "
        "saved facts, search_memory; to read a whole section, read_document_section."
    )
    parameters = {
        "query": ToolParameter(
            name="query",
            type="string",
            description="What to find, as a question or key terms in plain words; matched by meaning and, when keyword search is on, by exact terms too.",
            required=True
        ),
        "top_k": ToolParameter(
            name="top_k",
            type="int",
            minimum=1,
            maximum=50,
            description="How many passages to return, 1-50. Omit for the configured default (3 on a stock install).",
            required=False
        ),
        "filter_type": ToolParameter(
            name="filter_type",
            type="string",
            description=(
                "Keep only passages with this content_type label. Labels are set per passage by a "
                "heuristic, e.g. 'text', 'header', 'table', 'list' or 'code' for files, a language "
                "name such as 'python' for code, and 'repository_summary' or 'repository_map' for "
                "analysed repositories. One label can miss relevant passages; omit it to search "
                "everything."
            ),
            required=False
        ),
        "project_id": ToolParameter(
            name="project_id",
            type="string",
            description="Id of a Guaardvark project, e.g. '12', to search only its documents; find_records lists projects with their ids. Omit to search every document.",
            required=False
        )
    }

    def __init__(self):
        super().__init__()

    def execute(self, query: str, top_k: int = None, filter_type: str = None,
                project_id: str = None) -> ToolResult:
        if is_mcp_transport(self):
            arguments = {"query": query, "top_k": top_k,
                         "filter_type": filter_type, "project_id": project_id}
            return run_tool_in_backend(
                self.name, {key: value for key, value in arguments.items() if value is not None}
            )
        logger.info(f"Executing KnowledgeSearchTool: {query}")
        try:
            # content_type is the type key actually stamped on every indexed node
            # (indexing/chunking), and it is what repository_analysis_service writes
            # for repo summaries. A bare "type" key exists only on those summaries.
            filters = {"content_type": filter_type} if filter_type else None
            payload = search_with_llamaindex(
                query,
                max_chunks=top_k,
                project_id=project_id,
                filters=filters,
                with_trace=True,
            )
            results = payload["results"]
            trace = payload["trace"]

            # An empty result set is a real answer, not a failure -- reporting it as
            # an error made "nothing indexed matches" indistinguishable from "the
            # index is broken", and the trace already carries which one it was.
            return ToolResult(
                success=True,
                output=_render(query, results, trace),
                metadata={"retrieval": trace, "results": results},
            )
        except Exception as e:
            logger.error(f"Error in KnowledgeSearchTool: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to search knowledge base: {str(e)}"
            )


# How long index_document waits for the indexing worker, by default and at
# most. A short text file is indexed in seconds; a long PDF can take minutes,
# and the answer then says it is still indexing. The cap stays under the MCP
# server's 120 s per-call timeout.
INDEX_WAIT_DEFAULT_S = 45
INDEX_WAIT_MAX_S = 90


class IndexDocumentTool(BaseTool):
    """Add one file to the knowledge base and index it."""

    name = "index_document"
    read_only = False
    destructive = False
    description = (
        "Add one file to the user's knowledge base so search_knowledge_base, list_documents and "
        "read_document_section can find it. The file is copied into Guaardvark's uploads as a new "
        "document, the way an upload on the Documents page is (a name already taken there gets a "
        "' (1)' style suffix), and Guaardvark's indexer splits and embeds it on this machine; a file "
        "that already is a document in uploads is indexed again in place. Waits up to wait_seconds "
        "for the indexer, then returns the document id and whether it is indexed, failed (with the "
        "reason) or still indexing, in which case list_documents shows it once done (a long PDF can "
        "take minutes). It reads the files process_file reads: over MCP, files in Guaardvark's "
        "uploads, outputs and install folder, never key or credential files. Refused while indexing "
        "is paused in Settings. To read a file once without adding it use process_file. Needs the "
        "Guaardvark backend and its indexing worker running."
    )
    parameters = {
        "file_path": ToolParameter(
            name="file_path",
            type="string",
            description=(
                "File to add, as process_file takes it: a path relative to the Guaardvark folder "
                "(e.g. 'data/outputs/files/notes.md'), else relative to its uploads folder, or an "
                "absolute path inside the uploads, outputs or install folder."
            ),
            required=True,
        ),
        "project_id": ToolParameter(
            name="project_id",
            type="int",
            minimum=1,
            description="Id of the Guaardvark project to file the document under (find_records lists projects). Omit for none.",
            required=False,
        ),
        "wait_seconds": ToolParameter(
            name="wait_seconds",
            type="int",
            minimum=0,
            maximum=INDEX_WAIT_MAX_S,
            default=INDEX_WAIT_DEFAULT_S,
            description=f"How long to wait for the indexer before answering, 0-{INDEX_WAIT_MAX_S} s (default {INDEX_WAIT_DEFAULT_S}); 0 answers once it is queued.",
            required=False,
        ),
    }

    def execute(self, file_path: str, project_id: int = None, wait_seconds: int = None) -> ToolResult:
        try:
            wait = INDEX_WAIT_DEFAULT_S if wait_seconds is None else max(0, min(int(wait_seconds), INDEX_WAIT_MAX_S))
        except (TypeError, ValueError):
            return ToolResult(success=False, error=f"wait_seconds must be a whole number, 0-{INDEX_WAIT_MAX_S}.")
        if is_mcp_transport(self):
            arguments = {"file_path": file_path, "project_id": project_id, "wait_seconds": wait}
            return run_tool_in_backend(
                self.name, {key: value for key, value in arguments.items() if value is not None},
                read_timeout=wait + 30,
            )

        from backend.tools.agent_tools.file_operation_tools import ProcessFileTool

        reader = ProcessFileTool()
        reader.set_context(dict(getattr(self, "_context", None) or {}))
        path, problem = reader._resolve(file_path)
        if problem:
            return ToolResult(success=False, error=problem)

        from backend.models import db
        try:
            document, copied = _queue_for_index(path, project_id)
        except ValueError as e:
            db.session.rollback()
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            db.session.rollback()
            logger.error(f"index_document failed for {file_path}: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Could not add {path.name}: {e}")

        deadline = time.monotonic() + wait
        while document.index_status not in ("INDEXED", "ERROR") and time.monotonic() < deadline:
            time.sleep(2)
            db.session.refresh(document)

        where = f" (copied to uploads as {document.path})" if copied else ""
        metadata = {"document_id": document.id, "filename": document.filename,
                    "path": document.path, "status": document.index_status, "copied": copied}
        if document.index_status == "ERROR":
            return ToolResult(
                success=False,
                error=f"{document.filename} is document {document.id}{where} but failed to index: "
                      f"{document.error_message or 'no reason recorded'}",
                metadata=metadata,
            )
        if document.index_status == "INDEXED":
            output = (f"Indexed {document.filename} as document {document.id}{where}; "
                      "search_knowledge_base and list_documents find it now.")
        else:
            output = (f"{document.filename} is document {document.id}{where} and is still indexing "
                      f"after {wait} s; list_documents shows it once it is done.")
        return ToolResult(success=True, output=output, metadata=metadata)


def _queue_for_index(path, project_id):
    """(document, copied): the document for this file, with indexing queued.

    A file already registered as a document in uploads is queued again in
    place, unless it is indexing now; any other file goes in through the
    Documents page's upload path, which queues it (a file in uploads that is
    not yet a document is registered where it is). Raises ValueError when
    indexing is paused or cannot be queued.
    """
    import io
    from pathlib import Path

    from werkzeug.datastructures import FileStorage
    from werkzeug.utils import secure_filename

    from backend import config
    from backend.api.indexing_api import _dispatch_document, _get_indexing_paused_setting
    from backend.celery_tasks_isolated import index_document_task
    from backend.models import Document as DBDocument, db
    from backend.services.indexing_service import update_document_status
    from backend.services.unified_upload_service import UnifiedUploadService
    from backend.utils.filename_resolver import resolve_filename
    from backend.utils.unified_progress_system import get_unified_progress

    if _get_indexing_paused_setting():
        raise ValueError("Indexing is paused in Settings, so nothing was added. Resume indexing and call again.")

    uploads = Path(config.UPLOAD_DIR).resolve()
    if path.is_relative_to(uploads):
        document = DBDocument.query.filter_by(path=path.relative_to(uploads).as_posix()).first()
        if document is not None:
            if document.index_status != "INDEXING":
                update_document_status(document.id, "INDEXING")
                refused = _dispatch_document(get_unified_progress(), index_document_task, document)
                if refused is not None:
                    update_document_status(document.id, "ERROR", str(refused))
                    raise ValueError(f"{document.filename} is document {document.id}, but indexing could not be queued: {refused}")
                db.session.refresh(document)
            return document, False

    # The upload writes uploads/<name> without looking at the disk, so a file
    # already there under that name would be overwritten. A file in uploads
    # that is not yet a document lands on itself; it is read first so the
    # write cannot truncate what it is reading.
    chosen = resolve_filename(None, secure_filename(path.name), db.session, DBDocument)
    target = (uploads / chosen).resolve()
    if target.exists() and target != path:
        raise ValueError(
            f"Guaardvark's uploads already hold a file named {chosen} that is not a document, so "
            f"nothing was copied over it. Rename {path.name}, or add that file with file_path '{chosen}'."
        )
    in_place = target == path
    stream = io.BytesIO(path.read_bytes()) if in_place else path.open("rb")
    with stream:
        document, _job_id = UnifiedUploadService.upload_file(
            FileStorage(stream=stream, filename=path.name), project_id=project_id,
            onto_existing_file=in_place,
        )
    return document, not in_place
