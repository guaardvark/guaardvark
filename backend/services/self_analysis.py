"""Answering questions about Guaardvark's own code from what is known about it.

When a chat turn asks about Guaardvark itself ("where in your code...", "how
do you decide...", "look into why your ..."), the System Mapper is asked
first: which files the question's words point to, and what those files
import, are imported by, define and are tested by. That goes into the turn
as a short dossier, so a small model starts from the right files instead of
searching blind. Notes from earlier answers ride along while the files they
describe are unchanged.

After the reply, every path and path:line it names is checked against the
checkout; a reference that does not exist is named under the reply rather
than left to read as fact. A reply whose references all hold is kept as a
note for the files it cites.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# The dossier's share of the turn, in characters; the question, history and
# tool results need the rest of an 8192-token context.
DOSSIER_CHARS = 6000
MODULE_SUMMARIES = 2
NOTES_PER_FILE = 2
NOTES_KEPT_PER_FILE = 6
NOTE_CHARS = 500

_SELF = re.compile(r"\b(you|your|yours|yourself|guaardvark'?s?)\b", re.I)
_CODE = re.compile(
    r"\b(code|codebase|source|file|files|function|functions|module|modules|class|method|implemented|"
    r"implementation|route|endpoint|service|tool|tools|backend|frontend|bug|bugs|mapper|system map|"
    r"script|setting|logic|handled?|decides?|works?)\b", re.I)
_PATH_REF = re.compile(
    r"(?<![\w/.-])((?:backend|frontend|cli|scripts|plugins)/[\w./-]+\.(?:py|jsx?|tsx?|json|sh|md))(?::(\d+)(?:-(\d+))?)?")
# "Where do you ...", "how do you ...": a question about how Guaardvark works.
_ASKS = re.compile(r"\b(where|which|how|why)\b", re.I)
_notes_lock = threading.Lock()


def _root() -> Path:
    from backend.config import GUAARDVARK_ROOT
    return Path(GUAARDVARK_ROOT).resolve()


def about_self(message: str, options: Optional[Dict[str, Any]] = None) -> bool:
    """A question about Guaardvark's own code or workings."""
    text = message or ""
    if _PATH_REF.search(text):
        return True
    root = (options or {}).get("project_root")
    if root:
        try:
            if Path(root).resolve() == _root():
                return bool(_CODE.search(text))
        except OSError:
            pass
    if _SELF.search(text) and (_CODE.search(text) or _ASKS.search(text)):
        return True
    # "Where does plan mode ...": a question that names one of Guaardvark's
    # own parts.
    lowered = text.lower()
    return bool(_ASKS.search(text) and any(name in lowered for name in _subsystem_names()))


_SUBSYSTEMS: Optional[List[str]] = None


def _subsystem_names() -> List[str]:
    """Names of Guaardvark's parts (system_mapper/subsystems.json) that are
    specific enough to mark a question about it."""
    global _SUBSYSTEMS
    if _SUBSYSTEMS is None:
        try:
            data = json.loads((Path(__file__).parent / "system_mapper" / "subsystems.json").read_text())
            names = [s["name"].lower() for s in data.get("subsystems", [])]
        except (OSError, ValueError, KeyError):
            names = []
        generic = {"tools", "tests", "cli", "extensions", "jobs and progress", "files and documents"}
        _SUBSYSTEMS = [n for n in names if n not in generic]
    return _SUBSYSTEMS


def _map():
    from backend.tools.workstation_tools import _query_inputs
    root = _root()
    snapshot, index, _note = _query_inputs(root, 0)
    return root, snapshot, index


def dossier(message: str, *, budget: int = DOSSIER_CHARS) -> Tuple[str, List[str]]:
    """(text for the turn, focus files) from the System Mapper, or ("", [])
    when no map is ready yet (one is then being built)."""
    from backend.services.system_mapper import query as q

    try:
        root, snapshot, index = _map()
    except Exception:
        logger.debug("system map unavailable for a self question", exc_info=True)
        return "", []
    if snapshot is None or index is None:
        return "", []
    where = q.answer(root, snapshot, index, "where", message)
    focus = []
    for line in where.splitlines():
        m = re.match(r"\s+((?:backend|frontend|cli|scripts|plugins)/\S+)", line)
        if m and m.group(1) not in focus:
            focus.append(m.group(1))
    parts = [where.strip()]
    for rel in focus[:MODULE_SUMMARIES]:
        parts.append(q.answer(root, snapshot, index, "module", rel).strip())
    notes = notes_for(focus[:4])
    if notes:
        parts.append("Earlier answers about these files (the files are unchanged since):\n" + "\n".join(notes))
    body = "\n\n".join(parts)
    if len(body) > budget:
        body = body[:budget].rsplit("\n", 1)[0] + "\n…"
    header = ("This question is about Guaardvark's own code. The System Mapper's notes on where the answer "
              "is likely to be (read the file, or one function with read_ast_node, before you rely on a "
              "line; name files as paths like backend/services/x.py:120):")
    return f"{header}\n{body}", focus


def check_citations(reply: str) -> Dict[str, Any]:
    """Every path and path:line the reply names, checked against the checkout."""
    root = _root()
    valid, invalid = [], []
    seen = set()
    for path, start, end in _PATH_REF.findall(reply or ""):
        ref = f"{path}:{start}" + (f"-{end}" if end else "") if start else path
        if ref in seen:
            continue
        seen.add(ref)
        target = root / path
        if not target.is_file():
            invalid.append({"ref": ref, "why": "no such file"})
            continue
        if start:
            lines = sum(1 for _ in target.open(encoding="utf-8", errors="replace"))
            last = int(end or start)
            if not (1 <= int(start) <= lines and last <= lines):
                invalid.append({"ref": ref, "why": f"the file has {lines} lines"})
                continue
        valid.append({"ref": ref, "path": path, "line": int(start) if start else None})
    return {"valid": valid, "invalid": invalid}


def invalid_note(report: Dict[str, Any]) -> str:
    refs = ", ".join(f"{i['ref']} ({i['why']})" for i in report["invalid"])
    return f"_Not found in Guaardvark's code: {refs}. Treat those references as unverified._"


# ---------------------------------------------------------------------------
# Notes: what earlier answers established, valid while the files are unchanged
# ---------------------------------------------------------------------------

def _notes_path() -> Path:
    from backend.config import STORAGE_DIR
    return Path(STORAGE_DIR) / "agent" / "code_notes.json"


def _sha(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def _load_notes() -> Dict[str, List[Dict[str, Any]]]:
    try:
        return json.loads(_notes_path().read_text())
    except (OSError, ValueError):
        return {}


def notes_for(files: List[str]) -> List[str]:
    root = _root()
    notes = _load_notes()
    out = []
    for rel in files:
        current = _sha(root / rel)
        kept = [n for n in notes.get(rel, []) if n.get("sha") == current][-NOTES_PER_FILE:]
        out += [f"- {rel}: asked \"{n['question'][:120]}\" — {n['answer'][:NOTE_CHARS]}" for n in kept]
    return out


def remember(question: str, answer: str, report: Dict[str, Any]) -> int:
    """Keep the answer as a note on each file it cites, when every reference
    it made holds. Returns how many files got the note."""
    if report["invalid"] or not report["valid"]:
        return 0
    root = _root()
    files = sorted({v["path"] for v in report["valid"]})
    with _notes_lock:
        notes = _load_notes()
        for rel in files:
            entry = {"question": question[:300], "answer": " ".join(answer.split())[:NOTE_CHARS],
                     "sha": _sha(root / rel), "saved": datetime.now().isoformat(timespec="seconds")}
            notes[rel] = (notes.get(rel, []) + [entry])[-NOTES_KEPT_PER_FILE:]
        path = _notes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(notes, indent=1))
        tmp.replace(path)
    return len(files)
