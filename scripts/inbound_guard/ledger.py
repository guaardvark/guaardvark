"""Append-only JSONL record of verdicts and approvals.

The file lives in the git common directory, so every worktree of a clone shares
one record and nothing in it can be committed by accident. The backend reads the
same file to show git-side verdicts beside its own.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Set

try:
    import fcntl
except ImportError:  # Windows: appends of one short line are atomic enough there
    fcntl = None  # type: ignore[assignment]

LEDGER_NAME = "ledger.jsonl"
ROTATE_BYTES = 5 * 1024 * 1024


def now_iso() -> str:
    """Local time as ISO-8601 with a ``±HH:MM`` offset, which every browser parses."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def guard_dir(common_dir: Path) -> Path:
    return common_dir / "inbound-guard"


def ledger_path(common_dir: Path) -> Path:
    return guard_dir(common_dir) / LEDGER_NAME


def append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    record.setdefault("ts", now_iso())
    line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            if fh.tell() > ROTATE_BYTES:
                os.replace(path, path.with_suffix(".jsonl.1"))
                with open(path, "a", encoding="utf-8") as fresh:
                    fresh.write(line)
                return
            fh.write(line)
        finally:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)


def records(path: Path) -> Iterator[dict]:
    for candidate in (path.with_suffix(".jsonl.1"), path):
        if not candidate.exists():
            continue
        with open(candidate, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def recent(path: Path, limit: int = 20) -> List[dict]:
    items = list(records(path))
    return items[-limit:]


def recent_digests(path: Path, tail_bytes: int = 65536) -> Set[str]:
    """Digests of verdicts near the end of the ledger, read without loading it all.

    A merge is judged by pre-merge-commit and then seen again when main moves;
    this lets the second look stay quiet about what the first already reported.
    """
    if not path.exists():
        return set()
    with open(path, "rb") as fh:
        fh.seek(max(0, path.stat().st_size - tail_bytes))
        chunk = fh.read().decode("utf-8", "replace")
    found = set()
    for line in chunk.splitlines()[1:] if len(chunk) >= tail_bytes else chunk.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("kind") == "verdict" and record.get("digest"):
            found.add(record["digest"])
    return found


def approval_for(path: Path, digest: str) -> Optional[dict]:
    """The approval recorded for exactly this change, if any."""
    found = None
    for record in records(path):
        if record.get("kind") == "approval" and record.get("digest") == digest:
            found = record
    return found
