import logging
from typing import Any, Dict, List

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.indexing_service import search_with_llamaindex

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

    if trace.get("degraded"):
        head.append(f"!! DEGRADED — {trace.get('degraded_reason')}")
    if trace.get("error"):
        head.append(f"!! ERROR — {trace['error']}")

    if not results:
        head.append("")
        head.append("No matching content. The index may be empty, or the filters too narrow.")
        return "\n".join(head)

    body = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata") or {}
        src = meta.get("source_filename") or meta.get("file_path") or "unknown source"
        page = meta.get("page_label")
        loc = f" p.{page}" if page else ""
        score = r.get("score")
        score_s = f" (score {score:.3f})" if isinstance(score, (int, float)) else ""
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
    description = (
        "Search the internal knowledge base for information about the project, architecture, "
        "code repositories, or documents. Returns verbatim source passages with their filenames "
        "and relevance scores, not a summary — cite the filenames in your answer."
    )
    parameters = {
        "query": ToolParameter(
            name="query",
            type="string",
            description="The specific question or query to search for in the knowledge base.",
            required=True
        ),
        "top_k": ToolParameter(
            name="top_k",
            type="int",
            description="How many passages to return (1-50). Omit for the configured default.",
            required=False
        ),
        "filter_type": ToolParameter(
            name="filter_type",
            type="string",
            description=(
                "Optional filter on the indexed content type. Real values include "
                "'document', 'text', and 'repository_summary'. Omit to search everything."
            ),
            required=False
        ),
        "project_id": ToolParameter(
            name="project_id",
            type="string",
            description="Optional project ID to scope the search.",
            required=False
        )
    }

    def __init__(self):
        super().__init__()

    def execute(self, query: str, top_k: int = None, filter_type: str = None,
                project_id: str = None) -> ToolResult:
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
