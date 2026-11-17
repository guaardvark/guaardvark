
import logging
import json
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import requests
from urllib.parse import quote_plus, urlsplit
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

from flask import Blueprint, current_app, jsonify, request
from backend.utils.response_utils import success_response, error_response
from backend.utils.settings_utils import get_web_access
from backend.utils.safe_math import evaluate_arithmetic
from backend.utils.hosts import no_netrc_session
from backend.utils.text_focus import focus_window
from backend.utils.request_phrases import QUESTION_LEAD_IN, TRAILING_WHEN, normalise_message
from backend.utils.web_fetch import (
    LOCAL_FILE_ADVICE, FetchFailed, FetchRefused, decode_page, fetch_page,
)
from backend.utils.web_search_sources import (
    DEFAULT_SEARCH_RESULTS, MAX_SEARCH_RESULTS, SEARCH_ENGINE, WEATHER_SOURCE,
)

web_search_bp = Blueprint("web_search_api", __name__, url_prefix="/api/web-search")
logger = logging.getLogger(__name__)

_NOT_A_WEB_URL = f"Refused: only http and https URLs can be fetched. {LOCAL_FILE_ADVICE}"


def extract_website_content(url: str, query: Optional[str] = None, public_only: bool = False) -> Dict[str, Any]:
    """Fetch a page and return its title, description and up to 2,000 characters of its text.

    ``public_only`` refuses any address that is not globally routable, on every
    redirect hop; every fetch of a URL a caller or a model chose asks for it
    (fetch_url, analyze_website, a URL in a web search, a competitor page).
    Logins saved in ~/.netrc are never sent, with or without it.

    The fetch is bounded in size and time (:mod:`backend.utils.web_fetch`). A
    page that was read only in part is still returned, with ``page_cut`` saying
    what was left out; the key is absent for a page read in full.

    With ``query`` the text is the stretch of the page about the query
    (:func:`backend.utils.text_focus.focus_window`); without it, the head of the page.
    """
    try:
        url = url.strip()
        scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://", url)
        if scheme and scheme.group(1).lower() not in ("http", "https"):
            return {"success": False, "url": url, "error": _NOT_A_WEB_URL}
        if scheme:
            url = scheme.group(1).lower() + url[len(scheme.group(1)):]
        else:
            url = 'https://' + url
            
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        try:
            page = fetch_page(url, headers=headers, public_only=public_only)
        except (FetchRefused, FetchFailed) as e:
            return {"success": False, "url": url, "error": str(e)}
        current = page.url

        text, _encoding = decode_page(page.body, page.charset, complete=page.cut is None)
        soup = BeautifulSoup(text, 'html.parser')

        for script in soup(["script", "style", "nav", "footer", "aside"]):
            script.decompose()
        
        title = soup.find('title')
        title_text = title.get_text().strip() if title else ""
        
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        description = meta_desc['content'].strip() if meta_desc and meta_desc.get('content') else ""
        
        content_selectors = [
            'main', 'article', '.content', '#content', 
            '.main-content', '#main-content', '.post-content',
            'body'
        ]
        
        content_text = ""
        for selector in content_selectors:
            content_elem = soup.select_one(selector)
            if content_elem:
                content_text = content_elem.get_text(separator=' ', strip=True)
                break
        
        if not content_text:
            content_text = soup.get_text(separator=' ', strip=True)
        
        content_text = re.sub(r'\s+', ' ', content_text)
        page_word_count = len(content_text.split())
        content_text = focus_window(content_text, query, 2000) if query else content_text[:2000]
        
        result = {
            "success": True,
            "url": url,
            "final_url": current,
            "title": title_text,
            "description": description,
            "content": content_text,
            "content_length": len(content_text),
            "page_word_count": page_word_count,
        }
        if page.cut:
            result["page_cut"] = page.cut
        return result

    except requests.RequestException as e:
        logger.error(f"Website scraping failed for {url}: {e}")
        return {
            "success": False,
            "url": url,
            "error": f"Failed to access website: {str(e)}"
        }
    except Exception as e:
        logger.error(f"Website content extraction failed for {url}: {e}")
        return {
            "success": False,
            "url": url,
            "error": f"Failed to extract content: {str(e)}"
        }

# Entries a sitemap report lists by name; its counts cover every entry read.
SITEMAP_LISTED_ENTRIES = 20
# Top-level pages (one path segment) a sitemap report names as landing pages.
SITEMAP_LANDING_PAGES = 5


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _sitemap_entries(body: bytes):
    """Parse a sitemap body as far as it is well-formed.

    Returns ``(kind, entries, stopped)``: kind is the root element's name
    ("urlset" or "sitemapindex"), or None when the body is not a sitemap;
    entries are dicts of the child elements of each <url> or <sitemap>; stopped
    is None, or the parse error that ended reading early (a body cut at the size
    limit ends that way).
    """
    parser = ET.XMLPullParser(events=("start", "end"))
    kind = None
    entry_name = None
    entries = []
    stopped = None
    try:
        parser.feed(body)
        parser.close()
    except ET.ParseError as e:
        stopped = e
    try:
        for event, element in parser.read_events():
            name = _local_name(element.tag)
            if event == "start":
                if kind is None:
                    kind = name
                    if kind not in ("urlset", "sitemapindex"):
                        return None, [], None
                    entry_name = "url" if kind == "urlset" else "sitemap"
                continue
            if name == entry_name:
                entry = {}
                for child in element:
                    field = _local_name(child.tag)
                    if field in ("loc", "lastmod", "changefreq", "priority") and child.text:
                        entry[field] = child.text.strip()
                if entry.get("loc"):
                    entries.append(entry)
                element.clear()
    except ET.ParseError as e:
        stopped = stopped or e
    return kind, entries, stopped


def _path_depth(url: str) -> int:
    return len([part for part in urlsplit(url).path.split("/") if part])


def read_sitemap(url: str) -> Dict[str, Any]:
    """Fetch one sitemap and summarise it: its kind (a list of pages, or an
    index of other sitemaps), how many entries it has, the first
    ``SITEMAP_LISTED_ENTRIES`` of them, and for a list of pages, how many sit at
    each path depth and which top-level pages it gives a priority above 0.5.

    The fetch is the one fetch_url makes: public addresses only, on every
    redirect; no ~/.netrc logins; bounded in size and time. The sitemaps an
    index names are listed, not fetched. A sitemap cut at the size limit is
    read up to the cut, and ``page_cut`` says so.
    """
    url = (url or "").strip()
    scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://", url)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        return {"success": False, "url": url, "error": _NOT_A_WEB_URL}
    if not scheme:
        url = "https://" + url

    try:
        page = fetch_page(url, headers={"User-Agent": "Guaardvark-Sitemap/1.0"}, public_only=True)
    except (FetchRefused, FetchFailed) as e:
        return {"success": False, "url": url, "error": str(e)}
    except requests.RequestException as e:
        return {"success": False, "url": url, "error": f"Failed to access the sitemap: {e}"}

    kind, entries, stopped = _sitemap_entries(page.body)
    if kind is None:
        return {
            "success": False,
            "url": url,
            "error": (f"Not a sitemap: {page.url} is not a sitemap XML document "
                      "(its root element is not <urlset> or <sitemapindex>)."),
        }

    report: Dict[str, Any] = {
        "success": True,
        "url": url,
        "final_url": page.url,
        "type": kind,
        "total": len(entries),
        "entries": entries[:SITEMAP_LISTED_ENTRIES],
    }
    if kind == "urlset":
        by_depth: Dict[int, int] = {}
        landing_pages = []
        for entry in entries:
            depth = _path_depth(entry["loc"])
            by_depth[depth] = by_depth.get(depth, 0) + 1
            try:
                priority = float(entry.get("priority") or 0)
            except ValueError:
                priority = 0.0
            if depth == 1 and priority > 0.5 and len(landing_pages) < SITEMAP_LANDING_PAGES:
                landing_pages.append(entry)
        report["by_depth"] = dict(sorted(by_depth.items()))
        report["landing_pages"] = landing_pages
    if page.cut:
        report["page_cut"] = page.cut
    elif stopped is not None:
        read = f"{len(entries)} entry" if len(entries) == 1 else f"{len(entries)} entries"
        report["incomplete"] = (
            f"The sitemap stops being well-formed XML after {read} ({stopped}); "
            "the counts cover only those."
        )
    return report


def get_weather_info(location: str) -> Dict[str, Any]:
    try:
        
        weather_apis = [
            f"https://wttr.in/{quote_plus(location)}?format=j1",
        ]
        
        for api_url in weather_apis:
            try:
                headers = {'User-Agent': 'Guaardvark-Weather/1.0'}
                with no_netrc_session() as session:
                    response = session.get(api_url, headers=headers, timeout=10)

                if response.ok:
                    data = response.json()
                    
                    if 'current_condition' in data:
                        current = data['current_condition'][0]
                        weather_desc = current.get('weatherDesc', [{}])[0].get('value', 'Unknown')
                        temp_c = current.get('temp_C', 'Unknown')
                        temp_f = current.get('temp_F', 'Unknown')
                        humidity = current.get('humidity', 'Unknown')
                        
                        return {
                            "success": True,
                            "location": location,
                            "temperature_celsius": temp_c,
                            "temperature_fahrenheit": temp_f,
                            "description": weather_desc,
                            "humidity": humidity,
                            "source": WEATHER_SOURCE
                        }
                        
            except Exception as e:
                logger.warning(f"Weather API {api_url} failed: {e}")
                continue
        
        return {
            "success": False,
            "location": location,
            "error": "Could not retrieve weather data from available sources"
        }
        
    except Exception as e:
        logger.error(f"Weather lookup failed for {location}: {e}")
        return {
            "success": False,
            "location": location,
            "error": f"Weather service error: {str(e)}"
        }

def enhanced_web_search(query: str, max_results: int = DEFAULT_SEARCH_RESULTS) -> Dict[str, Any]:
    """Answer ``query`` from the web. A URL in the query is fetched directly,
    and only from a public address, on every redirect, exactly as fetch_url
    fetches: the query can come from a model or from a page it read, on any
    path (chat and MCP tools, the web-search routes, /websearch, research tasks,
    the outreach recon). ``max_results`` is how many search results to ask for
    (1 to ``MAX_SEARCH_RESULTS``). ``data["source"]`` names the service that
    answered."""

    results = {
        "query": query,
        "strategy_used": "",
        "success": False,
        "data": {}
    }
    
    special_result = handle_special_queries(query)
    if special_result["success"]:
        return special_result

    urls = _urls_in_text(query)

    if urls:
        url = urls[0]
        if not re.match(r'https?://', url, re.IGNORECASE):
            url = 'https://' + url
            
        logger.info(f"Direct website access for: {url}")
        website_data = extract_website_content(url, public_only=True)
        if not website_data["success"] and str(website_data.get("error", "")).startswith(
                ("Refused", "Not a web page")):
            # A refused address, or a URL that serves a file instead of a page,
            # ends the call: searching the web for the URL's text would answer a
            # question nobody asked, and would send the query to the search engine.
            results["error"] = website_data["error"]
            results["data"] = {"message": website_data["error"]}
            return results

        if website_data["success"]:
            results.update({
                "strategy_used": "direct_website",
                "success": True,
                "data": {
                    "type": "website_content",
                    "url": website_data["url"],
                    "title": website_data["title"],
                    "description": website_data["description"],
                    "content": website_data["content"],
                    "snippet": f"Website: {website_data['title']}\n\nDescription: {website_data['description']}\n\nContent: {website_data['content'][:500]}..."
                }
            })
            if website_data.get("page_cut"):
                results["data"]["page_cut"] = website_data["page_cut"]
            return results
        else:
            results["data"]["website_error"] = website_data["error"]
    
    logger.info(f"Performing web search for: {query}")
    search = perform_duckduckgo_search(query, max_results=max_results)

    # strategy_used "duckduckgo_search" is an older name that callers match
    # on; data["source"] is what names the engine.
    if search["success"]:
        results.update({
            "strategy_used": "duckduckgo_search",
            "success": True,
            "data": {
                "type": "search_results",
                "results": search["results"],
                "snippet": search["snippet"],
                "total_results": search["total_results"],
                "source": search.get("source") or SEARCH_ENGINE
            }
        })
        return results

    # No results and a failed search both end here, with the engine's own
    # answer as the message: "no_results" when it found nothing,
    # "search_failed" when it could not be asked.
    message = search.get("error") or f"The search could not be completed at {SEARCH_ENGINE}."
    website_error = results["data"].get("website_error")
    if website_error:
        message = f"Could not read {url}: {str(website_error).rstrip('.')}. {message}"
    return {
        "query": query,
        "strategy_used": "failed",
        "success": False,
        "error": message,
        "data": {
            "type": "no_results" if search.get("no_results") else "search_failed",
            "message": message,
            "source": SEARCH_ENGINE,
            "errors": {**results["data"], "search_error": search.get("error")},
            "attempted_strategies": ["duckduckgo_search"] + (["direct_website"] if urls else [])
        }
    }


_URL_IN_TEXT = re.compile(r'(?:https?://|www\.)\S+', re.IGNORECASE)
_URL_TRAILING_PUNCTUATION = ".,;:!?'\"`*"
_URL_CLOSERS = {")": "(", "]": "[", "}": "{", ">": "<"}


def _urls_in_text(text: str) -> List[str]:
    """The URLs in ``text`` as written (path case kept), without the sentence
    punctuation or quotes that follow them. A closing bracket stays when the URL
    itself opened it, as in ``https://en.wikipedia.org/wiki/Mercury_(planet)``."""
    urls = []
    for url in _URL_IN_TEXT.findall(text or ""):
        while url:
            last = url[-1]
            if last in _URL_TRAILING_PUNCTUATION or (
                last in _URL_CLOSERS and url.count(last) > url.count(_URL_CLOSERS[last])
            ):
                url = url[:-1]
            else:
                break
        if _URL_IN_TEXT.fullmatch(url):
            urls.append(url)
    return urls


# Shortcuts that answer a query without searching: the clock, wttr.in and the
# calculator. Each fires only when the whole query asks for it. A query that
# merely contains 'time', 'temperature', 'forecast' or '=' ('python requests
# timeout', 'LLM temperature setting explained', 'equation of a line y = 2x + 3')
# goes to the search. Chat passes the person's whole message, so a short polite
# lead-in and a trailing "now" / "today" are allowed around each form
# (backend.utils.request_phrases, shared with the chat's workstation shortcuts).
_LEAD_IN = QUESTION_LEAD_IN
_WHEN = TRAILING_WHEN
# Every shortcut form is a short question; a longer query is always searched,
# which also keeps the matchers below cheap on very long input.
_SHORTCUT_MAX_CHARS = 200
_WHAT_IS = r"what(?:'s|s|\s+is)?"

# The clock knows this machine's zone and UTC only, so "what time is it in
# Tokyo" is left to the search.
_TIME_QUERY = re.compile(
    "^" + _LEAD_IN + "(?:"
    + _WHAT_IS + r"\s+(?:the\s+)?(?:current\s+|exact\s+)?time(?:\s+is\s+it|\s+it\s+is)?"
    r"|(?:the\s+)?(?:current|exact)\s+time"
    r"|(?:the\s+)?time(?:\s+right)?\s+now"
    r"|(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?tell\s+me\s+the\s+time"
    ")" + _WHEN + "$"
)

_WEATHER_QUERY = re.compile(
    "^" + _LEAD_IN + "(?:"
    r"(?:(?:what|how)(?:'s|s|\s+is)?\s+)?(?:the\s+)?(?:current\s+|today'?s\s+)?weather"
    r"(?:\s+forecast)?(?:\s+(?:like|is|going\s+to\s+be))*"
    r"|(?:" + _WHAT_IS + r"\s+(?:the\s+)?(?:current\s+)?|(?:the\s+)?current\s+)temperature(?:\s+(?:is|outside))*"
    r"|how\s+(?:hot|cold|warm)\s+is\s+it(?:\s+outside)?"
    r"|is\s+it\s+(?:raining|snowing|sunny|hot|cold|warm)(?:\s+outside)?"
    ")" + _WHEN + r"\s+(?:in|at|for)\s+(?P<place>[^\W_][\w .,'\-]*?)" + _WHEN + "$"
)
_PLACE_MAX_WORDS = 5
# First words of phrases after "in/at/for" that are not a place name:
# "temperature in celsius", "how hot is it in a car", "weather for tomorrow".
_NOT_A_PLACE = frozenset({
    "a", "an", "this", "next", "my", "your", "our",
    "celsius", "fahrenheit", "kelvin", "degrees",
    "today", "tonight", "tomorrow", "now",
})

# A query that is itself arithmetic, optionally after "calculate" / "what is".
_MATH_QUERY = re.compile(
    "^" + _LEAD_IN
    + r"(?:(?P<verb>(?:(?:can|could|would)\s+you\s+(?:please\s+)?)?"
    r"(?:calculate|compute|evaluate|solve|work\s+out|how\s+much\s+is|" + _WHAT_IS + r"))\s+)?"
    r"(?P<expr>[\d\s.+\-*/%^()]+?)(?:\s*=)?$"
)
_MATH_BINARY_OP = re.compile(r"[\d.)]\s*(?:\*\*|//|[-+*/%^])\s*[-+(\s]*[\d.]")
# Digits joined by one repeated '-' or '/' read as a date, phone or part
# number ("2026-09-30", "9/30/2026", "555-123-4567"); without "calculate" in
# front they are searched, not subtracted or divided.
_MATH_IDENTIFIER = re.compile(r"^\d+(?:-\d+){2,}$|^\d+(?:/\d+){2,}$")

_normalise_query = normalise_message


def _is_time_query(normalised: str) -> bool:
    return bool(_TIME_QUERY.match(normalised))


def _weather_place(normalised: str) -> Optional[str]:
    """The place a weather question names, or None when the query is not one."""
    match = _WEATHER_QUERY.match(normalised)
    if not match:
        return None
    place = match.group("place").strip(" ,.")
    words = place.split()
    if not words or len(words) > _PLACE_MAX_WORDS or words[0] in _NOT_A_PLACE:
        return None
    return place


def _arithmetic_expression(normalised: str) -> Optional[str]:
    """The expression when the query is an arithmetic question, else None."""
    match = _MATH_QUERY.match(normalised)
    if not match:
        return None
    expr = match.group("expr").strip()
    if not _MATH_BINARY_OP.search(expr):
        return None
    if not match.group("verb") and _MATH_IDENTIFIER.match(expr):
        return None
    return expr.replace("^", "**")


def handle_special_queries(query: str) -> Dict[str, Any]:
    normalised = _normalise_query(query)
    short = len(normalised) <= _SHORTCUT_MAX_CHARS

    if short and _is_time_query(normalised):
        try:
            from datetime import datetime
            import pytz
            
            # Aware local time, so %Z names the zone.
            current_time = datetime.now().astimezone()
            utc_time = datetime.now(pytz.UTC)
            
            time_info = {
                "local_time": current_time.strftime("%I:%M %p %Z on %A, %B %d, %Y"),
                "utc_time": utc_time.strftime("%I:%M %p UTC on %A, %B %d, %Y"),
                "timestamp": current_time.isoformat()
            }
            
            snippet = f"Current time: {time_info['local_time']}\nUTC time: {time_info['utc_time']}"
            
            return {
                "query": query,
                "strategy_used": "time_service",
                "success": True,
                "data": {
                    "type": "time_info",
                    "time_info": time_info,
                    "snippet": snippet,
                    "source": "System Clock"
                }
            }
        except Exception as e:
            logger.warning(f"Time query failed: {e}")
    
    location = _weather_place(normalised) if short else None
    if location:
        try:
            logger.info(f"Weather query detected for location: {location}")
            weather_result = get_weather_info(location)

            if weather_result.get("success"):
                temp_f = weather_result.get('temperature_fahrenheit', 'N/A')
                temp_c = weather_result.get('temperature_celsius', 'N/A')
                description = weather_result.get('description', 'N/A')
                humidity = weather_result.get('humidity', 'N/A')

                snippet = f"Current weather in {location}:\nTemperature: {temp_f}°F ({temp_c}°C)\nConditions: {description}\nHumidity: {humidity}%"

                return {
                    "query": query,
                    "strategy_used": "weather_service",
                    "success": True,
                    "data": {
                        "type": "weather",
                        "location": location,
                        "temperature_fahrenheit": temp_f,
                        "temperature_celsius": temp_c,
                        "description": description,
                        "humidity": humidity,
                        "snippet": snippet,
                        "source": weather_result.get("source") or WEATHER_SOURCE
                    }
                }
            logger.warning(f"Weather lookup failed for {location}: {weather_result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.warning(f"Weather query processing failed: {e}")

    math_expr = _arithmetic_expression(normalised) if short else None
    if math_expr:
        try:
            result = evaluate_arithmetic(math_expr)
            return {
                "query": query,
                "strategy_used": "math_calculation",
                "success": True,
                "data": {
                    "type": "calculation",
                    "expression": math_expr,
                    "result": result,
                    "snippet": f"Calculation: {math_expr} = {result}",
                    "source": "System Calculator"
                }
            }
        except Exception as e:
            logger.warning(f"Math calculation failed: {e}")

    return {
        "query": query,
        "success": False,
        "strategy_used": "none"
    }


def search_result_count(value: Any) -> int:
    """``value`` as a number of results to ask for, within 1..MAX_SEARCH_RESULTS."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SEARCH_RESULTS
    return max(1, min(count, MAX_SEARCH_RESULTS))


# How many times one search is asked before it is given up; each ask is a new
# browser session at SEARCH_ENGINE. A search the engine refused is not asked
# again. Measured 2026-11-16 on 30 queries the engine had not seen (see
# backend/utils/bing_search.py for why a page can miss the query): 16 were
# answered on the first ask, 10 on the second, 1 on the third, and 3 ended
# with the reason; a search took 1.4 s on average and 4.6 s at most.
_SEARCH_ATTEMPTS = 3


def _search_failure(exc: BaseException) -> tuple[str, bool]:
    """Why asking the search engine failed, in words a person or a model can act
    on, and whether the engine refused the search (so asking again at once is
    pointless). The query is never part of the reason."""
    from backend.utils import bing_search

    if isinstance(exc, bing_search.BingRefused):
        return (f"{SEARCH_ENGINE} refused the search (HTTP {exc.status}); it limits automated "
                "searches. Try again in a few minutes."), True
    if isinstance(exc, bing_search.BingTimeout):
        return f"{SEARCH_ENGINE} did not answer in time.", False
    if isinstance(exc, bing_search.BingUnreachable):
        return f"{SEARCH_ENGINE} could not be reached; check the network connection.", False
    if isinstance(exc, bing_search.BingNoResultList):
        return (f"{SEARCH_ENGINE} did not answer the search: it sent a page with no result list, "
                "as it does when it challenges an automated search. Try again in a few minutes."), False
    if isinstance(exc, bing_search.BingOffQuery):
        return (f"{SEARCH_ENGINE} did not answer this search: the results it sent were for a "
                "different one, as it sometimes does with a query it has not seen. Ask again, "
                "or use fewer, plainer words."), False
    if isinstance(exc, bing_search.BingError):
        return f"{SEARCH_ENGINE} answered the search with an error ({exc}).", False
    return f"The search could not be completed at {SEARCH_ENGINE}: {type(exc).__name__}.", False


def perform_duckduckgo_search(query: str, max_results: int = DEFAULT_SEARCH_RESULTS) -> Dict[str, Any]:
    """Search SEARCH_ENGINE for ``query``. The name is from an earlier search
    client and is kept for the callers that import it.

    ``success`` is True when the engine returned results for the query.
    Otherwise ``error`` says why there are none: ``no_results`` is True when
    the engine said it found nothing, and absent when it could not be asked or
    did not answer the query (refused, timed out, unreachable, a challenge
    page, results for a different search, or the HTTP client is not
    installed). The query goes nowhere else either way. ``source`` names the
    engine.
    """
    max_results = search_result_count(max_results)
    try:
        import primp  # noqa: F401
        from backend.utils import bing_search
    except ImportError:
        return {
            "success": False,
            "error": "Web search is not available: the primp package is not installed.",
            "results": [],
            "snippet": "",
            "source": SEARCH_ENGINE,
        }

    results = []
    search_snippets = []
    answered_empty = False
    failure = None
    for _attempt in range(_SEARCH_ATTEMPTS):
        try:
            search_rows = bing_search.search(query, max_results=max_results)
        except Exception as search_error:
            failure, refused = _search_failure(search_error)
            # The reason only: the exception text can carry the query.
            logger.warning(f"Web search ({SEARCH_ENGINE}) failed: {type(search_error).__name__}")
            if refused:
                break
            continue

        for row in search_rows:
            title = (row.get("title") or "").strip()
            url = row.get("href", "")
            snippet = (row.get("body") or "").strip()

            if title and (url or snippet):
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet[:300]
                })
                if snippet:
                    search_snippets.append(f"{title}: {snippet[:200]}")

        if results:
            break
        answered_empty = True

    if results:
        combined_snippet = "\n\n".join(search_snippets[:3]) if search_snippets else ""
        return {
            "success": True,
            "results": results,
            "snippet": f"Search results for '{query}':\n\n{combined_snippet}",
            "total_results": len(results),
            "source": SEARCH_ENGINE,
        }

    outcome = {"success": False, "results": [], "snippet": "", "source": SEARCH_ENGINE}
    if failure:
        # Any ask that failed outranks an ask that came back empty: "no
        # results" is said only when every answer was the engine's own.
        outcome["error"] = failure
    elif answered_empty:
        outcome["no_results"] = True
        outcome["error"] = f"{SEARCH_ENGINE} returned no results for this query."
    else:
        outcome["error"] = f"The search could not be completed at {SEARCH_ENGINE}."
    return outcome

@web_search_bp.route("/quick-search", methods=["POST"])
def quick_search():
    try:
        if not get_web_access():
            return error_response("Web search is disabled in system settings", status_code=403)
        
        data = request.get_json()
        if not data:
            return error_response("Request body must be JSON", status_code=400)
        
        query = data.get("query")
        if not query:
            return error_response("Query is required", status_code=400)
        
        logger.info(f"Enhanced quick search request received (query_len={len(query)})")
        
        search_results = enhanced_web_search(query)
        
        if search_results["success"]:
            result = {
                "query": query,
                "snippet": search_results["data"].get("snippet", ""),
                "source": search_results["data"].get("source", search_results["strategy_used"]),
                "url": search_results["data"].get("url", ""),
                "has_result": True,
                "strategy_used": search_results["strategy_used"],
                "data_type": search_results["data"].get("type", "unknown"),
                "timestamp": datetime.now().isoformat()
            }
            
            logger.info(f"Enhanced quick search successful using {search_results['strategy_used']}")
            return success_response(result)
        else:
            result = {
                "query": query,
                "snippet": "",
                "source": "",
                "url": "",
                "has_result": False,
                "strategy_used": search_results["strategy_used"],
                "message": search_results["data"].get("message", "No results found"),
                "attempted_strategies": search_results["data"].get("attempted_strategies", []),
                "errors": search_results["data"].get("errors", {}),
                "timestamp": datetime.now().isoformat()
            }
            
            logger.warning(f"Enhanced quick search failed (query_len={len(query)})")
            return success_response(result)
            
    except Exception as e:
        logger.error(f"Error in enhanced quick search: {e}", exc_info=True)
        return error_response(f"Search failed: {str(e)}", status_code=500)

@web_search_bp.route("/search", methods=["POST"])
def web_search():
    try:
        if not get_web_access():
            return error_response("Web search is disabled in system settings", status_code=403)
        
        data = request.get_json()
        if not data:
            return error_response("Request body must be JSON", status_code=400)
        
        query = data.get("query")
        if not query:
            return error_response("Query is required", status_code=400)
        
        logger.info(f"Enhanced web search request received (query_len={len(query)})")
        
        search_results = enhanced_web_search(query)
        
        return success_response(search_results)
            
    except Exception as e:
        logger.error(f"Error in enhanced web search: {e}", exc_info=True)
        return error_response(f"Search failed: {str(e)}", status_code=500)

@web_search_bp.route("/sitemap", methods=["POST"])
def sitemap_report():
    """Summarise one sitemap (the chat's ``/websearch sitemap:<url>``); see read_sitemap."""
    try:
        if not get_web_access():
            return error_response("Web access is disabled in system settings", status_code=403)

        data = request.get_json(silent=True) or {}
        url = str(data.get("url") or "").strip()
        if not url:
            return error_response("A sitemap URL is required", status_code=400)

        report = read_sitemap(url)
        if not report["success"]:
            return error_response(report["error"], status_code=422, error_code="SITEMAP_NOT_READ",
                                  data={"url": report["url"]})
        return success_response(report)

    except Exception as e:
        logger.error(f"Error reading sitemap: {e}", exc_info=True)
        return error_response(f"Reading the sitemap failed: {str(e)}", status_code=500)

@web_search_bp.route("/status", methods=["GET"])
def search_status():
    try:
        web_enabled = get_web_access()

        # Probe the REAL building blocks — import + callable only, NO network I/O
        # (a status poll must never hammer the search engine or the weather
        # service; see SSRF/DOS trap). Each of these is a module-level function
        # in this file. The "duckduckgo_search" keys are an older name, kept
        # for callers that read them.
        probes = {
            "website_scraping": callable(globals().get("extract_website_content")),
            "duckduckgo_search": callable(globals().get("perform_duckduckgo_search")),
            "weather_api": callable(globals().get("get_weather_info")),
        }

        def _svc_state(code_ok):
            if not code_ok:
                return "unavailable"            # code path missing/broken
            return "available" if web_enabled else "disabled_by_policy"

        services = {name: _svc_state(ok) for name, ok in probes.items()}

        # Capabilities reflect what can ACTUALLY run now: the code exists AND web
        # access is enabled by policy. With web off, they're policy-disabled, not True.
        capabilities = {
            "website_analysis": bool(web_enabled and probes["website_scraping"]),
            "general_search": bool(web_enabled and probes["duckduckgo_search"]),
            "weather_lookup": bool(web_enabled and probes["weather_api"]),
        }

        if not web_enabled:
            service_status = "disabled_by_policy"
        elif capabilities["general_search"] and capabilities["website_analysis"]:
            service_status = "operational"
        elif capabilities["website_analysis"] or capabilities["general_search"]:
            service_status = "limited"
        else:
            service_status = "unavailable"
        
        return success_response({
            "web_search_enabled": web_enabled,
            "service_status": service_status,
            "capabilities": capabilities,
            "services": services,
            "timestamp": datetime.now().isoformat(),
            "search_strategies": [
                "direct_website", 
                "duckduckgo_search"
            ],
            "reliability_notes": {
                "direct_website": "Reliable for specific URLs",
                "duckduckgo_search": (
                    f"General search: {SEARCH_ENGINE}'s public result page. "
                    "When it finds nothing, cannot be reached or does not answer the query the search says so; "
                    "the query is not sent anywhere else"
                )
            }
        })
        
    except Exception as e:
        logger.error(f"Error checking search status: {e}")
        return error_response(f"Status check failed: {str(e)}", status_code=500) 
