#!/usr/bin/env python3

import asyncio
import base64
import io
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

DESKTOP_AUTOMATION_ENABLED = os.getenv("GUAARDVARK_DESKTOP_AUTOMATION", "false").lower() == "true"
GUI_AUTOMATION_ENABLED = os.getenv("GUAARDVARK_GUI_AUTOMATION", "false").lower() == "true"

GUAARDVARK_ROOT = os.environ.get("GUAARDVARK_ROOT", str(Path(__file__).resolve().parents[2]))

ALLOWED_PATHS = [
    os.path.join(GUAARDVARK_ROOT, "data"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Downloads"),
    "/tmp",
]
if os.getenv("GUAARDVARK_ALLOWED_PATHS"):
    ALLOWED_PATHS.extend(os.getenv("GUAARDVARK_ALLOWED_PATHS").split(":"))

ALLOWED_APPS = [
    "code", "code-insiders",
    "firefox", "firefox-esr", "chrome", "chromium", "chromium-browser",
    "gnome-terminal", "konsole", "xterm", "alacritty", "kitty",
    "nautilus", "dolphin", "thunar", "nemo",
    "gedit", "kate", "nano", "vim",
    "libreoffice", "gimp", "inkscape",
    "vlc",
]
if os.getenv("GUAARDVARK_ALLOWED_APPS"):
    ALLOWED_APPS.extend(os.getenv("GUAARDVARK_ALLOWED_APPS").split(":"))

RATE_LIMIT_WINDOW = 60
MAX_OPS_PER_WINDOW = 100
MAX_GUI_OPS_PER_WINDOW = 30


@dataclass
class RateLimitState:
    operations: List[float] = field(default_factory=list)
    gui_operations: List[float] = field(default_factory=list)
    
    def check_limit(self, is_gui: bool = False) -> bool:
        now = time.time()
        cutoff = now - RATE_LIMIT_WINDOW
        
        if is_gui:
            self.gui_operations = [t for t in self.gui_operations if t > cutoff]
            if len(self.gui_operations) >= MAX_GUI_OPS_PER_WINDOW:
                return False
            self.gui_operations.append(now)
        
        self.operations = [t for t in self.operations if t > cutoff]
        if len(self.operations) >= MAX_OPS_PER_WINDOW:
            return False
        self.operations.append(now)
        return True


@dataclass
class FileWatcher:
    path: str
    events: List[str]
    callback: Optional[Callable] = None
    observer: Any = None
    created_at: datetime = field(default_factory=datetime.now)
    event_count: int = 0


@dataclass
class DesktopState:
    initialized: bool = False
    file_watchers_active: int = 0
    total_file_operations: int = 0
    total_gui_operations: int = 0
    total_clipboard_operations: int = 0
    total_notifications: int = 0
    errors: List[str] = field(default_factory=list)


class DesktopAutomationService:
    
    _instance: Optional["DesktopAutomationService"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    @classmethod
    def get_instance(cls) -> "DesktopAutomationService":
        return cls()
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._state = DesktopState()
        self._rate_limit = RateLimitState()
        self._file_watchers: Dict[str, FileWatcher] = {}
        self._audit_log: List[Dict[str, Any]] = []
        
        self._pyautogui = None
        self._watchdog = None
        self._pyperclip = None
        self._plyer = None
        self._pynput = None
        
        logger.info("DesktopAutomationService initialized")
        logger.info(f"Desktop automation enabled: {DESKTOP_AUTOMATION_ENABLED}")
        logger.info(f"GUI automation enabled: {GUI_AUTOMATION_ENABLED}")
    
    def _audit(self, operation: str, details: Dict[str, Any], success: bool):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "details": details,
            "success": success
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-1000:]
        
        log_level = logging.INFO if success else logging.WARNING
        logger.log(log_level, f"Desktop operation: {operation} - {'success' if success else 'failed'}")
    
    def _check_path_allowed(self, path: str) -> bool:
        abs_path = os.path.abspath(os.path.expanduser(path))
        for allowed in ALLOWED_PATHS:
            allowed_abs = os.path.abspath(os.path.expanduser(allowed))
            if abs_path.startswith(allowed_abs):
                return True
        return False
    
    def _check_app_allowed(self, app: str) -> bool:
        app_name = os.path.basename(app).lower()
        return app_name in [a.lower() for a in ALLOWED_APPS]
    
    def _check_rate_limit(self, is_gui: bool = False) -> bool:
        return self._rate_limit.check_limit(is_gui)
    
    def _load_pyautogui(self):
        if self._pyautogui is None:
            try:
                import pyautogui
                pyautogui.FAILSAFE = True
                pyautogui.PAUSE = 0.1
                self._pyautogui = pyautogui
            except ImportError:
                raise ImportError("pyautogui not installed. Run: pip install pyautogui")
        return self._pyautogui
    
    def _load_watchdog(self):
        if self._watchdog is None:
            try:
                from watchdog.observers import Observer
                from watchdog.events import FileSystemEventHandler
                self._watchdog = {"Observer": Observer, "Handler": FileSystemEventHandler}
            except ImportError:
                raise ImportError("watchdog not installed. Run: pip install watchdog")
        return self._watchdog
    
    def _load_pyperclip(self):
        if self._pyperclip is None:
            try:
                import pyperclip
                self._pyperclip = pyperclip
            except ImportError:
                raise ImportError("pyperclip not installed. Run: pip install pyperclip")
        return self._pyperclip
    
    def _load_plyer(self):
        if self._plyer is None:
            try:
                from plyer import notification
                self._plyer = notification
            except ImportError:
                raise ImportError("plyer not installed. Run: pip install plyer")
        return self._plyer
    
    
    def file_watch_start(
        self,
        path: str,
        events: List[str] = None,
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        if not DESKTOP_AUTOMATION_ENABLED:
            return {"success": False, "error": "Desktop automation disabled"}
        
        if not self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}
        
        if not self._check_path_allowed(path):
            self._audit("file_watch_start", {"path": path}, False)
            return {"success": False, "error": f"Path not allowed: {path}"}
        
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"Path does not exist: {path}"}
        
        events = events or ["created", "modified", "deleted", "moved"]
        
        try:
            watchdog = self._load_watchdog()
            
            class EventHandler(watchdog["Handler"]):
                def __init__(handler_self, watcher_ref: FileWatcher, events: List[str]):
                    super().__init__()
                    handler_self.watcher = watcher_ref
                    handler_self.events = events
                
                def _should_handle(handler_self, event_type: str) -> bool:
                    return event_type in handler_self.events
                
                def on_created(handler_self, event):
                    if handler_self._should_handle("created"):
                        handler_self.watcher.event_count += 1
                        if handler_self.watcher.callback:
                            handler_self.watcher.callback("created", event.src_path)
                
                def on_modified(handler_self, event):
                    if handler_self._should_handle("modified"):
                        handler_self.watcher.event_count += 1
                        if handler_self.watcher.callback:
                            handler_self.watcher.callback("modified", event.src_path)
                
                def on_deleted(handler_self, event):
                    if handler_self._should_handle("deleted"):
                        handler_self.watcher.event_count += 1
                        if handler_self.watcher.callback:
                            handler_self.watcher.callback("deleted", event.src_path)
                
                def on_moved(handler_self, event):
                    if handler_self._should_handle("moved"):
                        handler_self.watcher.event_count += 1
                        if handler_self.watcher.callback:
                            handler_self.watcher.callback("moved", event.src_path, event.dest_path)
            
            watcher = FileWatcher(path=abs_path, events=events, callback=callback)
            handler = EventHandler(watcher, events)
            
            observer = watchdog["Observer"]()
            observer.schedule(handler, abs_path, recursive=os.path.isdir(abs_path))
            observer.start()
            
            watcher.observer = observer
            watch_id = f"watch_{len(self._file_watchers)}_{int(time.time())}"
            self._file_watchers[watch_id] = watcher
            
            self._state.file_watchers_active = len(self._file_watchers)
            self._state.total_file_operations += 1
            self._audit("file_watch_start", {"path": abs_path, "events": events}, True)
            
            return {
                "success": True,
                "watch_id": watch_id,
                "path": abs_path,
                "events": events
            }
            
        except Exception as e:
            self._audit("file_watch_start", {"path": path, "error": str(e)}, False)
            return {"success": False, "error": str(e)}
    
    def file_watch_stop(self, watch_id: str) -> Dict[str, Any]:
        if watch_id not in self._file_watchers:
            return {"success": False, "error": f"Unknown watch_id: {watch_id}"}
        
        try:
            watcher = self._file_watchers[watch_id]
            if watcher.observer:
                watcher.observer.stop()
                watcher.observer.join(timeout=5)
            
            del self._file_watchers[watch_id]
            self._state.file_watchers_active = len(self._file_watchers)
            self._audit("file_watch_stop", {"watch_id": watch_id}, True)
            
            return {
                "success": True,
                "watch_id": watch_id,
                "event_count": watcher.event_count
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def file_bulk_operation(
        self,
        operation: str,
        source_patterns: List[str],
        destination: Optional[str] = None
    ) -> Dict[str, Any]:
        if not DESKTOP_AUTOMATION_ENABLED:
            return {"success": False, "error": "Desktop automation disabled"}
        
        if not self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}
        
        if operation not in ("copy", "move", "delete"):
            return {"success": False, "error": f"Invalid operation: {operation}"}
        
        if operation in ("copy", "move") and not destination:
            return {"success": False, "error": "Destination required for copy/move"}
        
        if destination and not self._check_path_allowed(destination):
            return {"success": False, "error": f"Destination not allowed: {destination}"}
        
        results = {
            "success": True,
            "operation": operation,
            "processed": [],
            "failed": [],
            "total_processed": 0,
            "total_failed": 0
        }
        
        for pattern in source_patterns:
            import glob as glob_module
            matches = glob_module.glob(os.path.expanduser(pattern))
            
            for source_path in matches:
                if not self._check_path_allowed(source_path):
                    results["failed"].append({
                        "path": source_path,
                        "error": "Path not allowed"
                    })
                    results["total_failed"] += 1
                    continue
                
                try:
                    if operation == "copy":
                        dest_path = os.path.join(destination, os.path.basename(source_path))
                        if os.path.isdir(source_path):
                            shutil.copytree(source_path, dest_path)
                        else:
                            shutil.copy2(source_path, dest_path)
                        results["processed"].append({"source": source_path, "dest": dest_path})
                    
                    elif operation == "move":
                        dest_path = os.path.join(destination, os.path.basename(source_path))
                        shutil.move(source_path, dest_path)
                        results["processed"].append({"source": source_path, "dest": dest_path})
                    
                    elif operation == "delete":
                        if os.path.isdir(source_path):
                            shutil.rmtree(source_path)
                        else:
                            os.remove(source_path)
                        results["processed"].append({"source": source_path})
                    
                    results["total_processed"] += 1
                    
                except Exception as e:
                    results["failed"].append({"path": source_path, "error": str(e)})
                    results["total_failed"] += 1
        
        self._state.total_file_operations += results["total_processed"]
        self._audit("file_bulk_operation", {
            "operation": operation,
            "processed": results["total_processed"],
            "failed": results["total_failed"]
        }, results["total_failed"] == 0)
        
        return results
    
    
    def app_launch(
        self,
        app_name: str,
        args: List[str] = None,
        wait: bool = False
    ) -> Dict[str, Any]:
        if not DESKTOP_AUTOMATION_ENABLED:
            return {"success": False, "error": "Desktop automation disabled"}
        
        if not self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}
        
        if not self._check_app_allowed(app_name):
            self._audit("app_launch", {"app": app_name}, False)
            return {"success": False, "error": f"App not allowed: {app_name}"}
        
        try:
            cmd = [app_name] + (args or [])
            
            if wait:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                return {
                    "success": result.returncode == 0,
                    "app": app_name,
                    "exit_code": result.returncode,
                    "stdout": result.stdout[:10000],
                    "stderr": result.stderr[:10000]
                }
            else:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                self._audit("app_launch", {"app": app_name, "pid": process.pid}, True)
                return {
                    "success": True,
                    "app": app_name,
                    "pid": process.pid
                }
                
        except FileNotFoundError:
            return {"success": False, "error": f"App not found: {app_name}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def app_list(self, filter_pattern: Optional[str] = None) -> Dict[str, Any]:
        if not DESKTOP_AUTOMATION_ENABLED:
            return {"success": False, "error": "Desktop automation disabled"}
        
        try:
            import psutil
            
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_percent']):
                try:
                    info = proc.info
                    name = info['name'] or ''
                    
                    if filter_pattern:
                        if not fnmatch(name.lower(), filter_pattern.lower()):
                            continue
                    
                    processes.append({
                        "pid": info['pid'],
                        "name": name,
                        "cmdline": ' '.join(info['cmdline'][:3]) if info['cmdline'] else '',
                        "cpu_percent": info['cpu_percent'],
                        "memory_percent": round(info['memory_percent'], 2) if info['memory_percent'] else 0
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            return {
                "success": True,
                "processes": processes[:100],
                "total": len(processes)
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def app_focus(
        self,
        window_title: Optional[str] = None,
        process_name: Optional[str] = None
    ) -> Dict[str, Any]:
        if not DESKTOP_AUTOMATION_ENABLED:
            return {"success": False, "error": "Desktop automation disabled"}
        
        if not GUI_AUTOMATION_ENABLED:
            return {"success": False, "error": "GUI automation disabled"}
        
        try:
            if window_title:
                result = subprocess.run(
                    ["wmctrl", "-a", window_title],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    return {"success": True, "method": "wmctrl", "pattern": window_title}
            
            if process_name:
                result = subprocess.run(
                    ["xdotool", "search", "--name", process_name, "windowactivate"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    return {"success": True, "method": "xdotool", "pattern": process_name}
            
            return {"success": False, "error": "Window not found"}
            
        except FileNotFoundError:
            return {"success": False, "error": "wmctrl/xdotool not installed"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    
    def gui_click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1
    ) -> Dict[str, Any]:
        if not GUI_AUTOMATION_ENABLED:
            return {"success": False, "error": "GUI automation disabled. Set GUAARDVARK_GUI_AUTOMATION=true"}
        
        if not self._check_rate_limit(is_gui=True):
            return {"success": False, "error": "GUI rate limit exceeded"}
        
        try:
            pyautogui = self._load_pyautogui()
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            
            self._state.total_gui_operations += 1
            self._audit("gui_click", {"x": x, "y": y, "button": button}, True)
            
            return {
                "success": True,
                "x": x,
                "y": y,
                "button": button,
                "clicks": clicks
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def gui_type(self, text: str, interval: float = 0.05) -> Dict[str, Any]:
        if not GUI_AUTOMATION_ENABLED:
            return {"success": False, "error": "GUI automation disabled"}
        
        if not self._check_rate_limit(is_gui=True):
            return {"success": False, "error": "GUI rate limit exceeded"}
        
        try:
            pyautogui = self._load_pyautogui()
            pyautogui.write(text, interval=interval)
            
            self._state.total_gui_operations += 1
            self._audit("gui_type", {"length": len(text)}, True)
            
            return {
                "success": True,
                "text_length": len(text)
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def gui_hotkey(self, *keys) -> Dict[str, Any]:
        if not GUI_AUTOMATION_ENABLED:
            return {"success": False, "error": "GUI automation disabled"}
        
        if not self._check_rate_limit(is_gui=True):
            return {"success": False, "error": "GUI rate limit exceeded"}
        
        try:
            pyautogui = self._load_pyautogui()
            pyautogui.hotkey(*keys)
            
            self._state.total_gui_operations += 1
            self._audit("gui_hotkey", {"keys": list(keys)}, True)
            
            return {
                "success": True,
                "keys": list(keys)
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def gui_screenshot(
        self,
        region: Optional[Tuple[int, int, int, int]] = None,
        format: str = "png"
    ) -> Dict[str, Any]:
        if not GUI_AUTOMATION_ENABLED:
            return {"success": False, "error": "GUI automation disabled"}
        
        if not self._check_rate_limit(is_gui=True):
            return {"success": False, "error": "GUI rate limit exceeded"}
        
        try:
            pyautogui = self._load_pyautogui()
            
            if region:
                screenshot = pyautogui.screenshot(region=region)
            else:
                screenshot = pyautogui.screenshot()
            
            buffer = io.BytesIO()
            screenshot.save(buffer, format=format.upper())
            image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            self._state.total_gui_operations += 1
            self._audit("gui_screenshot", {"region": region}, True)
            
            return {
                "success": True,
                "image_base64": image_base64,
                "format": format,
                "size": {"width": screenshot.width, "height": screenshot.height}
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def gui_locate_image(
        self,
        image_path: str,
        confidence: float = 0.9
    ) -> Dict[str, Any]:
        if not GUI_AUTOMATION_ENABLED:
            return {"success": False, "error": "GUI automation disabled"}
        
        if not self._check_path_allowed(image_path):
            return {"success": False, "error": f"Path not allowed: {image_path}"}
        
        if not self._check_rate_limit(is_gui=True):
            return {"success": False, "error": "GUI rate limit exceeded"}
        
        try:
            pyautogui = self._load_pyautogui()
            
            location = pyautogui.locateOnScreen(image_path, confidence=confidence)
            
            self._state.total_gui_operations += 1
            
            if location:
                center = pyautogui.center(location)
                return {
                    "success": True,
                    "found": True,
                    "location": {
                        "x": location.left,
                        "y": location.top,
                        "width": location.width,
                        "height": location.height
                    },
                    "center": {"x": center.x, "y": center.y}
                }
            else:
                return {
                    "success": True,
                    "found": False
                }
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    
    def clipboard_get(self) -> Dict[str, Any]:
        if not DESKTOP_AUTOMATION_ENABLED:
            return {"success": False, "error": "Desktop automation disabled"}
        
        try:
            pyperclip = self._load_pyperclip()
            content = pyperclip.paste()
            
            self._state.total_clipboard_operations += 1
            
            return {
                "success": True,
                "content": content,
                "length": len(content)
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def clipboard_set(self, content: str) -> Dict[str, Any]:
        if not DESKTOP_AUTOMATION_ENABLED:
            return {"success": False, "error": "Desktop automation disabled"}
        
        try:
            pyperclip = self._load_pyperclip()
            pyperclip.copy(content)
            
            self._state.total_clipboard_operations += 1
            self._audit("clipboard_set", {"length": len(content)}, True)
            
            return {
                "success": True,
                "length": len(content)
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    
    def notification_send(
        self,
        title: str,
        message: str,
        timeout: int = 10
    ) -> Dict[str, Any]:
        if not DESKTOP_AUTOMATION_ENABLED:
            return {"success": False, "error": "Desktop automation disabled"}
        
        try:
            notification = self._load_plyer()
            notification.notify(
                title=title,
                message=message,
                timeout=timeout,
                app_name="Guaardvark"
            )
            
            self._state.total_notifications += 1
            self._audit("notification_send", {"title": title}, True)
            
            return {
                "success": True,
                "title": title,
                "timeout": timeout
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    
    def get_state(self) -> Dict[str, Any]:
        return {
            "initialized": self._state.initialized,
            "desktop_automation_enabled": DESKTOP_AUTOMATION_ENABLED,
            "gui_automation_enabled": GUI_AUTOMATION_ENABLED,
            "file_watchers_active": self._state.file_watchers_active,
            "total_file_operations": self._state.total_file_operations,
            "total_gui_operations": self._state.total_gui_operations,
            "total_clipboard_operations": self._state.total_clipboard_operations,
            "total_notifications": self._state.total_notifications,
            "allowed_paths": ALLOWED_PATHS,
            "allowed_apps": ALLOWED_APPS[:10],
            "errors": self._state.errors[-10:] if self._state.errors else []
        }
    
    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._audit_log[-limit:]
    
    def shutdown(self):
        logger.info("Shutting down DesktopAutomationService")
        
        for watch_id in list(self._file_watchers.keys()):
            self.file_watch_stop(watch_id)
        
        self._state = DesktopState()
        logger.info("DesktopAutomationService shutdown complete")


def get_desktop_service() -> DesktopAutomationService:
    return DesktopAutomationService.get_instance()
