#!/usr/bin/env python3
"""
Web Analysis Tools
Executable tools for web content analysis, website scraping, and web research.
"""

import logging
from typing import Dict, Any, Optional, List
import re
from urllib.parse import urlparse

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.web_fetch import (
    DEADLINE_SECONDS, IDLE_SECONDS, MAX_PAGE_BYTES, MAX_REDIRECTS, megabytes,
)
from backend.utils.web_search_sources import (
    DEFAULT_SEARCH_RESULTS, MAX_SEARCH_QUERY_CHARS, MAX_SEARCH_RESULTS, SEARCH_ENGINE, WEATHER_SOURCE,
)

logger = logging.getLogger(__name__)

# What fetch_url and analyze_website tell a caller about the limits of a fetch;
# the numbers live in backend/utils/web_fetch.py.
_FETCH_LIMITS = (
    f"Up to {MAX_REDIRECTS} redirects are followed; each hop must be a public address. The fetch "
    f"gives up when the server is silent for {IDLE_SECONDS} s or the whole fetch passes "
    f"{DEADLINE_SECONDS} s. Only the first {megabytes(MAX_PAGE_BYTES)} of a page are read; a page "
    "that was cut short comes back with a page_cut field saying so. A URL that is not a page (a "
    "PDF, an image, a download) is refused."
)


def extract_facts_from_search_results(search_output: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract key facts from web search results for fact registry.
    
    Args:
        search_output: Output from WebSearchTool.execute()
        
    Returns:
        List of fact dictionaries with content, source, and evidence
    """
    facts = []
    
    if not isinstance(search_output, dict):
        return facts
    
    results = search_output.get("results", [])
    summary = search_output.get("summary", "")
    query = search_output.get("query", "")
    
    # Extract facts from individual search results
    for idx, result in enumerate(results[:5]):  # Top 5 results
        snippet = result.get("snippet", "")
        title = result.get("title", "")
        url = result.get("url", "")
        
        if snippet:
            # Extract location mentions (cities, states, countries)
            locations = _extract_locations(snippet)
            for location in locations:
                facts.append({
                    "content": f"Location mentioned: {location}",
                    "source": f"web_search result {idx + 1}",
                    "evidence": f"{title}: {snippet[:200]}",
                    "confidence": 0.8 if idx < 2 else 0.6
                })
            
            # Extract dates
            dates = _extract_dates(snippet)
            for date in dates:
                facts.append({
                    "content": f"Date mentioned: {date}",
                    "source": f"web_search result {idx + 1}",
                    "evidence": f"{title}: {snippet[:200]}",
                    "confidence": 0.8
                })
            
            # Extract key factual statements
            statements = _extract_factual_statements(snippet)
            for statement in statements:
                facts.append({
                    "content": statement,
                    "source": f"web_search result {idx + 1}",
                    "evidence": f"{title}: {snippet[:200]}",
                    "confidence": 0.7 if idx < 2 else 0.5
                })
    
    # Extract from summary if available
    if summary:
        summary_statements = _extract_factual_statements(summary)
        for statement in summary_statements:
            facts.append({
                "content": statement,
                "source": "web_search summary",
                "evidence": summary[:300],
                "confidence": 0.7
            })
    
    return facts


def _extract_locations(text: str) -> List[str]:
    """Extract location names (cities, states, countries) from text"""
    locations = []
    
    # Common patterns for locations
    # City, State pattern
    city_state_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),\s*([A-Z][a-z]+)\b'
    matches = re.findall(city_state_pattern, text)
    for city, state in matches:
        locations.append(f"{city}, {state}")
    
    # Standalone state/country names (common ones)
    common_locations = [
        'Arkansas', 'Missouri', 'California', 'Texas', 'New York', 'Florida',
        'United States', 'USA', 'UK', 'Canada', 'Mexico'
    ]
    text_lower = text.lower()
    for loc in common_locations:
        if loc.lower() in text_lower:
            if loc not in locations:
                locations.append(loc)
    
    return list(set(locations))  # Remove duplicates


def _extract_dates(text: str) -> List[str]:
    """Extract dates from text"""
    dates = []
    
    # Month Day, Year pattern
    date_pattern = r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})\b'
    matches = re.findall(date_pattern, text)
    for month, day, year in matches:
        dates.append(f"{month} {day}, {year}")
    
    # MM/DD/YYYY pattern
    date_pattern2 = r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b'
    matches2 = re.findall(date_pattern2, text)
    for month, day, year in matches2:
        dates.append(f"{month}/{day}/{year}")
    
    return list(set(dates))


def _extract_factual_statements(text: str) -> List[str]:
    """Extract factual statements from text"""
    statements = []
    
    # Split into sentences
    sentences = re.split(r'[.!?]+', text)
    
    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 20:
            continue
        
        # Look for factual indicators
        factual_indicators = [
            r'\b(won|sold|located|found|happened|occurred|was|is|are)\b',
            r'\b(in|at|on)\s+[A-Z]',  # Location references
            r'\$\d+',  # Money amounts
            r'\d+\s*(million|billion|thousand)',  # Large numbers
        ]
        
        sentence_lower = sentence.lower()
        if any(re.search(pattern, sentence_lower) for pattern in factual_indicators):
            # Clean up the sentence
            cleaned = re.sub(r'\s+', ' ', sentence).strip()
            if cleaned and len(cleaned) > 15:
                statements.append(cleaned)
    
    return statements[:10]  # Limit to top 10 statements


class WebAnalysisTool(BaseTool):
    """
    Analyze a website URL and extract comprehensive information.
    Provides content analysis, SEO metrics, structure analysis, and insights.
    """

    name = "analyze_website"
    read_only = True
    description = (
        "Audit one public web page's SEO live and return a JSON report, computed without an LLM: "
        "url (as requested, https:// added to a bare domain) and final_url (after redirects), title, meta "
        "description, a 500-character preview; a metadata block (on by default) checking title and "
        "meta-description lengths against 30-60 and 120-160 characters; and, by analysis_type, an seo "
        "block (word count of the page's main text, reading time, content density, https), a structure "
        "block (host name, path, scheme) "
        "and an insights block (sentence stats, readability and keyword hints for article, product or "
        "landing page, from the ~2000-character extract). Use it to audit a page's title and "
        "description; to read the page use fetch_url, to find pages web_search. Needs web access on "
        "in Settings (off by default), and over MCP the Guaardvark backend running; private and local "
        "addresses are refused, including after a redirect or a DNS change."
    )

    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description=(
                "Page URL or bare domain, e.g. 'https://example.com/about' or 'example.com' "
                "(https:// is added). " + _FETCH_LIMITS
            ),
        ),
        "analysis_type": ToolParameter(
            name="analysis_type",
            type="string",
            required=False,
            enum=["full", "seo", "structure", "content"],
            description="Which blocks to add: 'seo' (word_count and estimated_reading_time at 200 words a minute for the page's main text, content_density, has_https, title/description present), 'structure' (domain: the host name without port or login, path, scheme, is_secure), 'content' (sentence_count, average_sentence_length, readability, content_type_hints), or 'full' (default) for all three.",
            default="full"
        ),
        "include_metadata": ToolParameter(
            name="include_metadata",
            type="bool",
            required=False,
            description="Default true: add a metadata block with has_title, has_description, their lengths, and whether they fall within 30-60 and 120-160 characters. Open Graph and other meta tags are not read.",
            default=True
        ),
        "query": ToolParameter(
            name="query",
            type="string",
            required=False,
            description=(
                "The user's question, in their words. When given, the preview and the insights block "
                "come from the ~2000-character stretch of the page that contains the most of its words "
                "instead of the top of the page (often navigation)."
            ),
            default="",
        ),
    }

    def __init__(self):
        super().__init__()

    def execute(self, **kwargs) -> ToolResult:
        """Analyze website and return comprehensive report"""
        blocked = _web_access_block_reason("analyze websites")
        if blocked:
            return ToolResult(
                success=False,
                error=blocked
            )

        url = kwargs.get("url", "").strip()
        analysis_type = kwargs.get("analysis_type", "full")
        include_metadata = kwargs.get("include_metadata", True)
        query = (kwargs.get("query") or "").strip() or None

        if not url:
            return ToolResult(
                success=False,
                error="URL is required"
            )
        from backend.services.web_provenance import refusal as _provenance_refusal
        not_named = _provenance_refusal(kwargs.get("_agent_context"), url)
        if not_named:
            return ToolResult(success=False, error=not_named, metadata={"blocked_by": "web_provenance"})

        try:
            # Use existing web search API functionality
            from backend.api.web_search_api import extract_website_content

            # Extract basic content
            content_result = extract_website_content(url, query=query, public_only=True)

            if not content_result.get("success"):
                return ToolResult(
                    success=False,
                    error=content_result.get("error", "Failed to extract website content"),
                )

            # Build analysis report
            analysis = {
                "url": content_result.get("url", url),
                "final_url": content_result.get("final_url", content_result.get("url", url)),
                "title": content_result.get("title", ""),
                "description": content_result.get("description", ""),
                "content_preview": content_result.get("content", "")[:500] + "..." if len(content_result.get("content", "")) > 500 else content_result.get("content", ""),
                "content_length": content_result.get("content_length", 0),
            }
            if content_result.get("page_cut"):
                analysis["page_cut"] = content_result["page_cut"]

            # Add metadata analysis if requested
            if include_metadata:
                analysis["metadata"] = self._analyze_metadata(content_result)

            # Add SEO analysis
            if analysis_type in ("full", "seo"):
                analysis["seo"] = self._analyze_seo(content_result)

            # Add structure analysis
            if analysis_type in ("full", "structure"):
                analysis["structure"] = self._analyze_structure(analysis["final_url"], content_result)

            # Add content insights
            if analysis_type in ("full", "content"):
                analysis["insights"] = self._analyze_content(content_result)

            return ToolResult(
                success=True,
                output=analysis,
                metadata={
                    "analysis_type": analysis_type,
                    "url": url,
                    "timestamp": content_result.get("timestamp")
                }
            )

        except Exception as e:
            logger.error(f"Web analysis failed for {url}: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Analysis failed: {str(e)}",
                output={"url": url}
            )

    def _analyze_metadata(self, content_result: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze metadata from website content"""
        return {
            "has_title": bool(content_result.get("title")),
            "has_description": bool(content_result.get("description")),
            "title_length": len(content_result.get("title", "")),
            "description_length": len(content_result.get("description", "")),
            "title_optimal": 30 <= len(content_result.get("title", "")) <= 60,
            "description_optimal": 120 <= len(content_result.get("description", "")) <= 160,
        }

    def _analyze_seo(self, content_result: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze SEO aspects of the website"""
        content = content_result.get("content", "").lower()
        title = content_result.get("title", "").lower()
        description = content_result.get("description", "").lower()

        # Counts cover the whole page text, not only the returned extract.
        word_count = content_result.get("page_word_count") or len(content.split())
        final_url = content_result.get("final_url") or content_result.get("url", "")
        has_https = final_url.startswith("https://")
        
        return {
            "word_count": word_count,
            "content_length": content_result.get("content_length", 0),
            "has_https": has_https,
            "title_present": bool(title),
            "description_present": bool(description),
            "estimated_reading_time": round(word_count / 200, 1),  # Average reading speed
            "content_density": "high" if word_count > 1000 else "medium" if word_count > 300 else "low"
        }

    def _analyze_structure(self, url: str, content_result: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze website structure"""
        parsed_url = urlparse(url)

        # The host name only: netloc would repeat a login or port from the URL.
        # Whether the host has a subdomain is not reported: telling
        # "news.example.com" from "example.co.uk" needs the public-suffix list,
        # which is not bundled.
        return {
            "domain": parsed_url.hostname or "",
            "path": parsed_url.path,
            "scheme": parsed_url.scheme,
            "is_secure": parsed_url.scheme == "https"
        }

    def _analyze_content(self, content_result: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze content quality and characteristics"""
        content = content_result.get("content", "")
        
        # Basic content metrics
        sentences = re.split(r'[.!?]+', content)
        avg_sentence_length = sum(len(s.split()) for s in sentences) / len(sentences) if sentences else 0
        
        # Detect content type hints
        content_lower = content.lower()
        is_article = any(word in content_lower for word in ["article", "post", "blog", "news"])
        is_product = any(word in content_lower for word in ["price", "buy", "cart", "product"])
        is_landing = any(word in content_lower for word in ["sign up", "get started", "learn more", "contact us"])
        
        return {
            "sentence_count": len(sentences),
            "average_sentence_length": round(avg_sentence_length, 1),
            "content_type_hints": {
                "article": is_article,
                "product": is_product,
                "landing_page": is_landing
            },
            "readability": "high" if avg_sentence_length < 20 else "medium" if avg_sentence_length < 30 else "low"
        }


def _web_access_block_reason(action: str) -> str | None:
    """None when web access is enabled; otherwise the error the tool returns.
    The check is the one research tasks and the outreach recon make too."""
    from backend.utils.settings_utils import web_access_block_reason
    return web_access_block_reason(action)


def _is_web_access_allowed() -> bool:
    """Check if web access is enabled in settings."""
    return _web_access_block_reason("use the web") is None


class FetchUrlTool(BaseTool):
    """
    Fetch a specific URL and return its text content. Single-purpose primitive —
    use this whenever the user asks about a specific webpage or domain. Distinct
    from web_search (which runs a search query) and from analyze_website
    (which produces a structured SEO/metadata report).
    """

    name = "fetch_url"
    read_only = True
    description = (
        "Read one public web page live, for ANY question about a specific webpage or domain (e.g. "
        "'what's on example.com', 'read https://site.com/page'). Returns JSON {url, final_url, title, "
        "description, content, content_length}: the title ('' if the page has none), the meta "
        "description and up to ~2000 characters of main text (scripts, styles, nav, footers and asides "
        "removed; JavaScript is not run). Pass query with the user's question to get the stretch of the "
        "page that contains the most of its words instead of the top of the page. For open-ended "
        "searches use web_search; for a title and meta-description audit use analyze_website. Needs web "
        "access on in Settings (off by default), and over MCP the Guaardvark backend running; private "
        "and local addresses are refused, including after a redirect or a DNS change."
    )

    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description=(
                "Page URL or bare domain, e.g. 'https://example.com/pricing' or 'example.com' "
                "(https:// is added). " + _FETCH_LIMITS
            ),
        ),
        "query": ToolParameter(
            name="query",
            type="string",
            required=False,
            description=(
                "The user's question, in their words. Its words of three or more letters or digits "
                "(common stopwords ignored) pick the ~2000-character stretch with the most of them; "
                "the top of the page is returned when there is no query, none of the words occur, "
                "or the page already fits."
            ),
            default="",
        ),
    }

    def __init__(self):
        super().__init__()

    def execute(self, **kwargs) -> ToolResult:
        """Fetch the URL and return its text content."""
        blocked = _web_access_block_reason("fetch URLs")
        if blocked:
            return ToolResult(
                success=False,
                error=blocked,
            )

        url = (kwargs.get("url") or "").strip()
        if not url:
            return ToolResult(
                success=False,
                error="url parameter is required",
            )
        from backend.services.web_provenance import refusal as _provenance_refusal
        not_named = _provenance_refusal(kwargs.get("_agent_context"), url)
        if not_named:
            return ToolResult(success=False, error=not_named, metadata={"blocked_by": "web_provenance"})
        query = (kwargs.get("query") or "").strip() or None

        try:
            from backend.api.web_search_api import extract_website_content

            result = extract_website_content(url, query=query, public_only=True)
            if not result.get("success"):
                return ToolResult(
                    success=False,
                    error=result.get("error", "Failed to fetch URL"),
                )

            # Return a flat, LLM-friendly shape. No SEO layers, no nested metadata —
            # the single-purpose framing is the whole point of this tool.
            output = {
                "url": result.get("url", url),
                "final_url": result.get("final_url", result.get("url", url)),
                "title": result.get("title", ""),
                "description": result.get("description", ""),
                "content": result.get("content", ""),
                "content_length": result.get("content_length", 0),
            }
            if result.get("page_cut"):
                output["page_cut"] = result["page_cut"]
            return ToolResult(success=True, output=output)
        except Exception as e:
            logger.error(f"fetch_url failed for {url}: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to fetch URL: {str(e)}",
                output={"url": url},
            )


class WebSearchTool(BaseTool):
    """
    Perform web search and return results.
    Wraps existing web search API functionality.
    """

    name = "web_search"
    read_only = True
    description = (
        "Search the web: returns a ranked list of titles, snippets and URLs for a query, and a "
        "source field naming the service that answered. Use this for open-ended research or when "
        "you need to discover pages about a topic. For fetching a SPECIFIC URL or domain the user "
        "already named, use fetch_url instead (it's a direct fetch, no search ranking in between). "
        f"The query is sent to {SEARCH_ENGINE} (through the duckduckgo-search client) and nowhere "
        f"else. When {SEARCH_ENGINE} finds nothing, results is empty, no_results is true and summary "
        f"says so; when {SEARCH_ENGINE} refuses the search or cannot be reached, the call fails with "
        f"the reason. A question about the weather in a named place goes to {WEATHER_SOURCE} "
        "instead; the current time and plain arithmetic are answered on this machine; a URL in the "
        "query is fetched directly, as fetch_url fetches it: private and local addresses are refused, "
        "including after a redirect. Needs web access on in Settings (off by default)."
    )

    parameters = {
        "query": ToolParameter(
            name="query",
            type="string",
            required=True,
            description=(
                f"Search query string, at most {MAX_SEARCH_QUERY_CHARS} characters; a longer "
                "one is refused unsent"
            ),
        ),
        "max_results": ToolParameter(
            name="max_results",
            type="int",
            required=False,
            description=(
                f"How many results to ask for, 1 to {MAX_SEARCH_RESULTS} (default "
                f"{DEFAULT_SEARCH_RESULTS}). Fewer come back when the search finds fewer."
            ),
            default=DEFAULT_SEARCH_RESULTS,
            minimum=1,
            maximum=MAX_SEARCH_RESULTS,
        )
    }

    def __init__(self):
        super().__init__()

    def execute(self, **kwargs) -> ToolResult:
        """Perform web search"""
        blocked = _web_access_block_reason("use web search")
        if blocked:
            return ToolResult(
                success=False,
                error=blocked
            )

        query = kwargs.get("query", "").strip()

        if not query:
            return ToolResult(
                success=False,
                error="Search query is required"
            )

        # The same cap enhanced chat applies to the person's message: the whole
        # query goes to the search engine.
        if len(query) > MAX_SEARCH_QUERY_CHARS:
            logger.info(f"web_search refused, query too long (query_len={len(query)})")
            return ToolResult(
                success=False,
                error=(
                    f"Refused: the query is longer than {MAX_SEARCH_QUERY_CHARS} characters, "
                    "and a search would send all of it to the search engine. Search with a "
                    "short query."
                ),
                output={"query_length": len(query)},
            )

        # A URL in the query is fetched directly, so it is held to the same
        # rule as fetch_url (web_provenance).
        from backend.services import web_provenance
        ctx = kwargs.get("_agent_context")
        for named in web_provenance.URL_RE.findall(query):
            not_named = web_provenance.refusal(ctx, named)
            if not_named:
                return ToolResult(success=False, error=not_named, metadata={"blocked_by": "web_provenance"})

        try:
            from backend.api.web_search_api import enhanced_web_search, search_result_count

            # Chat callers are not held to the schema's bounds, so the number
            # is brought into range here.
            max_results = search_result_count(kwargs.get("max_results", DEFAULT_SEARCH_RESULTS))
            search_results = enhanced_web_search(query, max_results=max_results)

            if search_results and not search_results.get("success") and (
                    (search_results.get("data") or {}).get("type") == "no_results"):
                # The engine answered and found nothing. That is a result, not a
                # fault: the caller can rephrase, and a run of them does not
                # trip the failure breaker in the chat loop or the MCP server.
                data = search_results["data"]
                return ToolResult(
                    success=True,
                    output={
                        "query": query,
                        "results": [],
                        "summary": data.get("message", ""),
                        "source": data.get("source", SEARCH_ENGINE),
                        "no_results": True,
                    },
                    metadata={"result_count": 0, "query": query},
                )

            if not search_results or not search_results.get("success"):
                error_msg = search_results.get("error") if search_results else "Web search failed"
                if search_results and "data" in search_results:
                    error_msg = search_results["data"].get("message", error_msg)
                return ToolResult(
                    success=False,
                    error=error_msg,
                    output={"query": query}
                )

            # Extract data from nested structure
            data = search_results.get("data", {})
            strategy = search_results.get("strategy_used", "unknown")

            # Format results
            formatted_results = {
                "query": query,
                "results": [],
                "summary": data.get("snippet", ""),
                "source": data.get("source", strategy)
            }

            # Extract individual results if available
            if isinstance(data.get("results"), list):
                formatted_results["results"] = data["results"][:max_results]
            elif data.get("url"):
                formatted_results["results"] = [{
                    "title": data.get("title", ""),
                    "url": data.get("url", ""),
                    "snippet": data.get("snippet", "")
                }]
                if data.get("page_cut"):
                    formatted_results["page_cut"] = data["page_cut"]
            elif data.get("snippet"):
                # Single result format
                formatted_results["results"] = [{
                    "title": data.get("title", "Search Result"),
                    "url": data.get("url", ""),
                    "snippet": data.get("snippet", "")
                }]

            if isinstance(ctx, dict) and ctx.get("transport") == "chat":
                web_provenance.note_search_results(
                    ctx.get("chat_session_id"),
                    [r.get("url", "") for r in formatted_results["results"] if isinstance(r, dict)])
            return ToolResult(
                success=True,
                output=formatted_results,
                metadata={
                    "result_count": len(formatted_results["results"]),
                    "query": query
                }
            )

        except Exception as e:
            logger.error(f"Web search failed for query '{query}': {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Search failed: {str(e)}",
                output={"query": query}
            )
