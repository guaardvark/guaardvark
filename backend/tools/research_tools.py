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


class NoteTool(BaseTool):
    name = "note"
    description = (
        "Keep a finding in your notes for this chat: what you learned, where (file:line or page), "
        "and what is still to check. The notes are shown to you on every step and at the next "
        "message, after old tool results have scrolled out of view. They hold "
        "about 1,500 characters; when full, call again with replace=true and a shorter rewrite."
    )
    chat_summary = (
        "Keep a finding (with file:line or page) in your notes for this chat; they stay in view when "
        "old results scroll out. replace=true rewrites them when full."
    )
    read_only = True
    plan_research = True
    observation_chars = 2000
    parameters = {
        "text": ToolParameter(name="text", type="string", required=True,
                              description="What to keep, short: the fact and where it is."),
        "replace": ToolParameter(name="replace", type="bool", required=False, default=False,
                                 description="True to replace all notes with text (to rewrite them shorter)."),
    }

    def execute(self, text: str = "", replace: Any = False, _agent_context: Optional[Dict[str, Any]] = None,
                **_ignored) -> ToolResult:
        from backend.services import research_workspace as rw

        session_id = _session_of(_agent_context or self._context)
        if not session_id:
            return ToolResult(success=False, output=None, error="Notes need a chat session.")
        replace = str(replace).strip().lower() in ("1", "true", "yes", "on")
        done = rw.write_note(session_id, text, replace=replace)
        if not done["ok"]:
            return ToolResult(success=True, output=(
                f"Not added: the notes would pass {rw.NOTES_MAX_CHARS:,} characters. Rewrite them "
                f"shorter with note(text=\"...\", replace=true). Current notes:\n{done['notes']}"))
        return ToolResult(success=True, output=f"Noted ({done['chars']:,}/{rw.NOTES_MAX_CHARS:,} characters).")


class ScanTool(BaseTool):
    name = "scan"
    description = (
        "Read a whole long source part by part for one question and return what it found, with line "
        "numbers or pages: a file of Guaardvark's own code (path), an indexed document (its filename "
        "as list_documents prints it) or a kept result (R3). For questions about the whole of "
        "something: every place X happens, what a long report says about Y, a summary of a large "
        "file. Slow: one model call per part, so a 300-page report takes minutes and shows progress "
        "in the footer; Stop ends it with what was found so far. For one fact, search and read "
        "instead."
    )
    chat_summary = (
        "Read a whole long file, indexed document or kept result (R3) for one question; returns "
        "findings with lines or pages. Slow; for 'every place' or whole-document questions."
    )
    read_only = True
    # A scan holds the chat model for minutes; plan research stays bounded.
    plan_research = False
    observation_chars = 4000
    parameters = {
        "source": ToolParameter(name="source", type="string", required=True,
                                description="A code path (backend/services/x.py), an indexed document's filename, or R3."),
        "question": ToolParameter(name="question", type="string", required=True,
                                  description="What to look for, as a full question."),
    }

    def execute(self, source: str = "", question: str = "", _agent_context: Optional[Dict[str, Any]] = None,
                **_ignored) -> ToolResult:
        import re
        from backend.services import research_scan, research_workspace
        from backend.services.agent_control_service import get_chat_stop_check

        if not (source or "").strip() or not (question or "").strip():
            return ToolResult(success=False, output=None, error="scan needs a source and a question.")
        session_id = _session_of(_agent_context or self._context)
        try:
            done = research_scan.scan(source, question, session_id=session_id, stop=get_chat_stop_check())
        except research_scan.ScanRefused as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        whole = done["read"] == done["parts"]
        head = (f"Scan of {source} ({done['kind']}): read {done['read']} of {done['parts']} parts"
                f"{'' if whole else ', stopped early'} in {done['secs']:.0f} s, for: {question}")
        if session_id and done["read"]:
            last = done["spans"][-1]
            m = re.search(r"lines \d+-(\d+) of (\d+)", last)
            if m:
                research_workspace.log_read(session_id, source, 1, int(m.group(1)), int(m.group(2)))
            else:
                research_workspace.log_read(session_id, source, 1, done["read"], done["parts"], unit="parts")
        body = done["memory"] or "Nothing in it answers the question."
        return ToolResult(success=True, output=f"{head}\n{body}",
                          metadata={"parts": done["parts"], "read": done["read"], "secs": done["secs"]})
