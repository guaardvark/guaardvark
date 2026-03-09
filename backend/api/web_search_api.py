
import logging
import json
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import requests
from urllib.parse import quote_plus, urlparse, parse_qs
from bs4 import BeautifulSoup

from flask import Blueprint, current_app, jsonify, request
from backend.utils.response_utils import success_response, error_response
from backend.utils.settings_utils import get_web_access

web_search_bp = Blueprint("web_search_api", __name__, url_prefix="/api/web-search")
logger = logging.getLogger(__name__)

def extract_website_content(url: str) -> Dict[str, Any]:
    try:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser', from_encoding='utf-8')
        
        for script in soup(["script", "style", "nav", "footer", "aside"]):
            script.decompose()
        
        title = soup.find('title')
        title_text = title.get_text().strip() if title else "No title found"
        
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
        content_text = content_text[:2000]
        
        return {
            "success": True,
            "url": url,
            "title": title_text,
            "description": description,
            "content": content_text,
            "content_length": len(content_text)
        }
        
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

def get_weather_info(location: str) -> Dict[str, Any]:
    try:
        
        weather_apis = [
            f"https://wttr.in/{quote_plus(location)}?format=j1",
        ]
        
        for api_url in weather_apis:
            try:
                headers = {'User-Agent': 'LLM003-Weather/1.0'}
                response = requests.get(api_url, headers=headers, timeout=10)
                
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
                            "source": "wttr.in"
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

def enhanced_web_search(query: str) -> Dict[str, Any]:
    
    results = {
        "query": query,
        "strategy_used": "",
        "success": False,
        "data": {}
    }
    
    special_result = handle_special_queries(query)
    if special_result["success"]:
        return special_result
    
    url_pattern = r'(?:https?://|www\.)[^\s]+'
    urls = re.findall(url_pattern, query.lower())
    
    if urls:
        url = urls[0]
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            
        logger.info(f"Direct website access for: {url}")
        website_data = extract_website_content(url)
        
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
            return results
        else:
            results["data"]["website_error"] = website_data["error"]
    
    logger.info(f"Performing DuckDuckGo search for: {query}")
    ddg_results = perform_duckduckgo_search(query)
    
    if ddg_results["success"]:
        results.update({
            "strategy_used": "duckduckgo_search",
            "success": True,
            "data": {
                "type": "search_results",
                "results": ddg_results["results"],
                "snippet": ddg_results["snippet"],
                "total_results": ddg_results["total_results"],
                "source": "DuckDuckGo"
            }
        })
        return results
    
    results["data"]["duckduckgo_error"] = ddg_results.get("error", "Unknown error")
    return {
        "query": query,
        "strategy_used": "failed",
        "success": False,
        "data": {
            "type": "search_failed",
            "message": f"Unable to find current information for: {query}",
            "errors": results["data"],
            "attempted_strategies": ["duckduckgo_search"] + (["direct_website"] if urls else [])
        }
    }


def handle_special_queries(query: str) -> Dict[str, Any]:
    query_lower = query.lower().strip()
    
    if any(keyword in query_lower for keyword in ['time', 'clock', 'current time', 'what time']):
        try:
            from datetime import datetime
            import pytz
            
            current_time = datetime.now()
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
    
    weather_keywords = ['weather', 'temperature', 'forecast', 'climate', 'how hot', 'how cold', 'degrees']
    location_keywords = ['in ', 'at ', 'for ']
    
    if any(keyword in query_lower for keyword in weather_keywords):
        try:
            import re
            location = None
            
            location_patterns = [
                r'(?:weather|temperature|forecast|climate).*?(?:in|at|for)\s+([^?]+?)(?:\?|$)',
                r'(?:what.*?)(?:weather|temperature).*?(?:in|at|for)\s+([^?]+?)(?:\?|$)',
                r'(?:how\s+hot|how\s+cold).*?(?:in|at|for)\s+([^?]+?)(?:\?|$)',
                r'current\s+temperature\s+(?:in|at|for)\s+([^?]+?)(?:\?|$)'
            ]
            
            for pattern in location_patterns:
                match = re.search(pattern, query_lower)
                if match:
                    location = match.group(1).strip()
                    location = re.sub(r'\b(right\s+now|now|currently|today|tonight)\b', '', location, flags=re.IGNORECASE).strip()
                    location = re.sub(r',\s*$', '', location).strip()
                    break
            
            if not location:
                words = query_lower.split()
                if len(words) >= 2:
                    location = ' '.join(words[-2:])
                    
                    location = re.sub(r'\b(what|is|the|current)\b', '', location).strip()
            
            if location and len(location) > 1:
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
                            "source": "Weather API"
                        }
                    }
                else:
                    logger.warning(f"Weather lookup failed for {location}: {weather_result.get('error', 'Unknown error')}")
            else:
                logger.warning(f"Could not extract location from weather query: {query}")
        except Exception as e:
            logger.warning(f"Weather query processing failed: {e}")
    
    if any(keyword in query_lower for keyword in ['calculate', 'math', 'equation', '=']) and any(op in query for op in ['+', '-', '*', '/', '=']):
        try:
            import re
            math_expr = re.sub(r'[^0-9+\-*/.() ]', '', query)
            if math_expr.strip():
                result = eval(math_expr.strip())
                return {
                    "query": query,
                    "strategy_used": "math_calculation",
                    "success": True,
                    "data": {
                        "type": "calculation",
                        "expression": math_expr.strip(),
                        "result": result,
                        "snippet": f"Calculation: {math_expr.strip()} = {result}",
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


def perform_duckduckgo_search(query: str) -> Dict[str, Any]:
    try:
        from duckduckgo_search import DDGS

        results = []
        search_snippets = []
        last_error = None
        for backend in ("lite", "html"):
            try:
                with DDGS() as ddgs:
                    search_rows = ddgs.text(query, backend=backend, max_results=5)

                for row in search_rows:
                    title = (row.get("title") or "").strip()
                    url = row.get("href", "")
                    snippet = (row.get("body") or row.get("snippet") or "").strip()

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
            except Exception as backend_error:
                last_error = str(backend_error)
                logger.warning(f"DuckDuckGo {backend} backend failed: {backend_error}")
                continue

        if not results:
            try:
                proxy_url = f"https://r.jina.ai/http://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
                headers = {"User-Agent": "guaardvark-web-search/1.0"}
                resp = requests.get(proxy_url, headers=headers, timeout=10)
                resp.raise_for_status()

                lines = resp.text.splitlines()
                for idx, line in enumerate(lines):
                    match = re.match(r"\d+\.\[(.+?)\]\((.+?)\)", line.strip())
                    if not match:
                        continue

                    title = match.group(1).strip()
                    url = match.group(2).strip()

                    parsed = urlparse(url)
                    query_params = parse_qs(parsed.query)
                    uddg_target = query_params.get("uddg", [])
                    if uddg_target:
                        url = uddg_target[0]

                    snippet = ""
                    if idx + 1 < len(lines):
                        candidate = lines[idx + 1].strip()
                        if candidate and not candidate.startswith("Markdown Content"):
                            snippet = candidate[:300]

                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet
                    })
                    if snippet:
                        search_snippets.append(f"{title}: {snippet}")
                    if len(results) >= 5:
                        break
            except Exception as proxy_error:
                last_error = last_error or str(proxy_error)
                logger.warning(f"DuckDuckGo proxy fallback failed: {proxy_error}")

        if results:
            combined_snippet = "\n\n".join(search_snippets[:3]) if search_snippets else ""
            return {
                "success": True,
                "results": results,
                "snippet": f"Search results for '{query}':\n\n{combined_snippet}",
                "total_results": len(results)
            }

        return {
            "success": False,
            "error": last_error or "No search results found",
            "results": [],
            "snippet": ""
        }

    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {e}")
        return {
            "success": False,
            "error": f"DuckDuckGo search error: {str(e)}",
            "results": [],
            "snippet": ""
        }

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
        
        logger.info(f"Enhanced quick search request: {query[:100]}...")
        
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
            
            logger.warning(f"Enhanced quick search failed for: {query[:50]}...")
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
        
        logger.info(f"Enhanced web search request: {query[:100]}...")
        
        search_results = enhanced_web_search(query)
        
        return success_response(search_results)
            
    except Exception as e:
        logger.error(f"Error in enhanced web search: {e}", exc_info=True)
        return error_response(f"Search failed: {str(e)}", status_code=500)

@web_search_bp.route("/status", methods=["GET"])
def search_status():
    try:
        web_enabled = get_web_access()
        
        capabilities = {
            "website_analysis": True,
            "general_search": True,
            "weather_lookup": True
        }
        
        services = {
            "website_scraping": "available",
            "duckduckgo_search": "available",
            "weather_api": "available"
        }
        
        if capabilities["general_search"] and capabilities["website_analysis"]:
            service_status = "operational"
        elif capabilities["website_analysis"]:
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
                "duckduckgo_search": "Primary general search provider"
            }
        })
        
    except Exception as e:
        logger.error(f"Error checking search status: {e}")
        return error_response(f"Status check failed: {str(e)}", status_code=500) 
