"""
Cross-session lesson reconciliation — Phase 5 of see-think-act-remember.

The Phase 4 belief tracker writes one `belief_update` AgentMemory per session
when an element claimed in `data/agent/self_knowledge_compact.md` (or similar
knowledge files) turns out to not be on screen. After enough sessions agree the
same claim is wrong, the *file* itself should change — not just the next-session
prompt. That's what this module does.

Workflow:

  1. Read every AgentMemory of type ``belief_update``.
  2. Group rows by (source_file, source_line, lowercased element name) using
     the structured tag set Phase 4 attaches.
  3. For each group whose count is at or above the reconciliation threshold
     (default 3) and whose source is a real file (not ``model_belief``),
     synthesise a one-line unified diff against the source file proposing a
     hedge-strengthened version of that line — and stage it as a
     ``PendingFix`` row so the user can approve/reject from the existing
     self-improvement UI.

The reconciler is deliberately *not* on the Celery beat schedule. It runs from
the CLI (``scripts/run_lesson_reconciler.py``) or from a manual API call. Auto-
firing every minute would generate noise for groups that haven't crossed the
threshold yet; auto-firing every hour would trigger surprise file edits behind
the user's back. Opt-in is the right cadence.

Errors degrade gracefully — a single malformed memory row never blocks
processing of the others.
"""

import difflib
import json
import logging
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# How many sessions must agree before we propose a file edit. Three is enough
# to filter one-off hallucinations while keeping the loop responsive — by the
# fourth occurrence the user is likely already frustrated.
DEFAULT_THRESHOLD = 3

# Sources we know how to edit. "model_belief" rows are recorded by Phase 4 to
# keep the next-session prompt honest, but they don't correspond to any line
# in a knowledge file — so the reconciler has nothing to propose for them.
_EDITABLE_SOURCES = {"self_knowledge_compact.md", "self_knowledge.md", "recipes.json"}


def _knowledge_root() -> str:
    """Resolve the data/agent/ root. Lazy so test imports don't need backend.config."""
    from backend.config import GUAARDVARK_ROOT
    return os.path.join(GUAARDVARK_ROOT, "data", "agent")


def _parse_tags(raw: Optional[str]) -> List[str]:
    """Decode the JSON-array stored in AgentMemory.tags. Bad data → []."""
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
        return [str(t) for t in decoded] if isinstance(decoded, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _extract_group_key(tags: List[str]) -> Optional[Tuple[str, Optional[int], str]]:
    """Pull (source_file, source_line, element_name) out of a memory's tags.

    Phase 4 writes tags in this shape::

        ["belief_update", "<element name lowercased>", "src:<file>:<line>"]

    where the line is omitted for ``model_belief`` rows.  Returns None when
    the tag set is too malformed to bucket — the caller skips it.
    """
    src_tag = next((t for t in tags if t.startswith("src:")), None)
    if not src_tag:
        return None

    rest = src_tag[len("src:"):]
    if ":" in rest:
        source_file, line_str = rest.rsplit(":", 1)
        try:
            source_line: Optional[int] = int(line_str)
        except ValueError:
            source_file, source_line = rest, None
    else:
        source_file, source_line = rest, None

    element = next(
        (t for t in tags if t and t != "belief_update" and not t.startswith("src:")),
        "",
    )
    if not element:
        return None
    return (source_file, source_line, element.lower())


def _hedged_line(original: str, sessions_seen: int) -> str:
    """Soften a bullet/claim line with an evidence-tagged hedge."""
    if not original.strip():
        return original
    stripped = original.rstrip("\n")
    note = (
        f"  <!-- belief-update: {sessions_seen} sessions did not see this; "
        f"verify before assuming -->"
    )
    return f"{stripped}{note}\n"


def _build_diff(
    abs_path: str,
    source_line: int,
    sessions_seen: int,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Read the file and produce (original_line, proposed_line, unified_diff).

    Returns (None, None, None) when the file or line can't be read — the
    reconciler skips proposing in that case.
    """
    try:
        with open(abs_path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        logger.warning(f"[RECONCILER] could not read {abs_path}: {e}")
        return None, None, None

    if source_line < 1 or source_line > len(lines):
        logger.warning(f"[RECONCILER] line {source_line} out of range in {abs_path}")
        return None, None, None

    idx = source_line - 1
    original_line = lines[idx]
    proposed_line = _hedged_line(original_line, sessions_seen)
    if proposed_line == original_line:
        return None, None, None  # Already hedged or blank — nothing to do.

    proposed_lines = lines.copy()
    proposed_lines[idx] = proposed_line
    rel = os.path.relpath(abs_path, _knowledge_root().rsplit("/data/", 1)[0])
    diff = "".join(difflib.unified_diff(
        lines, proposed_lines,
        fromfile=f"a/{rel}", tofile=f"b/{rel}",
        n=2,
    ))
    return original_line, proposed_line, diff


def _existing_proposal(file_path: str, element: str) -> bool:
    """True if an active PendingFix already proposes a fix for this (file, element)."""
    from backend.models import db, PendingFix
    rows = (
        db.session.query(PendingFix)
        .filter(PendingFix.file_path == file_path)
        .filter(PendingFix.status.in_(("proposed", "triaged", "approved")))
        .all()
    )
    needle = element.lower()
    for r in rows:
        if needle in (r.fix_description or "").lower():
            return True
    return False


def scan_belief_updates(threshold: int = DEFAULT_THRESHOLD) -> int:
    """Scan belief_update memories and stage PendingFix rows where evidence converges.

    Returns the number of PendingFix rows created on this run. Idempotent —
    running it twice with the same evidence won't create duplicate proposals.
    """
    from backend.models import db, AgentMemory, PendingFix

    memories = (
        db.session.query(AgentMemory)
        .filter(AgentMemory.type == "belief_update")
        .all()
    )

    # Bucket by (file, line, element_lower); count distinct memory rows.
    buckets: Dict[Tuple[str, Optional[int], str], List[AgentMemory]] = defaultdict(list)
    for m in memories:
        key = _extract_group_key(_parse_tags(m.tags))
        if key is None:
            continue
        buckets[key].append(m)

    created = 0
    for (source_file, source_line, element_lower), rows in buckets.items():
        if len(rows) < threshold:
            continue
        if source_file not in _EDITABLE_SOURCES:
            # model_belief rows + future-named sources — keep the lesson in the
            # next-session prompt; don't try to edit a file we don't know.
            continue
        if source_line is None:
            continue

        abs_path = os.path.join(_knowledge_root(), source_file)
        if _existing_proposal(abs_path, element_lower):
            continue

        original_line, proposed_line, diff = _build_diff(abs_path, source_line, len(rows))
        if not diff:
            continue

        try:
            fix = PendingFix(
                file_path=abs_path,
                original_content=original_line,
                proposed_new_content=proposed_line,
                proposed_diff=diff,
                fix_description=(
                    f"{element_lower!r} flagged as not-visible across {len(rows)} "
                    f"sessions. Propose hedging the claim on "
                    f"{source_file}:{source_line}."
                ),
                severity="low",
                status="proposed",
                reviewed_by="lesson_reconciler",
            )
            db.session.add(fix)
            db.session.commit()
            created += 1
            logger.info(
                f"[RECONCILER] staged pending_fix #{fix.id} for {element_lower!r} "
                f"({source_file}:{source_line}, {len(rows)} sessions)"
            )
        except Exception as e:
            db.session.rollback()
            logger.warning(
                f"[RECONCILER] failed to stage pending_fix for {element_lower!r}: {e}"
            )

    return created
