"""Research mode: questions answered by reading, not by recall.

"Tell me about your planning code", "explain how a failed step is handled",
"what caused the fire, name the report and the page": the answer sits in
files or documents longer than one look. Ordinary chat gives the model five
steps and asks it to wrap up at the third, which ends such a question after
one partial read. In research mode the model gets more steps, the reading
and note tools, and a short method: find, read in pieces, note, read on
until the notes answer the question, then answer from them.

Nothing here runs a model; the mode only changes the turn's budget, the
tools offered and one block of instructions.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Steps a research turn may take (Tier 2). Ordinary chat takes 5. Sized for a
# search, three or four reads, notes and the answer; still bounded by the
# turn's step budget. Measured with scripts/eval_research.py (see the
# research_evals log named in the commit that set it).
RESEARCH_MAX_ITERATIONS = 12

_ASK = re.compile(
    r"\b(explain|describe|tell me about|walk me through|look into|investigate|analy[sz]e|review|"
    r"suggest|improve|summari[sz]e|compare|across|how (?:does|do|is|are|did)|why (?:does|do|is|did)|"
    r"what (?:does|do|did|caused|causes|happened|was|were|is|are)|where (?:is|are|does|do)|which|"
    r"name the|find (?:where|every|all)|list (?:every|all))\b", re.I)
_DOCUMENTS = re.compile(
    r"\b(reports?|documents?|pdfs?|papers?|manuals?|handbooks?|pages?|chapters?|sections?|"
    r"spreadsheets?|transcripts?|minutes|contracts?)\b", re.I)

CODE_TOOLS = ["search_codebase", "system_map_query", "search_code", "read_code", "read_ast_node",
              "read_result", "note"]
DOCUMENT_TOOLS = ["search_knowledge_base", "list_documents", "get_document_outline",
                  "read_document_section", "read_result", "note"]

_METHOD = {
    "code": (
        "Research mode: answer this by reading the code, not from memory. Work in rounds. "
        "1) Find where the answer lives: system_map_query (mode 'where'), search_codebase, or "
        "search_code for an exact name. 2) Read those places with read_code, using start_line and "
        "end_line, or read_result for a result that was cut short; read every file the answer "
        "touches, not only the first. 3) After each read, keep what you learned with "
        "note(text=\"fact, file:line\"). 4) Read on (the next lines, the caller, the other file) "
        "until your notes answer every part of the question. Then answer from your notes, in full, "
        "with file:line for each point. The short-reply rule does not apply to this answer."
    ),
    "documents": (
        "Research mode: answer this from the documents, not from memory. Work in rounds. "
        "1) Find where the answer lives: search_knowledge_base, or list_documents then "
        "get_document_outline. 2) Read those pages or sections with read_document_section, and "
        "the next offset when the answer continues; for several reports, find each one. 3) After "
        "each read, keep what you found with note(text=\"fact, report, page\"). 4) Read on until "
        "your notes answer every part of the question. Then answer from your notes, naming the "
        "report and page for each point. The short-reply rule does not apply to this answer."
    ),
}

# Self-analysis dossier in research mode, in characters (ordinary chat: 6,000).
# The dossier points at files; the reads are the answer material and need the
# room in an 8,192-token window.
DOSSIER_CHARS = 3000
# Reading calls a code research answer needs before it is accepted: one look
# at a search hit is not reading the code.
MIN_CODE_READS = 2
READ_TOOLS = {"read_code", "read_result", "read_ast_node", "scan", "read_document_section"}

RESEARCH_NUDGE = (
    "This question is answered by reading, and no tool has run yet. Before you answer, look it "
    "up: search for where the answer is, read it, and keep what you find with note. Then answer."
)


def kind(message: str, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """'code' or 'documents' when the message is a research question, else None."""
    text = message or ""
    if not text.strip():
        return None
    try:
        from backend.services import self_analysis
        if self_analysis.about_self(text, options) and (_ASK.search(text) or "?" in text):
            return "code"
    except Exception:  # noqa: BLE001 - without the mapper, code questions take ordinary chat
        pass
    if _DOCUMENTS.search(text) and (_ASK.search(text) or "?" in text):
        return "documents"
    return None


def method_note(research_kind: str) -> str:
    return _METHOD.get(research_kind, "")


def tools_for(research_kind: str) -> List[str]:
    return CODE_TOOLS if research_kind == "code" else DOCUMENT_TOOLS if research_kind == "documents" else []


def depth_nudge(steps: List[Dict[str, Any]], focus: List[str], session_id: Optional[str]) -> str:
    """The push to read on when a code research answer comes after fewer than
    MIN_CODE_READS reading calls; empty when it has read enough."""
    reads = sum(1 for step in steps or [] for call in (step.get("tool_calls") or [])
                if isinstance(call, dict) and call.get("tool_name") in READ_TOOLS and call.get("success", True))
    if reads >= MIN_CODE_READS:
        return ""
    try:
        from backend.services import research_workspace
        read = research_workspace.read_sources(session_id)
    except Exception:  # noqa: BLE001
        read = set()
    unread = [f for f in focus if f not in read][:3]
    where = (f" Start with {', '.join(unread)}." if unread else
             " Read the files your answer depends on.")
    return (
        f"Before answering: you have read {reads} place(s) of code for this question, and an answer "
        f"about how code works needs the code itself.{where} Read them with read_code (in pages), "
        "keep what you find with note, then answer from your notes with file:line."
    )
