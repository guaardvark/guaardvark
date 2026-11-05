"""Read on in a tool result that was longer than the chat could show.

When a tool's output is longer than the chat model's window onto it, the chat
keeps the whole output under an id (R1, R2, ...) and tells the model how much
it saw (backend/services/research_workspace.py). This tool reads the kept
output by line range, or finds the lines that match a pattern, so the model
can go through a 300-line file or a long log the way a person scrolls.
"""

import logging
from typing import Any, Dict, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


def _session_of(context: Optional[Dict[str, Any]]) -> Optional[str]:
    context = context or {}
    return context.get("chat_session_id") or context.get("session_id")


class ReadResultTool(BaseTool):
    name = "read_result"
    description = (
        "Read more of an earlier tool result that was cut short. When a result says 'The whole result "
        "is kept as R3', pass id='R3' with start_line to read the next lines, or pattern to list only "
        "the lines that match (numbered). Results are kept for this chat."
    )
    chat_summary = (
        "Read on in a cut-short tool result: id='R3' plus start_line (next lines) or pattern "
        "(matching lines, numbered)."
    )
    read_only = True
    plan_research = True
    idempotent = True
    observation_chars = 6200
    parameters = {
        "id": ToolParameter(name="id", type="string", required=True,
                            description="The kept result's id, e.g. R3, from the line under the cut result."),
        "start_line": ToolParameter(name="start_line", type="int", required=False,
                                    description="First line to read (1-based). Default 1."),
        "end_line": ToolParameter(name="end_line", type="int", required=False,
                                  description="Last line to read. Default: as many as fit."),
        "pattern": ToolParameter(name="pattern", type="string", required=False,
                                 description="Words or a regex; returns only matching lines with their numbers."),
    }

    def execute(self, id: str = "", start_line: Optional[int] = None, end_line: Optional[int] = None,
                pattern: Optional[str] = None, _agent_context: Optional[Dict[str, Any]] = None,
                **_ignored) -> ToolResult:
        from backend.services import research_workspace as rw

        session_id = _session_of(_agent_context or self._context)
        rid = str(id or "").strip().upper()
        text = rw.load(session_id, rid) if session_id else None
        if text is None:
            return ToolResult(success=False, output=None, error=(
                f"No kept result '{id}' in this chat. Ids come from the line under a cut-short result, "
                f"e.g. 'kept as R3'."))
        info = rw.describe(session_id, rid) or {}
        source = info.get("tool", "a tool")
        args = ", ".join(f"{k}={v}" for k, v in (info.get("params") or {}).items())
        head = f"{rid} ({source}{'(' + args + ')' if args else ''}), {info.get('lines', '?')} lines"

        if pattern:
            found = rw.find_lines(text, pattern)
            if not found["count"]:
                return ToolResult(success=True, output=f"{head}. No line matches '{pattern}'.")
            more = (f" Showing the first {found['shown']}; narrow the pattern for the rest."
                    if found["count"] > found["shown"] else "")
            return ToolResult(success=True, output=(
                f"{head}. {found['count']} lines match '{pattern}'.{more}\n{found['text']}\n"
                f"[Read around a match with read_result(id=\"{rid}\", start_line=<its number>).]"))

        try:
            page = rw.read_lines(text, start_line, end_line)
        except (TypeError, ValueError):
            return ToolResult(success=False, output=None,
                              error="start_line and end_line must be whole numbers, counted from 1.")
        if page["past_end"]:
            return ToolResult(success=True, output=(
                f"{head}. Nothing after line {page['total']}: that was the end of {rid}."))
        footer = (f"[Lines {page['first']}-{page['last']} of {page['total']}. Next: "
                  f"read_result(id=\"{rid}\", start_line={page['last'] + 1})]"
                  if page["last"] < page["total"] else f"[Lines {page['first']}-{page['last']} of "
                                                       f"{page['total']}: the end of {rid}.]")
        return ToolResult(success=True, output=f"{head}.\n{page['text']}\n{footer}",
                          metadata={"id": rid, "first": page["first"], "last": page["last"],
                                    "total": page["total"]})
