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

logger = logging.getLogger(__name__)


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
    description = "Analyze a website URL to extract content, SEO information, structure, and provide insights"

    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description="Website URL to analyze (with or without protocol)"
        ),
        "analysis_type": ToolParameter(
            name="analysis_type",
            type="string",
            required=False,
            description="Type of analysis: 'full', 'seo', 'content', 'structure' (default: 'full')",
            default="full"
        ),
        "include_metadata": ToolParameter(
            name="include_metadata",
            type="bool",
            required=False,
            description="Include metadata analysis (meta tags, Open Graph, etc.)",
            default=True
        )
    }

    def __init__(self):
        super().__init__()

    def execute(self, **kwargs) -> ToolResult:
        """Analyze website and return comprehensive report"""
        if not _is_web_access_allowed():
            return ToolResult(
                success=False,
                error="Web access is disabled. Enable it in Settings to analyze websites."
            )

        url = kwargs.get("url", "").strip()
        analysis_type = kwargs.get("analysis_type", "full")
        include_metadata = kwargs.get("include_metadata", True)

        if not url:
            return ToolResult(
                success=False,
                error="URL is required"
            )

        try:
            # Use existing web search API functionality
            from backend.api.web_search_api import extract_website_content

            # Extract basic content
            content_result = extract_website_content(url)
            
            if not content_result.get("success"):
                return ToolResult(
                    success=False,
                    error=content_result.get("error", "Failed to extract website content"),
                    output={"url": url, "raw_error": content_result}
                )

            # Build analysis report
            analysis = {
                "url": content_result.get("url", url),
                "title": content_result.get("title", ""),
                "description": content_result.get("description", ""),
                "content_preview": content_result.get("content", "")[:500] + "..." if len(content_result.get("content", "")) > 500 else content_result.get("content", ""),
                "content_length": content_result.get("content_length", 0),
            }

            # Add metadata analysis if requested
            if include_metadata:
                analysis["metadata"] = self._analyze_metadata(content_result)

            # Add SEO analysis
            if analysis_type in ("full", "seo"):
                analysis["seo"] = self._analyze_seo(content_result)

            # Add structure analysis
            if analysis_type in ("full", "structure"):
                analysis["structure"] = self._analyze_structure(url, content_result)

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

        # Basic SEO metrics
        word_count = len(content.split())
        heading_count = content.count("<h1>") + content.count("<h2>") + content.count("<h3>")
        
        # Check for common SEO elements
        has_https = content_result.get("url", "").startswith("https://")
        
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
        
        return {
            "domain": parsed_url.netloc,
            "path": parsed_url.path,
            "scheme": parsed_url.scheme,
            "has_subdomain": len(parsed_url.netloc.split(".")) > 2,
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


def _is_web_access_allowed() -> bool:
    """Check if web access is enabled in settings."""
    try:
        from flask import has_app_context
        from backend.utils.settings_utils import get_web_access
        if has_app_context():
            return get_web_access()
    except Exception:
        pass
    return False


class WebSearchTool(BaseTool):
    """
    Perform web search and return results.
    Wraps existing web search API functionality.
    """

    name = "web_search"
    description = "Search the web for information and return relevant results"

    parameters = {
        "query": ToolParameter(
            name="query",
            type="string",
            required=True,
            description="Search query string"
        ),
        "max_results": ToolParameter(
            name="max_results",
            type="int",
            required=False,
            description="Maximum number of results to return",
            default=5
        )
    }

    def __init__(self):
        super().__init__()

    def execute(self, **kwargs) -> ToolResult:
        """Perform web search"""
        if not _is_web_access_allowed():
            return ToolResult(
                success=False,
                error="Web access is disabled. Enable it in Settings to use web search."
            )

        query = kwargs.get("query", "").strip()
        max_results = kwargs.get("max_results", 5)

        if not query:
            return ToolResult(
                success=False,
                error="Search query is required"
            )

        try:
            from backend.api.web_search_api import enhanced_web_search

            search_results = enhanced_web_search(query)

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
            elif data.get("snippet"):
                # Single result format
                formatted_results["results"] = [{
                    "title": data.get("title", "Search Result"),
                    "url": data.get("url", ""),
                    "snippet": data.get("snippet", "")
                }]

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
