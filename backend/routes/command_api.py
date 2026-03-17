from flask import Blueprint, request

from backend.utils.response_utils import success_response

command_bp = Blueprint("command_api", __name__, url_prefix="/api/command")


@command_bp.route("/analyze", methods=["POST"])
def analyze_command():
    """Placeholder endpoint for /analyze chat command."""
    data = request.get_json() or {}
    return success_response(data=data, message="Analyze command executed")


@command_bp.route("/codefile", methods=["POST"])
def codefile_command():
    """Placeholder endpoint for /codefile chat command."""
    data = request.get_json() or {}
    return success_response(data=data, message="Codefile command executed")


@command_bp.route("/websearch", methods=["POST"])
def websearch_command():
    """Execute explicit web search command."""
    import logging

    logger = logging.getLogger(__name__)

    try:
        data = request.get_json() or {}
        query = data.get("query", data.get("message", ""))

        if not query:
            return success_response(
                data={"error": "No query provided"},
                message="Please provide a search query",
            )

        logger.info(f"/websearch command: '{query}'")

        # Import web search functionality
        from backend.api.web_search_api import enhanced_web_search

        # Perform search
        search_results = enhanced_web_search(query)

        if search_results.get("success"):
            result_data = search_results.get("data", {})
            result_type = result_data.get("type", "unknown")

            # Format response based on result type
            if result_type == "weather":
                response_text = (
                    f"Current weather in {result_data['location']}:\n"
                    f"Temperature: {result_data['temperature_fahrenheit']}°F "
                    f"({result_data['temperature_celsius']}°C)\n"
                    f"Conditions: {result_data['description']}\n"
                    f"Humidity: {result_data['humidity']}%"
                )
            elif result_type == "search_results":
                results = result_data.get("results", [])[:5]
                snippets = []
                for i, result in enumerate(results, 1):
                    snippets.append(
                        f"{i}. {result.get('title', 'N/A')}\n"
                        f"   {result.get('snippet', 'No description')}\n"
                        f"   URL: {result.get('url', 'N/A')}"
                    )
                response_text = "Search results:\n\n" + "\n\n".join(snippets)
            else:
                response_text = result_data.get("snippet", str(result_data))

            return success_response(
                data={
                    "query": query,
                    "response": response_text,
                    "raw_results": search_results,
                    "result_type": result_type,
                },
                message="Web search completed successfully",
            )
        else:
            error = search_results.get("error", "Unknown error")
            return success_response(
                data={
                    "query": query,
                    "error": error,
                    "response": f"Web search failed: {error}",
                },
                message="Web search failed",
            )

    except Exception as e:
        logger.error(f"/websearch command error: {e}", exc_info=True)
        return success_response(
            data={"error": str(e)}, message=f"Web search error: {str(e)}"
        )
