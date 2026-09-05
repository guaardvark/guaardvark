"""Compact status-line segments for the REPL prompt / banner."""

from __future__ import annotations

from typing import Any, Iterable


DEFAULT_ITEMS = ("model", "gpu", "jobs", "git", "cwd")


def render(items: Iterable[str] | None, data: dict[str, Any]) -> str:
    """Join configured segments with a middle-dot separator.

    ``data`` keys: model, gpu (str), jobs (int), git (str), cwd (str),
    agent (bool). Unknown items are skipped.
    """
    wanted = list(items) if items else list(DEFAULT_ITEMS)
    parts: list[str] = []
    for key in wanted:
        if key == "model" and data.get("model"):
            parts.append(str(data["model"]))
        elif key == "gpu" and data.get("gpu"):
            parts.append(str(data["gpu"]))
        elif key == "jobs":
            n = int(data.get("jobs") or 0)
            if n:
                parts.append(f"{n} job{'s' if n != 1 else ''}")
        elif key == "git" and data.get("git"):
            parts.append(str(data["git"]))
        elif key == "cwd" and data.get("cwd"):
            parts.append(str(data["cwd"]))
        elif key == "agent" and data.get("agent"):
            parts.append("agent")
    return " · ".join(parts)
