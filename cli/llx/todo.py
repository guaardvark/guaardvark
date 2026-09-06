"""Lightweight todo tracking for CLI sessions (mirrors todo_write usage in Grok sessions).

Persists lightly to ~/.guaardvark/todos/<session>.jsonl (best effort).
Keeps the REPL feeling like Cursor/Claude with visible task state.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


def _todos_dir() -> Path:
    d = Path.home() / ".guaardvark" / "todos"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TodoStore:
    """Simple in-memory + optional file-backed todo list."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())
        self._items: list[dict[str, Any]] = []
        self._path = _todos_dir() / f"{self.session_id}.jsonl"
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._items.append(json.loads(line))
        except Exception:
            pass  # best effort

    def _append(self, item: dict[str, Any]) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item) + "\n")
        except Exception:
            pass

    def list(self) -> list[dict[str, Any]]:
        return [dict(i) for i in self._items if not i.get("done")]

    def all(self) -> list[dict[str, Any]]:
        return [dict(i) for i in self._items]

    def add(self, text: str, priority: str = "normal") -> dict[str, Any]:
        item = {
            "id": str(uuid.uuid4())[:8],
            "text": text.strip(),
            "priority": priority,
            "created": time.time(),
            "done": False,
        }
        self._items.append(item)
        self._append(item)
        return item

    def done(self, todo_id: str) -> bool:
        for it in self._items:
            if it.get("id") == todo_id and not it.get("done"):
                it["done"] = True
                it["completed"] = time.time()
                self._append({"id": todo_id, "done": True, "completed": it["completed"]})
                return True
        return False

    def clear(self) -> int:
        n = len([i for i in self._items if not i.get("done")])
        # mark all open done
        for it in self._items:
            if not it.get("done"):
                it["done"] = True
                it["completed"] = time.time()
        return n

    def count_open(self) -> int:
        return sum(1 for i in self._items if not i.get("done"))
