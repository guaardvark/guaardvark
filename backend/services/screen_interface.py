#!/usr/bin/env python3
"""
Screen Interface — Abstract base class for screen capture and input injection.

Backends implement this interface:
- LocalScreenBackend (pyautogui/mss) — this machine
- RemoteBackend (WebSocket/GAP) — remote machines (Phase 2)
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple

from PIL import Image


class ScreenInterface(ABC):
    """Abstract interface for screen capture and input injection."""

    @abstractmethod
    def capture(self) -> Tuple[Image.Image, Tuple[int, int]]:
        """Capture screenshot and return (image, cursor_position)."""
        ...

    @abstractmethod
    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
        """Click at coordinates."""
        ...

    @abstractmethod
    def move(self, x: int, y: int) -> Dict[str, Any]:
        """Move cursor to coordinates."""
        ...

    @abstractmethod
    def type_text(self, text: str, interval: float = 0.05) -> Dict[str, Any]:
        """Type text with keystroke delay."""
        ...

    @abstractmethod
    def hotkey(self, *keys: str) -> Dict[str, Any]:
        """Press keyboard shortcut."""
        ...

    @abstractmethod
    def scroll(self, x: int, y: int, amount: int = -3) -> Dict[str, Any]:
        """Scroll wheel at position."""
        ...

    @abstractmethod
    def screen_size(self) -> Tuple[int, int]:
        """Return (width, height) of the screen."""
        ...

    @abstractmethod
    def cursor_position(self) -> Tuple[int, int]:
        """Return current (x, y) cursor position."""
        ...

    # ------------------------------------------------------------------
    # Optional gestures — concrete default implementations call back into
    # the abstract primitives above. Backends with native support (xdotool's
    # --repeat for dblclick, smooth-interpolated drag) should override.
    # ------------------------------------------------------------------

    def double_click(self, x: int, y: int, button: str = "left") -> Dict[str, Any]:
        """Double-click. Default falls back to two `click()` calls, which is
        slower and may miss browser `dblclick` windows. Backends should
        override with a native repeat-click primitive."""
        r1 = self.click(x, y, button=button)
        if not r1.get("success"):
            return r1
        r2 = self.click(x, y, button=button)
        if not r2.get("success"):
            return r2
        return {"success": True, "action": "double_click", "x": x, "y": y}

    def triple_click(self, x: int, y: int, button: str = "left") -> Dict[str, Any]:
        """Triple-click (line select). Default falls back to three sequential
        clicks; backends should override for proper timing."""
        for _ in range(3):
            r = self.click(x, y, button=button)
            if not r.get("success"):
                return r
        return {"success": True, "action": "triple_click", "x": x, "y": y}

    def drag(self, from_x: int, from_y: int, to_x: int, to_y: int,
             button: str = "left", duration_ms: int = 300) -> Dict[str, Any]:
        """Press at (from), move smoothly to (to) over duration, release.
        Smooth interpolation matters — many drag-and-drop UIs reject
        instant teleport-style drags as 'untrusted'. Backends without
        native drag support should raise NotImplementedError rather than
        attempt a click+click stand-in."""
        raise NotImplementedError("drag requires backend-specific support")

    def hover(self, x: int, y: int, settle_ms: int = 200) -> Dict[str, Any]:
        """Move cursor and wait for hover-triggered UI (tooltips, menus) to
        render. Default just moves; backends should override to add the
        settle delay."""
        return self.move(x, y)
