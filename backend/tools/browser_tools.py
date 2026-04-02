#!/usr/bin/env python3

import asyncio
import logging
import threading
from typing import Any, Dict

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.browser_automation_service import (
    get_browser_service,
    run_browser_action,
    BROWSER_AUTOMATION_ENABLED
)

logger = logging.getLogger(__name__)

_browser_loop = None
_browser_loop_thread = None
_browser_loop_lock = threading.Lock()


def _get_browser_loop():
    global _browser_loop, _browser_loop_thread
    with _browser_loop_lock:
        if _browser_loop is None or _browser_loop.is_closed():
            _browser_loop = asyncio.new_event_loop()
            _browser_loop_thread = threading.Thread(
                target=_browser_loop.run_forever, daemon=True
            )
            _browser_loop_thread.start()
    return _browser_loop


def _run_async(coro):
    loop = _get_browser_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=60)


class BrowserNavigateTool(BaseTool):
    
    name = "browser_navigate"
    description = "Navigate browser to a URL. Can wait for page load, network idle, or specific element."
    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description="URL to navigate to (must include protocol, e.g., https://)"
        ),
        "wait_for": ToolParameter(
            name="wait_for",
            type="string",
            required=False,
            description="What to wait for: 'load', 'networkidle', 'domcontentloaded', or a CSS selector",
            default="load"
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        url = kwargs.get("url")
        wait_for = kwargs.get("wait_for", "load")

        if not url:
            return ToolResult(success=False, error="URL is required")

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # If agent virtual display is active, navigate via xdotool on :99
        try:
            from backend.utils.agent_display_utils import is_agent_display_active
            if is_agent_display_active():
                return self._navigate_on_agent_display(url)
        except Exception as e:
            logger.debug(f"Agent display check failed, using headless: {e}")

        if not BROWSER_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Browser not available. Use 'analyze_website' or 'web_search' instead."
            )

        # Check web access setting — allow localhost/local network even when web access is off
        from urllib.parse import urlparse
        parsed = urlparse(url)
        is_local = parsed.hostname in ("localhost", "127.0.0.1", "0.0.0.0") or \
                   (parsed.hostname and (parsed.hostname.startswith("192.168.") or
                    parsed.hostname.startswith("10.") or parsed.hostname.startswith("172.")))
        if not is_local:
            try:
                from flask import has_app_context
                from backend.utils.settings_utils import get_web_access
                if has_app_context() and not get_web_access():
                    return ToolResult(
                        success=False,
                        error="Web access is disabled. Enable it in Settings to browse external sites."
                    )
            except Exception:
                pass

        try:
            service = get_browser_service()
            result = _run_async(service.navigate(url, wait_for=wait_for))

            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Navigated to {result.get('url')}\nTitle: {result.get('title')}",
                    metadata=result
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.get("error", "Navigation failed"),
                    metadata=result
                )

        except Exception as e:
            logger.error(f"Browser navigate error: {e}")
            return ToolResult(success=False, error=str(e))

    def _navigate_on_agent_display(self, url: str) -> ToolResult:
        """Navigate via keyboard shortcuts on the agent's virtual display (:99).

        Uses the same proven sequence as the navigate_url recipe:
        Escape → Ctrl+L → Ctrl+A → type URL → Enter
        """
        import time

        from backend.utils.agent_display_utils import (
            is_firefox_on_agent_display, wait_for_firefox_on_display
        )
        from backend.services.local_screen_backend import LocalScreenBackend

        screen = LocalScreenBackend()

        # If Firefox isn't running on :99, launch it first
        if not is_firefox_on_agent_display():
            logger.info("Firefox not on agent display, launching...")
            try:
                from backend.services.desktop_automation_service import get_desktop_service
                service = get_desktop_service()
                launch_result = service.app_launch("firefox")
                if not launch_result.get("success"):
                    return ToolResult(
                        success=False,
                        error=f"Failed to launch Firefox: {launch_result.get('error')}"
                    )
            except Exception as e:
                return ToolResult(success=False, error=f"Failed to launch Firefox: {e}")

            if not wait_for_firefox_on_display(timeout=8.0):
                return ToolResult(
                    success=False,
                    error="Firefox launched but did not appear on agent display within 8 seconds"
                )
            time.sleep(1)  # Let Firefox finish initializing

        # Navigate using keyboard shortcuts (proven recipe pattern)
        screen.hotkey("Escape")
        time.sleep(0.3)
        screen.hotkey("ctrl", "l")
        time.sleep(0.5)
        screen.hotkey("ctrl", "a")
        time.sleep(0.2)
        screen.type_text(url)
        time.sleep(0.3)
        screen.hotkey("Return")
        time.sleep(2)

        logger.info(f"Navigated to {url} on agent display")
        return ToolResult(
            success=True,
            output=f"Navigated to {url} on agent display (:99)",
            metadata={"url": url, "display": ":99", "method": "xdotool"}
        )


class BrowserClickTool(BaseTool):
    
    name = "browser_click"
    description = "Click an element on a web page using CSS or XPath selector."
    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description="URL of the page"
        ),
        "selector": ToolParameter(
            name="selector",
            type="string",
            required=True,
            description="CSS selector or XPath (prefix with 'xpath=' for XPath)"
        ),
        "timeout": ToolParameter(
            name="timeout",
            type="int",
            required=False,
            description="Timeout in milliseconds",
            default=30000
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not BROWSER_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Browser not available. Use 'analyze_website' or 'web_search' instead."
            )
        
        url = kwargs.get("url")
        selector = kwargs.get("selector")
        timeout = kwargs.get("timeout", 30000)
        
        if not url or not selector:
            return ToolResult(success=False, error="URL and selector are required")
        
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        try:
            service = get_browser_service()
            result = _run_async(service.click(url, selector, timeout=timeout))
            
            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Clicked element '{selector}' on {result.get('url')}",
                    metadata=result
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.get("error", "Click failed"),
                    metadata=result
                )
                
        except Exception as e:
            logger.error(f"Browser click error: {e}")
            return ToolResult(success=False, error=str(e))


class BrowserFillTool(BaseTool):
    
    name = "browser_fill"
    description = "Fill a form input field with a value. Can optionally submit the form."
    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description="URL of the page"
        ),
        "selector": ToolParameter(
            name="selector",
            type="string",
            required=True,
            description="CSS selector for the input field"
        ),
        "value": ToolParameter(
            name="value",
            type="string",
            required=True,
            description="Value to fill into the field"
        ),
        "submit": ToolParameter(
            name="submit",
            type="bool",
            required=False,
            description="Whether to submit the form after filling",
            default=False
        ),
        "submit_selector": ToolParameter(
            name="submit_selector",
            type="string",
            required=False,
            description="Optional CSS selector for submit button (if submit=true)"
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not BROWSER_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Browser not available. Use 'analyze_website' or 'web_search' instead."
            )
        
        url = kwargs.get("url")
        selector = kwargs.get("selector")
        value = kwargs.get("value")
        submit = kwargs.get("submit", False)
        submit_selector = kwargs.get("submit_selector")
        
        if not url or not selector or value is None:
            return ToolResult(success=False, error="URL, selector, and value are required")
        
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        try:
            service = get_browser_service()
            result = _run_async(service.fill_form(
                url, selector, value,
                submit=submit,
                submit_selector=submit_selector
            ))
            
            if result.get("success"):
                msg = f"Filled '{selector}' with value"
                if submit:
                    msg += " and submitted form"
                return ToolResult(
                    success=True,
                    output=msg,
                    metadata=result
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.get("error", "Fill failed"),
                    metadata=result
                )
                
        except Exception as e:
            logger.error(f"Browser fill error: {e}")
            return ToolResult(success=False, error=str(e))


class BrowserScreenshotTool(BaseTool):
    
    name = "browser_screenshot"
    description = "Capture a screenshot of a web page or specific element. Returns base64-encoded image."
    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description="URL to screenshot"
        ),
        "full_page": ToolParameter(
            name="full_page",
            type="bool",
            required=False,
            description="Capture the full scrollable page",
            default=False
        ),
        "selector": ToolParameter(
            name="selector",
            type="string",
            required=False,
            description="Optional CSS selector to screenshot specific element"
        ),
        "format": ToolParameter(
            name="format",
            type="string",
            required=False,
            description="Image format: 'png' or 'jpeg'",
            default="png"
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not BROWSER_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Browser not available. Use 'analyze_website' or 'web_search' instead."
            )
        
        url = kwargs.get("url")
        full_page = kwargs.get("full_page", False)
        selector = kwargs.get("selector")
        format = kwargs.get("format", "png")
        
        if not url:
            return ToolResult(success=False, error="URL is required")
        
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        try:
            service = get_browser_service()
            result = _run_async(service.screenshot(
                url,
                full_page=full_page,
                selector=selector,
                format=format
            ))
            
            if result.get("success"):
                img_preview = result.get("image_base64", "")[:50] + "..."
                return ToolResult(
                    success=True,
                    output=f"Screenshot captured ({format}, full_page={full_page})",
                    metadata={
                        "image_base64": result.get("image_base64"),
                        "format": format,
                        "url": result.get("url"),
                        "full_page": full_page
                    }
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.get("error", "Screenshot failed"),
                    metadata=result
                )
                
        except Exception as e:
            logger.error(f"Browser screenshot error: {e}")
            return ToolResult(success=False, error=str(e))


class BrowserExtractTool(BaseTool):
    
    name = "browser_extract"
    description = "Extract text content or attribute values from page elements. Useful for scraping data."
    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description="URL of the page"
        ),
        "selector": ToolParameter(
            name="selector",
            type="string",
            required=True,
            description="CSS selector for elements to extract from"
        ),
        "attribute": ToolParameter(
            name="attribute",
            type="string",
            required=False,
            description="Attribute to extract (e.g., 'href', 'src'). If not specified, extracts text content."
        ),
        "multiple": ToolParameter(
            name="multiple",
            type="bool",
            required=False,
            description="Extract from all matching elements (returns list)",
            default=False
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not BROWSER_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Browser not available. Use 'analyze_website' or 'web_search' instead."
            )
        
        url = kwargs.get("url")
        selector = kwargs.get("selector")
        attribute = kwargs.get("attribute")
        multiple = kwargs.get("multiple", False)
        
        if not url or not selector:
            return ToolResult(success=False, error="URL and selector are required")
        
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        try:
            service = get_browser_service()
            result = _run_async(service.extract(
                url, selector,
                attribute=attribute,
                multiple=multiple
            ))
            
            if result.get("success"):
                data = result.get("data")
                if multiple:
                    output = f"Extracted {result.get('count', 0)} items from '{selector}'"
                else:
                    output = f"Extracted from '{selector}': {data[:200] if isinstance(data, str) else data}"
                
                return ToolResult(
                    success=True,
                    output=output,
                    metadata=result
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.get("error", "Extraction failed"),
                    metadata=result
                )
                
        except Exception as e:
            logger.error(f"Browser extract error: {e}")
            return ToolResult(success=False, error=str(e))


class BrowserWaitTool(BaseTool):
    
    name = "browser_wait"
    description = "Wait for an element to be visible, hidden, attached, or detached from the DOM."
    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description="URL of the page"
        ),
        "selector": ToolParameter(
            name="selector",
            type="string",
            required=True,
            description="CSS selector for the element"
        ),
        "state": ToolParameter(
            name="state",
            type="string",
            required=False,
            description="State to wait for: 'visible', 'hidden', 'attached', 'detached'",
            default="visible"
        ),
        "timeout": ToolParameter(
            name="timeout",
            type="int",
            required=False,
            description="Timeout in milliseconds",
            default=30000
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not BROWSER_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Browser not available. Use 'analyze_website' or 'web_search' instead."
            )
        
        url = kwargs.get("url")
        selector = kwargs.get("selector")
        state = kwargs.get("state", "visible")
        timeout = kwargs.get("timeout", 30000)
        
        if not url or not selector:
            return ToolResult(success=False, error="URL and selector are required")
        
        if state in ("networkidle", "load", "domcontentloaded"):
            state = "visible"

        if state not in ("visible", "hidden", "attached", "detached"):
            return ToolResult(
                success=False,
                error=f"Invalid state '{state}'. Must be: visible, hidden, attached, detached"
            )
        
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        try:
            service = get_browser_service()
            result = _run_async(service.wait_for(url, selector, state=state, timeout=timeout))
            
            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Element '{selector}' is now {state}",
                    metadata=result
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.get("error", "Wait timed out"),
                    metadata=result
                )
                
        except Exception as e:
            logger.error(f"Browser wait error: {e}")
            return ToolResult(success=False, error=str(e))


class BrowserExecuteJSTool(BaseTool):
    
    name = "browser_execute_js"
    description = "Execute JavaScript code on a page and return the result. Useful for complex interactions."
    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description="URL of the page"
        ),
        "script": ToolParameter(
            name="script",
            type="string",
            required=True,
            description="JavaScript code to execute (returns last expression value)"
        ),
        "args": ToolParameter(
            name="args",
            type="list",
            required=False,
            description="Optional arguments to pass to the script"
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not BROWSER_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Browser not available. Use 'analyze_website' or 'web_search' instead."
            )
        
        url = kwargs.get("url")
        script = kwargs.get("script")
        args = kwargs.get("args")
        
        if not url or not script:
            return ToolResult(success=False, error="URL and script are required")
        
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        try:
            service = get_browser_service()
            result = _run_async(service.execute_js(url, script, args=args))
            
            if result.get("success"):
                js_result = result.get("result")
                return ToolResult(
                    success=True,
                    output=f"JavaScript executed. Result: {js_result}",
                    metadata=result
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.get("error", "JavaScript execution failed"),
                    metadata=result
                )
                
        except Exception as e:
            logger.error(f"Browser execute JS error: {e}")
            return ToolResult(success=False, error=str(e))


class BrowserGetHTMLTool(BaseTool):
    
    name = "browser_get_html"
    description = "Get the HTML content of a page or specific element. Useful for scraping rendered content."
    parameters = {
        "url": ToolParameter(
            name="url",
            type="string",
            required=True,
            description="URL of the page"
        ),
        "selector": ToolParameter(
            name="selector",
            type="string",
            required=False,
            description="Optional CSS selector for specific element (returns full page if not specified)"
        ),
        "outer": ToolParameter(
            name="outer",
            type="bool",
            required=False,
            description="If True, get outerHTML (includes element tag); if False, get innerHTML",
            default=True
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not BROWSER_AUTOMATION_ENABLED:
            return ToolResult(
                success=False,
                error="Browser not available. Use 'analyze_website' or 'web_search' instead."
            )
        
        url = kwargs.get("url")
        selector = kwargs.get("selector")
        outer = kwargs.get("outer", True)
        
        if not url:
            return ToolResult(success=False, error="URL is required")
        
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        try:
            service = get_browser_service()
            result = _run_async(service.get_html(url, selector=selector, outer=outer))
            
            if result.get("success"):
                html = result.get("html", "")
                preview = html[:500] + "..." if len(html) > 500 else html
                
                return ToolResult(
                    success=True,
                    output=f"Retrieved HTML ({len(html)} chars) from {result.get('url')}",
                    metadata={
                        "html": html,
                        "url": result.get("url"),
                        "selector": selector,
                        "length": len(html)
                    }
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.get("error", "Failed to get HTML"),
                    metadata=result
                )
                
        except Exception as e:
            logger.error(f"Browser get HTML error: {e}")
            return ToolResult(success=False, error=str(e))


__all__ = [
    "BrowserNavigateTool",
    "BrowserClickTool",
    "BrowserFillTool",
    "BrowserScreenshotTool",
    "BrowserExtractTool",
    "BrowserWaitTool",
    "BrowserExecuteJSTool",
    "BrowserGetHTMLTool",
]
