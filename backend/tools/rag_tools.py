import logging
from typing import Dict, Any, List

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.indexing_service import query_index

logger = logging.getLogger(__name__)

class KnowledgeSearchTool(BaseTool):
    """
    Tool for searching the internal knowledge base (RAG).
    Use this to retrieve information about the codebase, architecture, specific repositories, 
    or any documents that have been indexed.
    """
    
    name = "search_knowledge_base"
    description = "Search the internal knowledge base for information about the project, architecture, code repositories, or documents."
    parameters = {
        "query": ToolParameter(
            name="query",
            type="string",
            description="The specific question or query to search for in the knowledge base.",
            required=True
        ),
        "filter_type": ToolParameter(
            name="filter_type",
            type="string",
            description="Optional filter by document type (e.g., 'repository_summary', 'document', 'code').",
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

    def execute(self, query: str, filter_type: str = None, project_id: str = None) -> ToolResult:
        logger.info(f"Executing KnowledgeSearchTool: {query}")
        try:
            # filter_type has no backend support in query_index; accepted for
            # LLM compatibility but not forwarded
            response = query_index(query, project_id=project_id)
            if response is None:
                return ToolResult(
                    success=False,
                    error="Knowledge base query returned no result "
                          "(index unavailable or empty).",
                )
            return ToolResult(
                success=True,
                output=str(response)
            )
        except Exception as e:
            logger.error(f"Error in KnowledgeSearchTool: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to search knowledge base: {str(e)}"
            )
