"""Read a whole file or document that is bigger than the chat model's window.

Search and paging answer "where is X" and "what does this part say". A
question about the whole of something ("every place a plan can pause", "what
does this 230-page report conclude about the tanks") needs every part read,
and no 8K window holds 10,000 lines. A scan walks the source in parts sized
to the window. The code, not the model, does the walking: for each part it
asks the chat model to rewrite a short memory of what helps answer the
question (the MemAgent loop, by prompt). The memory that comes out of the
last part is the scan's answer material, with line numbers or pages.

A scan holds the chat model for one call per part, shows its progress in
the footer, and stops at the chat's Stop.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# The memory one part may leave, in characters (about 600 tokens).
MEMORY_MAX_CHARS = 2400
# Share of the context window one part may take, in the 3-characters-per-token
# estimate the chat engine uses; the rest holds the instructions, the question,
# the memory and the reply.
PART_SHARE = 0.45
PART_MIN_CHARS, PART_MAX_CHARS = 3000, 20000
# Most parts one scan reads. At the 8,192-token window's ~11,000 characters a
# part, that is about 1,760,000 characters: the 348-page Apollo 15 mission
# report is 103 parts, the 7,000-line chat engine 36. One part is one model
# call, so a scan this long runs for many minutes in the footer. Past it the
# scan refuses and asks for a narrower source.
MAX_PARTS = 160
# Tokens one part's reply may take.
REPLY_TOKENS = 900
CALL_TIMEOUT_S = 180

_SYSTEM = (
    "You are reading a long {what} one part at a time to answer a question, keeping a short "
    "memory of what helps answer it. For each part: read it, then rewrite the memory. Keep every "
    "earlier fact that still helps, add each new fact from this part that helps, with its "
    "location ({where}), and drop what does not help. Write only the memory, as short "
    "'- fact ({where_short})' lines, at most {limit} characters. If this part adds nothing, "
    "return the memory unchanged."
)


class ScanRefused(ValueError):
    """The source cannot be scanned (missing, unreadable, too large)."""


def part_chars(num_ctx: Optional[int]) -> int:
    window = int(num_ctx or 8192)
    return max(PART_MIN_CHARS, min(PART_MAX_CHARS, int(window * 3 * PART_SHARE)))


# --- sources ---------------------------------------------------------------

def _file_text(filepath: str) -> str:
    from backend.tools.llama_code_tools import read_code
    from backend.tools.agent_tools.code_manipulation_tools import _CONTENT_END, _CONTENT_START
    raw = read_code(filepath, allow_external=False)
    if raw.startswith("ERROR"):
        raise ScanRefused(raw)
    _, marker, rest = raw.partition(_CONTENT_START)
    if not marker:
        raise ScanRefused(f"Could not read '{filepath}'.")
    return rest[:-len(_CONTENT_END)] if rest.endswith(_CONTENT_END) else rest


def _document_passages(filename: str) -> List[Tuple[str, str]]:
    """(page or section label, text) for every passage of an indexed
    document, in document order."""
    from backend.tools import knowledge_tools as kt
    table, err = kt._table()
    if err:
        raise ScanRefused(err)
    key, _shown, early = kt._choose_document(table, filename, None)
    if early:
        raise ScanRefused(early.output or early.error or f"'{filename}' is ambiguous; name the document_id.")
    clauses, params = ["metadata_->>'source_filename' = %s"], [filename]
    if key is not None:
        clauses.append(f"{kt._DOC_KEY} = %s")
        params.append(key)
    rows, qerr = kt._query(f'SELECT text, metadata_ FROM "{table}" WHERE {" AND ".join(clauses)} ORDER BY id',
                           tuple(params))
    if qerr:
        raise ScanRefused(f"Query failed: {qerr}")
    out = []
    for text, meta in rows or []:
        m = kt._meta(meta)
        body = (m.get("original_text") or text or "").strip()
        out.append((kt._section_label(m.get("heading_path"), m.get("page_label")), body))
    return out


def parts_of(source: str, session_id: Optional[str], size: int) -> Tuple[List[Dict[str, str]], str]:
    """The source cut into parts of about ``size`` characters, each with where
    it sits, and what kind of thing it is ('file', 'result' or 'document')."""
    from backend.services import research_workspace
    source = (source or "").strip()
    if re.fullmatch(r"[Rr]\d+", source):
        text = research_workspace.load(session_id, source) if session_id else None
        if text is None:
            raise ScanRefused(f"No kept result '{source}' in this chat.")
        return _line_parts(text, size), "result"
    if "/" in source or re.search(r"\.(py|jsx?|tsx?|md|json|sh|txt|ya?ml|toml|css|html)$", source):
        try:
            return _line_parts(_file_text(source), size), "file"
        except ScanRefused:
            if "/" in source:
                raise
    passages = _document_passages(source)
    if not passages:
        raise ScanRefused(f"No file '{source}' in Guaardvark's code and no indexed document by that name.")
    return _passage_parts(passages, size), "document"


def _line_parts(text: str, size: int) -> List[Dict[str, str]]:
    lines = text.split("\n")
    parts, buf, used, first = [], [], 0, 1
    for n, line in enumerate(lines, 1):
        row = f"{n}: {line}"
        if buf and used + len(row) + 1 > size:
            parts.append({"where": f"lines {first}-{n - 1} of {len(lines)}", "text": "\n".join(buf)})
            buf, used, first = [], 0, n
        buf.append(row[:size])
        used += len(row) + 1
    if buf:
        parts.append({"where": f"lines {first}-{len(lines)} of {len(lines)}", "text": "\n".join(buf)})
    return parts


def _passage_parts(passages: List[Tuple[str, str]], size: int) -> List[Dict[str, str]]:
    parts, buf, used, labels = [], [], 0, []
    for label, body in passages:
        block = f"[{label}]\n{body}"
        if buf and used + len(block) + 2 > size:
            parts.append({"where": _span(labels), "text": "\n\n".join(buf)})
            buf, used, labels = [], 0, []
        buf.append(block[:size])
        used += len(block) + 2
        labels.append(label)
    if buf:
        parts.append({"where": _span(labels), "text": "\n\n".join(buf)})
    return parts


def _span(labels: List[str]) -> str:
    return labels[0] if labels[0] == labels[-1] else f"{labels[0]} to {labels[-1]}"


# --- the scan --------------------------------------------------------------

def scan(source: str, question: str, *, session_id: Optional[str] = None, model: Optional[str] = None,
         stop: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Read ``source`` part by part for ``question``. Returns {"memory",
    "parts", "read", "kind", "stopped", "secs"}."""
    from backend.services.plan_mode import active_chat_model
    from backend.utils.ollama_resource_manager import compute_optimal_num_ctx

    model = model or active_chat_model()
    if not model:
        raise ScanRefused("No chat model is active.")
    num_ctx = compute_optimal_num_ctx(model)
    parts, kind = parts_of(source, session_id, part_chars(num_ctx))
    if len(parts) > MAX_PARTS:
        raise ScanRefused(
            f"'{source}' is {len(parts)} parts at this model's window, more than the {MAX_PARTS} a scan "
            "reads. Search for the part you need, or scan a smaller file or one kept result.")
    what = {"file": "file", "result": "tool result", "document": "document"}[kind]
    where = "page or section" if kind == "document" else "line numbers"
    system = _SYSTEM.format(what=what, where=where, where_short="page N" if kind == "document" else "line N",
                            limit=MEMORY_MAX_CHARS)

    progress, pid = _progress_start(source, question, len(parts))
    memory, read, started = "", 0, time.time()
    stopped = False
    try:
        for i, part in enumerate(parts, 1):
            if stop and stop():
                stopped = True
                break
            user = (f"Question: {question}\n\nMemory so far:\n{memory or '(empty)'}\n\n"
                    f"Part {i} of {len(parts)} ({part['where']}):\n{part['text']}")
            reply = _call(model, system, user, num_ctx)
            if reply is not None:
                memory = reply[:MEMORY_MAX_CHARS]
            read = i
            _progress_update(progress, pid, i, len(parts), source)
    except Exception as exc:
        _progress_end(progress, pid, f"Scan failed: {exc}", error=True)
        raise
    _progress_end(progress, pid, "Stopped" if stopped else f"Read {read} of {len(parts)} parts")
    return {"memory": memory.strip(), "parts": len(parts), "read": read, "kind": kind,
            "stopped": stopped, "secs": round(time.time() - started, 1),
            "spans": [p["where"] for p in parts[:read]]}


def _call(model: str, system: str, user: str, num_ctx: int) -> Optional[str]:
    from backend.config import get_chat_keep_alive
    from backend.services.nl_control_plane import _client_for
    from backend.utils.ollama_resource_manager import request_options, think_payload
    try:
        response = _client_for(CALL_TIMEOUT_S).chat(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            options=request_options(model, temperature=0.2, num_ctx=num_ctx, num_predict=REPLY_TOKENS),
            keep_alive=get_chat_keep_alive(),
            **think_payload(model),
        )
    except Exception as exc:  # noqa: BLE001 - one failed part keeps the memory so far
        logger.warning("scan part failed: %s", exc)
        return None
    msg = getattr(response, "message", None) or (response.get("message") if isinstance(response, dict) else None)
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    return (content or "").strip() or None


def _progress_start(source: str, question: str, parts: int):
    try:
        from backend.utils.unified_progress_system import ProcessType, get_unified_progress
        progress = get_unified_progress()
        pid = progress.create_process(ProcessType.ANALYSIS, f"Reading {source} for: {question[:60]}",
                                      additional_data={"operation": "scan", "parts": parts})
        return progress, pid
    except Exception as exc:  # noqa: BLE001 - no footer entry is not a reason to stop
        logger.debug("scan progress unavailable: %s", exc)
        return None, None


def _progress_update(progress, pid, done: int, total: int, source: str) -> None:
    if progress and pid:
        try:
            progress.update_process(pid, int(done * 100 / total), f"Reading {source}: part {done} of {total}")
        except Exception:  # noqa: BLE001
            pass


def _progress_end(progress, pid, message: str, error: bool = False) -> None:
    if progress and pid:
        try:
            (progress.error_process if error else progress.complete_process)(pid, message)
        except Exception:  # noqa: BLE001
            pass
