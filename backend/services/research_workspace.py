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
