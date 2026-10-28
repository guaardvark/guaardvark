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
     (default 3) and whose source is a knowledge file (not ``model_belief``),
     synthesise a unified diff against the source file and stage it as a
     ``PendingFix`` row so the user can approve/reject from the existing
     self-improvement UI. A Markdown claim gets an evidence-tagged hedge on its
     line; a recipe in ``recipes.json`` gets a ``verify_first`` field, which
     the agent's recipe index shows next to that recipe.

It runs on the Celery beat every six hours (``memory.reconcile_belief_updates``;
``GUAARDVARK_RECONCILER_BEAT_DISABLED=1`` turns that off), from the CLI
(``scripts/run_lesson_reconciler.py``) and on demand. A scan only stages
proposals; a person applies them, so nothing here edits a knowledge file.

A group gets one proposal. A proposal for the same file and element that is
open, applied or rejected settles it, and a line that already carries a
belief-update hedge is left alone, so repeated scans never stack hedges or
re-ask a question a person has answered.

Errors degrade gracefully — a single malformed memory row never blocks
processing of the others.
"""

import difflib
import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# How many sessions must agree before we propose a file edit. Three is enough
# to filter one-off hallucinations while keeping the loop responsive — by the
# fourth occurrence the user is likely already frustrated.
DEFAULT_THRESHOLD = 3

# Sources we know how to edit. "model_belief" rows are recorded by Phase 4 to
# keep the next-session prompt honest, but they don't correspond to any line
# in a knowledge file — so the reconciler has nothing to propose for them.
_RECIPES_FILE = "recipes.json"
_EDITABLE_SOURCES = {"self_knowledge_compact.md", "self_knowledge.md", _RECIPES_FILE}

# Marks a line the reconciler has hedged. A line carrying it gets no second hedge.
_HEDGE_MARKER = "<!-- belief-update:"

# JSON has no comments, so a recipe's hedge is a field of the recipe. A recipe
# that already has one gets no second.
RECIPE_NOTE_FIELD = "verify_first"

# PendingFix statuses that settle a (file, element) group: still open, applied,
# or rejected by a person. Only a deleted proposal lets the scan ask again.
_SETTLED_STATUSES = ("proposed", "triaged", "approved", "applied", "rejected")


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
    """Soften a bullet/claim line with an evidence-tagged hedge.

    A blank line, or one already hedged, comes back unchanged.
    """
    if not original.strip() or _HEDGE_MARKER in original:
        return original
    stripped = original.rstrip("\n")
    note = (
        f"  {_HEDGE_MARKER} {sessions_seen} sessions did not see this; "
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


def _click_targets(recipe) -> set:
    steps = recipe.get("steps") if isinstance(recipe, dict) else None
    return {
        (s.get("target_description") or "").strip().lower()
        for s in steps or []
        if isinstance(s, dict) and s.get("action") == "click"
    }


def _build_recipe_diff(
    abs_path: str,
    source_line: Optional[int],
    element: str,
    sessions_seen: int,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[int]]:
    """Propose a ``verify_first`` field on the recipe whose click step names ``element``.

    Returns (original_line, proposed_line, unified_diff, recipe_line). The
    original is the recipe's opening line and the proposal is that line plus
    the new field, so the guarded apply replaces exactly one unique line. The
    recipe is found by its click target, preferring the one opening at
    ``source_line``: a field added to one recipe moves every recipe below it.
    All four are None when no single recipe names the element, it already has
    the field, or the result would not be the same JSON plus that one field.
    """
    nothing = (None, None, None, None)
    try:
        with open(abs_path, encoding="utf-8") as f:
            text = f.read()
        data = json.loads(text)
    except Exception as e:
        logger.warning(f"[RECONCILER] could not read {abs_path}: {e}")
        return nothing
    if not isinstance(data, dict):
        return nothing

    lines = text.splitlines(keepends=True)
    opening: Dict[str, int] = {}
    for idx, raw in enumerate(lines):
        m = re.match(r'\s*"([^"]+)"\s*:\s*\{\s*$', raw)
        if m and m.group(1) in data and m.group(1) not in opening:
            opening[m.group(1)] = idx

    named = [n for n, r in data.items() if not n.startswith("_") and element in _click_targets(r)]
    at_line = next((n for n, idx in opening.items() if idx + 1 == source_line), None)
    if at_line in named:
        name = at_line
    elif len(named) == 1:
        name = named[0]
    else:
        return nothing
    if data[name].get(RECIPE_NOTE_FIELD) or name not in opening:
        return nothing

    idx = opening[name]
    original_line = lines[idx]
    if text.count(original_line) != 1 or idx + 1 >= len(lines):
        return nothing
    following = lines[idx + 1]
    indent = following[: len(following) - len(following.lstrip())]
    note = (
        f"{sessions_seen} sessions did not see {element!r} on screen; "
        f"check the screen before relying on this recipe."
    )
    field_line = f'{indent}"{RECIPE_NOTE_FIELD}": {json.dumps(note)},\n'
    proposed_line = original_line + field_line

    proposed_lines = lines.copy()
    proposed_lines.insert(idx + 1, field_line)
    try:
        result = json.loads("".join(proposed_lines))
    except json.JSONDecodeError:
        return nothing
    expected = json.loads(text)
    expected[name] = {RECIPE_NOTE_FIELD: note, **expected[name]}
    if result != expected:
        return nothing

    rel = os.path.relpath(abs_path, _knowledge_root().rsplit("/data/", 1)[0])
    diff = "".join(difflib.unified_diff(
        lines, proposed_lines,
        fromfile=f"a/{rel}", tofile=f"b/{rel}",
        n=2,
    ))
    return original_line, proposed_line, diff, idx + 1


def _existing_proposal(file_path: str, element: str) -> bool:
    """True if a PendingFix for this (file, element) is open, applied or rejected.

    The element is matched as the quoted name its description starts with, so
    a proposal for "firefox icon" does not settle one for "icon".
    """
    from backend.models import db, PendingFix
    rows = (
        db.session.query(PendingFix)
        .filter(PendingFix.file_path == file_path)
        .filter(PendingFix.status.in_(_SETTLED_STATUSES))
        .all()
    )
    prefix = f"{element.lower()!r} "
    for r in rows:
        if (r.fix_description or "").lower().startswith(prefix):
            return True
    return False


@dataclass
class Candidate:
    """One (file, line, element) group a scan would stage as a PendingFix."""

    source_file: str
    source_line: int
    element: str
    sessions: int
    file_path: str
    original_line: str
    proposed_line: str
    diff: str


@dataclass
class Skipped:
    """A group at the threshold that a scan leaves alone, and why."""

    source_file: str
    source_line: Optional[int]
    element: str
    sessions: int
    reason: str


@dataclass
class Plan:
    memories: int = 0
    groups: int = 0
    candidates: List[Candidate] = field(default_factory=list)
    skipped: List[Skipped] = field(default_factory=list)


def plan_belief_updates(threshold: int = DEFAULT_THRESHOLD) -> Plan:
    """Decide what a scan would stage, without writing anything.

    The real scan stages exactly ``candidates``, so ``--dry-run`` and the beat
    task can never disagree about which proposals a run makes. Groups below
    the threshold are counted in ``groups`` but not listed.
    """
    from backend.models import db, AgentMemory

    memories = (
        db.session.query(AgentMemory)
        .filter(AgentMemory.type == "belief_update")
        # Respect the status column: archived belief rows are retired evidence
        # (e.g. the 2026-08-01 Wave-0 cleanup of key-name-tagged recipes.json
        # rows the group-key parser can't consume) and must not be recounted
        # on every 6h scan.
        .filter(AgentMemory.status == "active")
        .order_by(AgentMemory.created_at)
        .all()
    )

    # Bucket by (file, line, element_lower); count distinct memory rows.
    buckets: Dict[Tuple[str, Optional[int], str], List[AgentMemory]] = defaultdict(list)
    # A field added to one recipe moves every recipe below it, so one recipe
    # claim can arrive under two line numbers. Recipe claims are bucketed by
    # element, keeping the newest line as the hint for finding the recipe.
    recipe_lines: Dict[str, int] = {}
    for m in memories:
        key = _extract_group_key(_parse_tags(m.tags))
        if key is None:
            continue
        if key[0] == _RECIPES_FILE:
            if key[1] is not None:
                recipe_lines[key[2]] = key[1]
            key = (key[0], None, key[2])
        buckets[key].append(m)

    plan = Plan(memories=len(memories), groups=len(buckets))
    # One proposal per (file, element): a second line flagged for the same
    # element waits until the first proposal is settled or deleted.
    planned = set()
    for (source_file, source_line, element_lower), rows in buckets.items():
        sessions = len(rows)
        if sessions < threshold:
            continue

        reason = None
        if source_file not in _EDITABLE_SOURCES:
            # model_belief rows + future-named sources — keep the lesson in the
            # next-session prompt; don't try to edit a file we don't know.
            reason = "not an editable knowledge file"
        elif source_line is None and source_file != _RECIPES_FILE:
            reason = "no line number"
        else:
            abs_path = os.path.join(_knowledge_root(), source_file)
            if (abs_path, element_lower) in planned or _existing_proposal(abs_path, element_lower):
                reason = "already proposed, applied or rejected"
            elif source_file == _RECIPES_FILE:
                original_line, proposed_line, diff, source_line = _build_recipe_diff(
                    abs_path, recipe_lines.get(element_lower), element_lower, sessions)
                if not diff:
                    reason = "no single recipe clicks it, or it already has a note"
            else:
                original_line, proposed_line, diff = _build_diff(abs_path, source_line, sessions)
                if not diff:
                    reason = "line unreadable, blank or already hedged"
        if reason:
            plan.skipped.append(Skipped(source_file, source_line, element_lower, sessions, reason))
            continue

        planned.add((abs_path, element_lower))
        plan.candidates.append(Candidate(
            source_file=source_file,
            source_line=source_line,
            element=element_lower,
            sessions=sessions,
            file_path=abs_path,
            original_line=original_line,
            proposed_line=proposed_line,
            diff=diff,
        ))
    return plan


def stage_belief_updates(threshold: int = DEFAULT_THRESHOLD) -> List[Candidate]:
    """Stage a PendingFix for each candidate of ``plan_belief_updates``.

    Returns the candidates actually staged; one that fails to save is logged
    and left out.
    """
    from backend.models import db, PendingFix

    staged = []
    for c in plan_belief_updates(threshold).candidates:
        try:
            if True:  # ad-hoc
                import logging
                logging.getLogger(__name__).info("PendingFix without run_id (ad-hoc lesson; per infra audit intentional)")

            if c.source_file == _RECIPES_FILE:
                proposal = f"Propose a {RECIPE_NOTE_FIELD} note on the recipe at"
            else:
                proposal = "Propose hedging the claim on"
            fix = PendingFix(
                file_path=c.file_path,
                original_content=c.original_line,
                proposed_new_content=c.proposed_line,
                proposed_diff=c.diff,
                fix_description=(
                    f"{c.element!r} flagged as not-visible across {c.sessions} "
                    f"sessions. {proposal} {c.source_file}:{c.source_line}."
                ),
                severity="low",
                status="proposed",
                reviewed_by="lesson_reconciler",
            )
            db.session.add(fix)
            db.session.commit()
            staged.append(c)
            logger.info(
                f"[RECONCILER] staged pending_fix #{fix.id} for {c.element!r} "
                f"({c.source_file}:{c.source_line}, {c.sessions} sessions)"
            )
        except Exception as e:
            db.session.rollback()
            logger.warning(
                f"[RECONCILER] failed to stage pending_fix for {c.element!r}: {e}"
            )

    return staged


def scan_belief_updates(threshold: int = DEFAULT_THRESHOLD) -> int:
    """Scan belief_update memories and stage PendingFix rows where evidence converges.

    Returns the number of PendingFix rows created on this run. Idempotent —
    running it twice with the same evidence won't create duplicate proposals,
    whether the first one is still open, was applied or was rejected.
    """
    return len(stage_belief_updates(threshold))
