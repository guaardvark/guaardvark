#!/usr/bin/env python3

import asyncio
import base64
import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

BROWSER_AUTOMATION_ENABLED = os.getenv("GUAARDVARK_BROWSER_AUTOMATION", "true").lower() == "true"
BROWSER_HEADLESS = os.getenv("GUAARDVARK_BROWSER_HEADLESS", "true").lower() == "true"
MAX_PAGES = int(os.getenv("GUAARDVARK_BROWSER_MAX_PAGES", "5"))
PAGE_TIMEOUT = int(os.getenv("GUAARDVARK_BROWSER_TIMEOUT", "30000"))
IDLE_SHUTDOWN_SECONDS = int(os.getenv("GUAARDVARK_BROWSER_IDLE_SHUTDOWN", "300"))


@dataclass
class PageInfo:
    page: Any
    created_at: datetime
    last_used: datetime
    url: str = ""
    in_use: bool = False


@dataclass
class BrowserState:
    initialized: bool = False
    browser_type: str = "chromium"
    headless: bool = True
    active_pages: int = 0
    max_pages: int = MAX_PAGES
    total_navigations: int = 0
    total_screenshots: int = 0
    errors: List[str] = field(default_factory=list)


class BrowserAutomationService:
    
    _instance: Optional["BrowserAutomationService"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    @classmethod
    def get_instance(cls) -> "BrowserAutomationService":
        return cls()
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._playwright = None
        self._browser = None
        self._context = None
        self._pages: Dict[int, PageInfo] = {}
        self._page_counter = 0
        self._async_lock = None
        self._event_loop = None
        self._state = BrowserState()
        self._last_activity = None
        self._idle_timer = None

        logger.info("BrowserAutomationService initialized (lazy - browser not started yet)")
    
    def _ensure_event_loop(self) -> asyncio.AbstractEventLoop:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    
    async def _ensure_async_lock(self):
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock
    
    async def _start_browser(self) -> bool:
        if not BROWSER_AUTOMATION_ENABLED:
            logger.warning("Browser automation is disabled via GUAARDVARK_BROWSER_AUTOMATION=false")
            return False
        
        if self._browser is not None:
            return True
        
        lock = await self._ensure_async_lock()
        async with lock:
            if self._browser is not None:
                return True
            
            try:
                from playwright.async_api import async_playwright
                
                logger.info(f"Starting Playwright browser (headless={BROWSER_HEADLESS})")
                
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=BROWSER_HEADLESS,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ]
                )
                self._context = await self._browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                
                self._state.initialized = True
                self._state.headless = BROWSER_HEADLESS
                logger.info("Playwright browser started successfully")
                return True
                
            except ImportError:
                error = "Playwright not installed. Run: pip install playwright && playwright install chromium"
                logger.error(error)
                self._state.errors.append(error)
                return False
            except Exception as e:
                error = f"Failed to start Playwright browser: {e}"
                logger.error(error)
                self._state.errors.append(error)
                return False
    
    async def _create_page(self) -> Optional[Any]:
        if not await self._start_browser():
            return None
        
        if len(self._pages) >= MAX_PAGES:
            await self._cleanup_unused_pages()
            
            if len(self._pages) >= MAX_PAGES:
                logger.warning(f"Maximum pages ({MAX_PAGES}) reached, cannot create new page")
                return None
        
        try:
            page = await self._context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT)
            
            self._page_counter += 1
            page_id = self._page_counter
            
            self._pages[page_id] = PageInfo(
                page=page,
                created_at=datetime.now(),
                last_used=datetime.now(),
                in_use=True
            )
            
            self._state.active_pages = len(self._pages)
            logger.debug(f"Created new page (id={page_id}, total={len(self._pages)})")
            
            return page
            
        except Exception as e:
            logger.error(f"Failed to create page: {e}")
            return None
    
    async def _cleanup_unused_pages(self, max_age_seconds: int = 300):
        now = datetime.now()
        to_remove = []
        
        for page_id, info in self._pages.items():
            if not info.in_use:
                age = (now - info.last_used).total_seconds()
                if age > max_age_seconds:
                    to_remove.append(page_id)
        
        for page_id in to_remove:
            try:
                await self._pages[page_id].page.close()
                del self._pages[page_id]
                logger.debug(f"Cleaned up unused page {page_id}")
            except Exception as e:
                logger.warning(f"Error closing page {page_id}: {e}")
        
        self._state.active_pages = len(self._pages)
    
    @asynccontextmanager
    async def get_page(self):
        page = await self._create_page()
        if page is None:
            raise RuntimeError("Failed to create browser page. Check logs for details.")

        page_id = None
        for pid, info in self._pages.items():
            if info.page == page:
                page_id = pid
                break

        try:
            yield page
        finally:
            if page_id and page_id in self._pages:
                try:
                    await self._pages[page_id].page.close()
                except Exception:
                    pass
                del self._pages[page_id]
                self._state.active_pages = len(self._pages)
                logger.debug(f"Closed page {page_id} after use (remaining={len(self._pages)})")
            self._reset_idle_timer()
    
    
    async def navigate(
        self,
        url: str,
        wait_for: Optional[str] = None,
        timeout: int = PAGE_TIMEOUT
    ) -> Dict[str, Any]:
        async with self.get_page() as page:
            try:
                wait_until = "load"
                if wait_for in ("networkidle", "domcontentloaded", "load", "commit"):
                    wait_until = wait_for
                    wait_for = None
                
                response = await page.goto(url, wait_until=wait_until, timeout=timeout)
                
                if wait_for:
                    await page.wait_for_selector(wait_for, timeout=timeout)
                
                self._state.total_navigations += 1
                
                return {
                    "success": True,
                    "url": page.url,
                    "title": await page.title(),
                    "status": response.status if response else None
                }
                
            except Exception as e:
                logger.error(f"Navigation failed: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "url": url
                }
    
    async def click(
        self,
        url: str,
        selector: str,
        timeout: int = PAGE_TIMEOUT
    ) -> Dict[str, Any]:
        async with self.get_page() as page:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                await page.click(selector, timeout=timeout)
                
                return {
                    "success": True,
                    "selector": selector,
                    "url": page.url
                }
                
            except Exception as e:
                logger.error(f"Click failed: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "selector": selector
                }
    
    async def fill_form(
        self,
        url: str,
        selector: str,
        value: str,
        submit: bool = False,
        submit_selector: Optional[str] = None,
        timeout: int = PAGE_TIMEOUT
    ) -> Dict[str, Any]:
        async with self.get_page() as page:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                await page.fill(selector, value, timeout=timeout)
                
                if submit:
                    if submit_selector:
                        await page.click(submit_selector, timeout=timeout)
                    else:
                        await page.press(selector, "Enter")
                    await page.wait_for_load_state("domcontentloaded")
                
                return {
                    "success": True,
                    "selector": selector,
                    "submitted": submit,
                    "url": page.url
                }
                
            except Exception as e:
                logger.error(f"Fill failed: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "selector": selector
                }
    
    async def screenshot(
        self,
        url: str,
        full_page: bool = False,
        selector: Optional[str] = None,
        format: str = "png",
        timeout: int = PAGE_TIMEOUT
    ) -> Dict[str, Any]:
        async with self.get_page() as page:
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout)
                
                screenshot_options = {
                    "type": format,
                    "full_page": full_page and not selector
                }
                
                if selector:
                    element = await page.wait_for_selector(selector, timeout=timeout)
                    screenshot_bytes = await element.screenshot(**screenshot_options)
                else:
                    screenshot_bytes = await page.screenshot(**screenshot_options)
                
                self._state.total_screenshots += 1
                
                return {
                    "success": True,
                    "image_base64": base64.b64encode(screenshot_bytes).decode("utf-8"),
                    "format": format,
                    "url": page.url,
                    "full_page": full_page
                }
                
            except Exception as e:
                logger.error(f"Screenshot failed: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "url": url
                }
    
    async def extract(
        self,
        url: str,
        selector: str,
        attribute: Optional[str] = None,
        multiple: bool = False,
        timeout: int = PAGE_TIMEOUT
    ) -> Dict[str, Any]:
        async with self.get_page() as page:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                
                if multiple:
                    elements = await page.query_selector_all(selector)
                    results = []
                    for el in elements:
                        if attribute:
                            value = await el.get_attribute(attribute)
                        else:
                            value = await el.text_content()
                        results.append(value)
                    
                    return {
                        "success": True,
                        "data": results,
                        "count": len(results),
                        "selector": selector
                    }
                else:
                    element = await page.wait_for_selector(selector, timeout=timeout)
                    if attribute:
                        value = await element.get_attribute(attribute)
                    else:
                        value = await element.text_content()
                    
                    return {
                        "success": True,
                        "data": value,
                        "selector": selector
                    }
                
            except Exception as e:
                logger.error(f"Extract failed: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "selector": selector
                }
    
    async def wait_for(
        self,
        url: str,
        selector: str,
        state: str = "visible",
        timeout: int = PAGE_TIMEOUT
    ) -> Dict[str, Any]:
        async with self.get_page() as page:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                await page.wait_for_selector(selector, state=state, timeout=timeout)
                
                return {
                    "success": True,
                    "selector": selector,
                    "state": state
                }
                
            except Exception as e:
                logger.error(f"Wait failed: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "selector": selector,
                    "state": state
                }
    
    async def execute_js(
        self,
        url: str,
        script: str,
        args: Optional[List[Any]] = None,
        timeout: int = PAGE_TIMEOUT
    ) -> Dict[str, Any]:
        async with self.get_page() as page:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                
                if args:
                    result = await page.evaluate(script, args)
                else:
                    result = await page.evaluate(script)
                
                return {
                    "success": True,
                    "result": result
                }
                
            except Exception as e:
                logger.error(f"JavaScript execution failed: {e}")
                return {
                    "success": False,
                    "error": str(e)
                }
    
    async def get_html(
        self,
        url: str,
        selector: Optional[str] = None,
        outer: bool = True,
        timeout: int = PAGE_TIMEOUT
    ) -> Dict[str, Any]:
        async with self.get_page() as page:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                
                if selector:
                    element = await page.wait_for_selector(selector, timeout=timeout)
                    if outer:
                        html = await element.evaluate("el => el.outerHTML")
                    else:
                        html = await element.evaluate("el => el.innerHTML")
                else:
                    html = await page.content()
                
                return {
                    "success": True,
                    "html": html,
                    "url": page.url,
                    "selector": selector
                }
                
            except Exception as e:
                logger.error(f"Get HTML failed: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "url": url
                }
    
    def _reset_idle_timer(self):
        self._last_activity = datetime.now()
        if self._idle_timer:
            self._idle_timer.cancel()
        if self._browser is not None and IDLE_SHUTDOWN_SECONDS > 0:
            self._idle_timer = threading.Timer(
                IDLE_SHUTDOWN_SECONDS, self._idle_shutdown
            )
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def _idle_shutdown(self):
        if not self._pages:
            logger.info(f"Browser idle for {IDLE_SHUTDOWN_SECONDS}s, shutting down to free resources")
            try:
                loop = _get_browser_loop_if_exists()
                if loop and not loop.is_closed():
                    future = asyncio.run_coroutine_threadsafe(self.shutdown(), loop)
                    future.result(timeout=10)
            except Exception as e:
                logger.warning(f"Idle shutdown error: {e}")

    def get_state(self) -> Dict[str, Any]:
        return {
            "initialized": self._state.initialized,
            "browser_type": self._state.browser_type,
            "headless": self._state.headless,
            "active_pages": self._state.active_pages,
            "max_pages": self._state.max_pages,
            "total_navigations": self._state.total_navigations,
            "total_screenshots": self._state.total_screenshots,
            "errors": self._state.errors[-10:] if self._state.errors else []
        }
    
    async def shutdown(self):
        logger.info("Shutting down BrowserAutomationService")
        
        for page_id, info in list(self._pages.items()):
            try:
                await info.page.close()
            except Exception:
                pass
        self._pages.clear()

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        
        self._state.initialized = False
        self._state.active_pages = 0
        logger.info("BrowserAutomationService shutdown complete")


def run_browser_action(coro):
    try:
        loop = asyncio.get_running_loop()
        return asyncio.ensure_future(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            pass


def _get_browser_loop_if_exists():
    try:
        from backend.tools.browser_tools import _browser_loop
        return _browser_loop
    except ImportError:
        return None


def get_browser_service() -> BrowserAutomationService:
    return BrowserAutomationService.get_instance()


def register_browser_shutdown(app):
    import atexit

    def _shutdown_browser():
        service = BrowserAutomationService.get_instance()
        if service._browser is not None:
            logger.info("Shutting down browser on app exit")
            try:
                loop = _get_browser_loop_if_exists()
                if loop and not loop.is_closed():
                    future = asyncio.run_coroutine_threadsafe(service.shutdown(), loop)
                    future.result(timeout=10)
            except Exception as e:
                logger.warning(f"Browser shutdown error on exit: {e}")

    atexit.register(_shutdown_browser)
