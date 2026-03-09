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
            filters = {}
            if filter_type:
                filters["type"] = filter_type
            if project_id:
                filters["project_id"] = project_id
                
            # Call the indexing service
            # Note: query_index returns a string response directly or might be structured
            # Based on indexing_service.py analysis, it returns a string (response.response)
            
            response = query_index(query, filters=filters if filters else None)
            
            return ToolResult(
                success=True,
                output=response
            )
        except Exception as e:
            logger.error(f"Error in KnowledgeSearchTool: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to search knowledge base: {str(e)}"
            )
