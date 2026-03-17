# backend/services/task_handlers/web_research_handler.py
# Handler for web research and scraping tasks
# Version 2.0 - Full implementation wrapping web_search_api and web_scraper

import logging
import json
import csv
import os
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List

from .base_handler import BaseTaskHandler, TaskResult, TaskResultStatus

logger = logging.getLogger(__name__)


class WebResearchHandler(BaseTaskHandler):
    """
    Handler for web research and scraping operations.
    Wraps: web_search_api.py, web_scraper.py
    """

    @property
    def handler_name(self) -> str:
        return "web_research"

    @property
    def display_name(self) -> str:
        return "Web Research"

    @property
    def process_type(self) -> str:
        return "web_research"

    @property
    def celery_queue(self) -> str:
        return "default"

    @property
    def default_priority(self) -> int:
        return 5  # Medium priority for web research

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "required": ["research_type"],
            "properties": {
                "research_type": {
                    "type": "string",
                    "enum": [
                        "search",
                        "scrape",
                        "analyze_website",
                        "batch_scrape",
                        "weather",
                        "combined_research",
                    ],
                    "description": "Type of research operation",
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Search queries to execute",
                },
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URLs to scrape or analyze",
                },
                "location": {
                    "type": "string",
                    "description": "Location for weather queries",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["json", "csv", "markdown"],
                    "default": "json",
                    "description": "Output format for research results",
                },
                "output_path": {
                    "type": "string",
                    "description": "Path to save output file",
                },
                "max_results_per_query": {
                    "type": "integer",
                    "default": 10,
                    "description": "Maximum results per search query",
                },
                "include_content": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include full content when scraping",
                },
                "summarize_results": {
                    "type": "boolean",
                    "default": False,
                    "description": "Use LLM to summarize research results",
                },
            },
        }

    def execute(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable[[int, str, Optional[Dict[str, Any]]], None],
    ) -> TaskResult:
        """
        Execute web research operations.
        Supports:
        - search: Web search using DuckDuckGo
        - scrape: Scrape content from URLs
        - analyze_website: Deep analysis of a website
        - batch_scrape: Scrape multiple URLs
        - weather: Get weather information
        - combined_research: Search + scrape top results
        """
        started_at = datetime.now()

        try:
            research_type = config.get("research_type", "search")

            operations = {
                "search": self._execute_search,
                "scrape": self._execute_scrape,
                "analyze_website": self._execute_analyze_website,
                "batch_scrape": self._execute_batch_scrape,
                "weather": self._execute_weather,
                "combined_research": self._execute_combined_research,
            }

            handler = operations.get(research_type)
            if not handler:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message=f"Unknown research type: {research_type}",
                    error_message=f"research_type must be one of: {', '.join(operations.keys())}",
                    started_at=started_at,
                    completed_at=datetime.now(),
                )

            return handler(task, config, progress_callback, started_at)

        except Exception as e:
            logger.error(f"Web research handler error: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Web research failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_search(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Execute web search"""
        queries = config.get("queries", [])
        max_results = config.get("max_results_per_query", 10)
        output_format = config.get("output_format", "json")
        output_path = config.get("output_path", "")

        if not queries:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No search queries provided",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        progress_callback(0, f"Starting search for {len(queries)} queries", None)

        try:
            from backend.api.web_search_api import (
                enhanced_web_search,
                perform_duckduckgo_search,
            )

            all_results = []
            failed_queries = []

            for i, query in enumerate(queries):
                progress = int(10 + (i / len(queries)) * 80)
                progress_callback(
                    progress,
                    f"Searching: {query[:50]}...",
                    {
                        "current_query": query,
                        "query_index": i + 1,
                        "total_queries": len(queries),
                    },
                )

                try:
                    result = enhanced_web_search(query)
                    if result.get("success"):
                        all_results.append(
                            {
                                "query": query,
                                "success": True,
                                "data": result.get("data", {}),
                                "strategy": result.get("strategy_used", "unknown"),
                            }
                        )
                    else:
                        failed_queries.append(query)
                        all_results.append(
                            {
                                "query": query,
                                "success": False,
                                "error": result.get("data", {}).get(
                                    "message", "Search failed"
                                ),
                            }
                        )
                except Exception as e:
                    failed_queries.append(query)
                    all_results.append(
                        {"query": query, "success": False, "error": str(e)}
                    )

            # Save results to file if path provided
            output_files = []
            if output_path:
                output_files = self._save_results(
                    all_results, output_path, output_format
                )

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(
                100,
                f"Search complete: {len(all_results) - len(failed_queries)} successful",
                None,
            )

            successful = len(all_results) - len(failed_queries)
            status = TaskResultStatus.SUCCESS
            if failed_queries:
                status = (
                    TaskResultStatus.PARTIAL
                    if successful > 0
                    else TaskResultStatus.FAILED
                )

            return TaskResult(
                status=status,
                message=f"Searched {successful}/{len(queries)} queries successfully",
                output_files=output_files,
                output_data={
                    "results": all_results,
                    "successful_queries": successful,
                    "failed_queries": failed_queries,
                },
                items_processed=successful,
                items_total=len(queries),
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except ImportError as e:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="Web search API not available",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_scrape(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Scrape content from a single URL"""
        urls = config.get("urls", [])

        if not urls:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No URLs provided",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        # For single URL, use first one
        url = urls[0]
        progress_callback(0, f"Scraping: {url[:50]}...", None)

        try:
            from backend.utils.web_scraper import scrape_website
            from backend.api.web_search_api import extract_website_content

            progress_callback(30, "Fetching content...", None)

            # Try detailed scraper first
            result = scrape_website(url)

            progress_callback(80, "Processing content...", None)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Scrape complete", None)

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Successfully scraped: {result.get('title', url)[:50]}",
                output_data={
                    "url": url,
                    "title": result.get("title", ""),
                    "content": result.get("content", "")[:5000],
                    "keywords": result.get("keywords", ""),
                    "metadata": result.get("metadata", {}),
                    "sitemaps": result.get("sitemaps", []),
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except Exception as e:
            logger.error(f"Scraping failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Scraping failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_analyze_website(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Deep analysis of a website"""
        urls = config.get("urls", [])

        if not urls:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No URL provided for analysis",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        url = urls[0]
        progress_callback(0, f"Analyzing: {url[:50]}...", None)

        try:
            from backend.utils.web_scraper import scrape_website
            import requests
            from urllib.parse import urlparse

            progress_callback(20, "Fetching website data...", None)

            # Get basic scrape data
            scrape_data = scrape_website(url)

            progress_callback(50, "Analyzing structure...", None)

            # Parse URL for domain analysis
            parsed = urlparse(url)
            domain = parsed.netloc

            # Build analysis report
            analysis = {
                "url": url,
                "domain": domain,
                "title": scrape_data.get("title", ""),
                "content_length": len(scrape_data.get("content", "")),
                "has_sitemap": len(scrape_data.get("sitemaps", [])) > 0,
                "sitemaps": scrape_data.get("sitemaps", []),
                "keywords": scrape_data.get("keywords", ""),
                "metadata": scrape_data.get("metadata", {}),
                "category": scrape_data.get("category", ""),
                "analysis_timestamp": datetime.now().isoformat(),
            }

            # Extract key metadata
            metadata = scrape_data.get("metadata", {})
            analysis["seo_info"] = {
                "description": metadata.get("description", ""),
                "og_title": metadata.get("og:title", ""),
                "og_description": metadata.get("og:description", ""),
                "og_image": metadata.get("og:image", ""),
                "twitter_card": metadata.get("twitter:card", ""),
            }

            progress_callback(100, "Analysis complete", None)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Analyzed: {domain}",
                output_data=analysis,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except Exception as e:
            logger.error(f"Website analysis failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Analysis failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_batch_scrape(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Scrape multiple URLs"""
        urls = config.get("urls", [])
        output_format = config.get("output_format", "json")
        output_path = config.get("output_path", "")
        include_content = config.get("include_content", True)

        if not urls:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No URLs provided",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        progress_callback(0, f"Starting batch scrape of {len(urls)} URLs", None)

        try:
            from backend.utils.web_scraper import scrape_website
            from backend.api.web_search_api import extract_website_content

            all_results = []
            failed_urls = []

            for i, url in enumerate(urls):
                progress = int(10 + (i / len(urls)) * 85)
                progress_callback(
                    progress,
                    f"Scraping {url[:40]}...",
                    {"current_url": url, "url_index": i + 1, "total_urls": len(urls)},
                )

                try:
                    result = scrape_website(url)
                    scraped_data = {
                        "url": url,
                        "success": True,
                        "title": result.get("title", ""),
                        "keywords": result.get("keywords", ""),
                        "category": result.get("category", ""),
                    }
                    if include_content:
                        scraped_data["content"] = result.get("content", "")[:3000]
                    all_results.append(scraped_data)
                except Exception as e:
                    failed_urls.append(url)
                    all_results.append({"url": url, "success": False, "error": str(e)})

            # Save results
            output_files = []
            if output_path:
                output_files = self._save_results(
                    all_results, output_path, output_format
                )

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, f"Batch scrape complete", None)

            successful = len(all_results) - len(failed_urls)
            status = TaskResultStatus.SUCCESS
            if failed_urls:
                status = (
                    TaskResultStatus.PARTIAL
                    if successful > 0
                    else TaskResultStatus.FAILED
                )

            return TaskResult(
                status=status,
                message=f"Scraped {successful}/{len(urls)} URLs",
                output_files=output_files,
                output_data={
                    "results": all_results,
                    "successful_count": successful,
                    "failed_urls": failed_urls,
                },
                items_processed=successful,
                items_total=len(urls),
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except ImportError as e:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="Web scraper not available",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_weather(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Get weather information"""
        location = config.get("location", "")

        if not location:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No location provided",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        progress_callback(0, f"Getting weather for: {location}", None)

        try:
            from backend.api.web_search_api import get_weather_info

            progress_callback(50, "Fetching weather data...", None)

            result = get_weather_info(location)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Weather data retrieved", None)

            if result.get("success"):
                return TaskResult(
                    status=TaskResultStatus.SUCCESS,
                    message=f"Weather for {location}: {result.get('temperature_fahrenheit')}F, {result.get('description')}",
                    output_data=result,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )
            else:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message=f"Weather lookup failed: {result.get('error', 'Unknown error')}",
                    error_message=result.get("error"),
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )

        except ImportError as e:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="Weather API not available",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_combined_research(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Search + scrape top results"""
        queries = config.get("queries", [])
        max_results = config.get("max_results_per_query", 5)
        output_format = config.get("output_format", "json")
        output_path = config.get("output_path", "")

        if not queries:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No search queries provided",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        progress_callback(
            0, f"Starting combined research for {len(queries)} queries", None
        )

        try:
            from backend.api.web_search_api import (
                enhanced_web_search,
                perform_duckduckgo_search,
            )
            from backend.utils.web_scraper import scrape_website

            all_results = []

            for i, query in enumerate(queries):
                progress = int(10 + (i / len(queries)) * 60)
                progress_callback(progress, f"Researching: {query[:40]}...", None)

                query_result = {
                    "query": query,
                    "search_results": [],
                    "scraped_content": [],
                }

                # First, search
                search_result = enhanced_web_search(query)
                if search_result.get("success"):
                    results_data = search_result.get("data", {})
                    search_results = results_data.get("results", [])
                    query_result["search_results"] = search_results[:max_results]

                    # Then scrape top results
                    for j, sr in enumerate(search_results[:3]):  # Scrape top 3
                        url = sr.get("url", "")
                        if url:
                            try:
                                scraped = scrape_website(url)
                                query_result["scraped_content"].append(
                                    {
                                        "url": url,
                                        "title": scraped.get("title", ""),
                                        "content_preview": scraped.get("content", "")[
                                            :1000
                                        ],
                                    }
                                )
                            except Exception as e:
                                logger.warning(f"Failed to scrape {url}: {e}")

                all_results.append(query_result)

            # Save results
            output_files = []
            if output_path:
                output_files = self._save_results(
                    all_results, output_path, output_format
                )

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Combined research complete", None)

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Researched {len(queries)} queries with content scraping",
                output_files=output_files,
                output_data={"results": all_results, "queries_processed": len(queries)},
                items_processed=len(queries),
                items_total=len(queries),
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except ImportError as e:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="Web research APIs not available",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _save_results(
        self, results: List[Dict], output_path: str, output_format: str
    ) -> List[str]:
        """Save research results to file"""
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

            if output_format == "json":
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2, default=str)

            elif output_format == "csv":
                if results and isinstance(results[0], dict):
                    fieldnames = results[0].keys()
                    with open(output_path, "w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        for row in results:
                            # Flatten nested dicts for CSV
                            flat_row = {}
                            for k, v in row.items():
                                if isinstance(v, (dict, list)):
                                    flat_row[k] = json.dumps(v)
                                else:
                                    flat_row[k] = v
                            writer.writerow(flat_row)

            elif output_format == "markdown":
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write("# Research Results\n\n")
                    for i, result in enumerate(results, 1):
                        f.write(f"## Result {i}\n\n")
                        for k, v in result.items():
                            if isinstance(v, (dict, list)):
                                f.write(
                                    f"**{k}:**\n```json\n{json.dumps(v, indent=2)}\n```\n\n"
                                )
                            else:
                                f.write(f"**{k}:** {v}\n\n")
                        f.write("---\n\n")

            return [output_path]

        except Exception as e:
            logger.error(f"Failed to save results: {e}")
            return []

    def get_estimated_duration(self, config: Dict[str, Any]) -> Optional[int]:
        """Estimate based on research type and item count"""
        research_type = config.get("research_type", "search")
        queries = config.get("queries", [])
        urls = config.get("urls", [])

        base_times = {
            "search": 5,
            "scrape": 10,
            "analyze_website": 15,
            "batch_scrape": 10,
            "weather": 5,
            "combined_research": 30,
        }

        base = base_times.get(research_type, 10)
        items = max(len(queries), len(urls), 1)

        return base * items

    def can_retry(self, task: Any, error: Exception) -> bool:
        """Check if task can be retried"""
        error_msg = str(error).lower()
        # Retry on network errors
        if "timeout" in error_msg or "connection" in error_msg:
            return True
        # Don't retry on rate limits
        if "rate limit" in error_msg or "too many requests" in error_msg:
            return False
        return super().can_retry(task, error)
