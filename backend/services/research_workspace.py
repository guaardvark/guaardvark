"""What a chat keeps while it researches: tool results in full, under a handle.

The chat model reads each tool result through a window (the tool's
``observation_chars``). A result longer than that used to be cut with
"... [truncated]" and the rest was gone: the model could not read on, and a
file of 342 lines looked like a file of 48. Here every cut result is kept in
full on disk for the chat session, under a short id (R1, R2, ...), and the
model is told how much it saw and the exact call that reads the rest
(``read_result``, backend/tools/research_tools.py).

Kept results live in ``<CACHE_DIR>/tool_results/<session>/``, one text file
per result. They are working material for the conversation, not a record:
a result is kept up to MAX_RESULT_CHARS and a session's folder is removed
TTL_DAYS after it was last written.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Longest result kept, in characters. A cat of the largest source file in the
# tree is about 300,000; a page dump or a log tail rarely passes 1,000,000.
MAX_RESULT_CHARS = 2_000_000
# Days a session's kept results stay after their last write.
TTL_DAYS = 7
# Characters of numbered lines one read_result call returns. Fits inside the
# tool's observation_chars with its header and footer.
READ_PAGE_CHARS = 5500
# Most matching lines one pattern search returns.
MAX_PATTERN_HITS = 60

_lock = threading.Lock()
_last_sweep = 0.0
_SWEEP_EVERY_S = 3600


def _root() -> Path:
    try:
        from backend.config import CACHE_DIR
        base = Path(CACHE_DIR)
    except Exception:  # noqa: BLE001 - a bare import (tests, scripts) still works
        base = Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "data" / "cache"
    return base / "tool_results"


def _session_dir(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or "no-session"))[:120]
    return _root() / safe


def _sweep_expired() -> None:
    """Remove session folders not written for TTL_DAYS. Runs at most hourly."""
    global _last_sweep
    now = time.time()
    if now - _last_sweep < _SWEEP_EVERY_S:
        return
    _last_sweep = now
    root = _root()
    if not root.is_dir():
        return
    cutoff = now - TTL_DAYS * 86400
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            continue


def _index_path(folder: Path) -> Path:
    return folder / "index.json"


def _load_index(folder: Path) -> Dict[str, Any]:
    try:
        return json.loads(_index_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"results": {}}


def keep(session_id: str, tool_name: str, params: Optional[Dict[str, Any]], text: str) -> Dict[str, Any]:
    """Keep ``text`` in full for the session and return its handle.

    Returns {"id", "chars", "lines", "clipped"}: ``chars``/``lines`` describe
    the whole text; ``clipped`` is True when it was longer than
    MAX_RESULT_CHARS and only the start was kept.
    """
    text = text or ""
    clipped = len(text) > MAX_RESULT_CHARS
    if clipped:
        text = text[:MAX_RESULT_CHARS]
    folder = _session_dir(session_id)
    with _lock:
        _sweep_expired()
        folder.mkdir(parents=True, exist_ok=True)
        index = _load_index(folder)
        rid = f"R{len(index['results']) + 1}"
        (folder / f"{rid}.txt").write_text(text, encoding="utf-8")
        lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        index["results"][rid] = {
            "tool": tool_name,
            "params": {k: str(v)[:200] for k, v in (params or {}).items()},
            "chars": len(text),
            "lines": lines,
            "clipped": clipped,
            "kept_at": time.time(),
        }
        _index_path(folder).write_text(json.dumps(index), encoding="utf-8")
    return {"id": rid, "chars": len(text), "lines": lines, "clipped": clipped}


def load(session_id: str, rid: str) -> Optional[str]:
    """The kept text of ``rid`` in this session, or None."""
    rid = str(rid or "").strip().upper()
    if not re.fullmatch(r"R\d+", rid):
        return None
    path = _session_dir(session_id) / f"{rid}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def describe(session_id: str, rid: str) -> Optional[Dict[str, Any]]:
    """What ``rid`` was: the tool, its arguments and its size."""
    return _load_index(_session_dir(session_id))["results"].get(str(rid or "").strip().upper())


def cut_with_handle(session_id: str, tool_name: str, params: Optional[Dict[str, Any]],
                    full_text: str, shown_prefix: str, budget: int) -> tuple:
    """The observation the model reads for a result longer than its budget,
    and the id the whole output is kept under.

    ``shown_prefix`` is everything of the observation that precedes the
    output (tag, status, metadata); ``full_text`` is the output itself. The
    output is kept under a handle, the head that fits is shown, and a footer
    says how much was shown and the call that reads on.
    """
    from backend.utils.text_cut import cut_on_whitespace

    kept = keep(session_id, tool_name, params, full_text)
    footer_room = 420
    room = max(200, budget - len(shown_prefix) - footer_room)
    head = cut_on_whitespace(full_text, room)
    shown_lines = head.count("\n") + 1
    # The last shown line may be partial; reading on starts at it again.
    next_line = shown_lines if not head.endswith("\n") else shown_lines + 1
    footer = (
        f"\n[Shown {len(head):,} of {kept['chars']:,} characters, lines 1-{shown_lines} of {kept['lines']}. "
        f"The whole result is kept as {kept['id']}. Read on with "
        f"read_result(id=\"{kept['id']}\", start_line={next_line}), or find lines with "
        f"read_result(id=\"{kept['id']}\", pattern=\"<words or regex>\").]"
    )
    if kept["clipped"]:
        footer += f"\n[{kept['id']} keeps the first {MAX_RESULT_CHARS:,} characters; the output was longer.]"
    if tool_name == "system_command":
        src = _source_of({"params": params or {}})
        if src:
            log_read(session_id, src, 1, max(1, next_line - 1), kept["lines"])
    hint = _file_hint(tool_name, params, next_line)
    if hint:
        footer += f"\n[{hint}]"
    return f"{shown_prefix}{head}{footer}", kept["id"]


def _file_hint(tool_name: str, params: Optional[Dict[str, Any]], next_line: int) -> str:
    """For a shell read of one file, the reader made for files."""
    if tool_name != "system_command":
        return ""
    command = str((params or {}).get("command") or "")
    m = re.match(r"\s*cat\s+([^\s|;&<>]+)\s*$", command)
    if not m:
        return ""
    return f"For a file, read_code(filepath=\"{m.group(1)}\", start_line={next_line}) pages it too."


def read_lines(text: str, start_line: Optional[int], end_line: Optional[int],
               max_chars: int = READ_PAGE_CHARS) -> Dict[str, Any]:
    """Numbered lines of ``text`` from ``start_line``, as many as fit.

    Returns {"text", "first", "last", "total", "past_end"}. A start past the
    last line is not an error: it returns no lines and ``past_end`` True, so
    a reader that pages to the end hears "that was the end".
    """
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    total = len(lines)
    first = max(1, int(start_line or 1))
    if first > total:
        return {"text": "", "first": first, "last": first - 1, "total": total, "past_end": True}
    wanted_last = min(int(end_line), total) if end_line else total
    out, used = [], 0
    for n in range(first, wanted_last + 1):
        line = f"{n}: {lines[n - 1]}"
        if out and used + len(line) + 1 > max_chars:
            break
        if not out and len(line) > max_chars:
            line = line[:max_chars]
        out.append(line)
        used += len(line) + 1
    last = first + len(out) - 1
    return {"text": "\n".join(out), "first": first, "last": last, "total": total, "past_end": False}


def find_lines(text: str, pattern: str, max_hits: int = MAX_PATTERN_HITS) -> Dict[str, Any]:
    """Lines of ``text`` that match ``pattern`` (a regex, or plain words when
    it is not a valid regex), numbered, case-insensitive."""
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    hits, count = [], 0
    for n, line in enumerate(text.split("\n"), 1):
        if rx.search(line):
            count += 1
            if len(hits) < max_hits:
                hits.append(f"{n}: {line[:300]}")
    return {"text": "\n".join(hits), "count": count, "shown": len(hits)}


# --- Notes and reading log -------------------------------------------------
#
# The chat model's window rolls: old tool results are pruned to fit, and a
# small model forgets what it read three calls ago. The notes pad is what it
# chooses to keep (written with the note tool, rewritten when full, the
# MemAgent pattern); the reading log is what it has read, kept by the engine
# without a model call. Both are shown on every step and at the next turn of
# the same chat, so research builds up instead of starting over.

# Longest notes pad, in characters (about 400 tokens: a fifth of what an
# 8,192-token window leaves after the system prompt and tools).
NOTES_MAX_CHARS = 1500
# Sources the reading log lists, most recent first.
READING_LOG_SOURCES = 12


def _notes_path(session_id: str) -> Path:
    return _session_dir(session_id) / "notes.md"


def notes(session_id: str) -> str:
    try:
        return _notes_path(session_id).read_text(encoding="utf-8")
    except OSError:
        return ""


def write_note(session_id: str, text: str, replace: bool = False) -> Dict[str, Any]:
    """Add ``text`` to the notes (or replace them). Refuses, without writing,
    when the notes would pass NOTES_MAX_CHARS: the model rewrites them shorter."""
    text = (text or "").strip()
    with _lock:
        folder = _session_dir(session_id)
        folder.mkdir(parents=True, exist_ok=True)
        current = "" if replace else notes(session_id)
        updated = f"{current.rstrip()}\n- {text}".strip() if current and text else (
            text if text.startswith("- ") or not text else f"- {text}")
        if len(updated) > NOTES_MAX_CHARS:
            return {"ok": False, "chars": len(current), "notes": current}
        _notes_path(session_id).write_text(updated, encoding="utf-8")
    return {"ok": True, "chars": len(updated), "notes": updated}


def _reading_path(session_id: str) -> Path:
    return _session_dir(session_id) / "reading.json"


def _load_reading(session_id: str) -> Dict[str, Any]:
    try:
        return json.loads(_reading_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def log_read(session_id: str, source: str, first: int, last: int, total: Optional[int],
             unit: str = "lines") -> None:
    """Record that ``source`` was read from ``first`` to ``last`` (inclusive)."""
    if not session_id or not source or last < first:
        return
    with _lock:
        folder = _session_dir(session_id)
        folder.mkdir(parents=True, exist_ok=True)
        log = _load_reading(session_id)
        entry = log.get(source) or {"spans": [], "total": total, "unit": unit}
        spans = sorted(entry["spans"] + [[int(first), int(last)]])
        merged: list = []
        for a, b in spans:
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        entry.update({"spans": merged, "total": total or entry.get("total"), "unit": unit,
                      "at": time.time()})
        log[source] = entry
        _reading_path(session_id).write_text(json.dumps(log), encoding="utf-8")


def reading_summary(session_id: str) -> str:
    """One line per source read in this chat: which parts, of how many."""
    log = _load_reading(session_id)
    rows = sorted(log.items(), key=lambda kv: kv[1].get("at", 0), reverse=True)[:READING_LOG_SOURCES]
    out = []
    for source, e in rows:
        spans = ", ".join(f"{a}-{b}" if a != b else f"{a}" for a, b in e["spans"])
        total = e.get("total")
        whole = total and e["spans"] == [[1, total]]
        tail = " (all of it)" if whole else (f" of {total}" if total else "")
        out.append(f"- {source}: {e.get('unit', 'lines')} {spans}{tail}")
    return "\n".join(out)


def research_block(session_id: str) -> str:
    """The notes and the reading log, as the model reads them; empty when
    nothing has been noted or read in this chat."""
    if not session_id:
        return ""
    pad, read = notes(session_id), reading_summary(session_id)
    if not pad and not read:
        return ""
    parts = []
    if read:
        parts.append(f"What you have read in this chat:\n{read}")
    parts.append(f"Your notes ({len(pad)}/{NOTES_MAX_CHARS} characters):\n{pad}" if pad else
                 "Your notes: none yet. Keep what you find with note(text=\"...\") so it is not lost "
                 "when old results scroll out of view.")
    return "\n\n".join(parts)


def log_tool_read(session_id: str, tool_name: str, params: Optional[Dict[str, Any]],
                  metadata: Optional[Dict[str, Any]]) -> None:
    """Add a reading tool's result to the reading log, from what it reports."""
    p, m = params or {}, metadata or {}
    try:
        if tool_name == "read_code" and m.get("total_lines") is not None:
            path = p.get("filepath") or p.get("file_path") or p.get("path") or m.get("filepath")
            if m.get("past_end"):
                return
            first = m.get("start_line") or 1
            last = m.get("end_line") or m.get("total_lines")
            log_read(session_id, str(path), first, last, m.get("total_lines"))
        elif tool_name == "read_result" and m.get("id"):
            info = describe(session_id, m["id"]) or {}
            source = _source_of(info) or m["id"]
            log_read(session_id, source, m["first"], m["last"], m.get("total"))
        elif tool_name == "read_document_section" and m.get("passages"):
            name = p.get("source_filename") or "?"
            where = p.get("page_label") or p.get("heading_path")
            source = f"{name}" + (f" ({where})" if where else "")
            first = int(p.get("offset") or 0) + 1
            log_read(session_id, source, first, first + int(m["passages"]) - 1,
                     None, unit="passages")
    except Exception as exc:  # noqa: BLE001 - the log is a help, never a failure
        logger.debug("reading log skipped for %s: %s", tool_name, exc)


def _source_of(info: Dict[str, Any]) -> Optional[str]:
    """The file a kept result came from, when it was a shell read of one file."""
    command = str((info.get("params") or {}).get("command") or "")
    m = re.match(r"\s*cat\s+([^\s|;&<>]+)\s*$", command)
    return m.group(1) if m else None


def read_sources(session_id: str) -> set:
    """The files and documents this chat has read any part of."""
    return set(_load_reading(session_id)) if session_id else set()
