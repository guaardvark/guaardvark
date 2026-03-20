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
