"""
Plan parser — turns a markdown file into a list of SwarmTasks.

Supports two formats and auto-detects which one you're using:

  Structured: explicit `- files:`, `- depends_on:`, `- backend:` fields
  Freeform:   just headers and prose, parser infers the rest

You can mix both in the same file. Each ## heading starts a new task.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .models import SwarmTask, SwarmStatus, ConflictWarning

logger = logging.getLogger("swarm.plan_parser")

# patterns for detecting structured fields
#
# Each field accepts an optional leading "- " bullet so that both the original
# template style (`- files:`) and the UI / AI-plan-builder style (`Files:`,
# `Assign to:`, `Deps:`) parse identically. All matching is case-insensitive.
#
#   files / file               -> file_scope    (also UI label "Files:")
#   depends_on / depends / deps -> dependencies  (also UI label "Deps:")
#   backend / assign to        -> preferred_backend (UI label "Assign to:")
_FILES_RE = re.compile(r"^-?\s*files?:\s*(.+)", re.IGNORECASE)
_DEPS_RE = re.compile(r"^-?\s*(?:depends?(?:_?on)?|deps):\s*(.+)", re.IGNORECASE)
_BACKEND_RE = re.compile(r"^-?\s*(?:backend|assign\s+to):\s*(.+)", re.IGNORECASE)
_TAG_RE = re.compile(r"\[([\w\s-]+):\s*([^\]]+)\]")

# patterns for inferring file paths from freeform text
_PATH_RE = re.compile(
    r"(?:^|\s|`)"                       # start of line, whitespace, or backtick
    r"((?:[\w.-]+/)+[\w.-]+\.[\w]+)"    # something/like/this.ext
    r"(?:`|\s|$|[,;.])",               # end boundary
)

# dependency inference — phrases that hint at ordering
_DEP_PHRASES = [
    (re.compile(r"(?:depends?\s+on|requires?|needs?|after)\s+(?:the\s+)?['\"]?(\w[\w\s-]{3,}?)['\"]?\s+(?:task|module|step|api|service)", re.IGNORECASE), None),
    (re.compile(r"(?:calls|uses|imports|consumes)\s+(?:the\s+)(\w[\w\s-]{3,}?)(?:'s|\s+(?:api|service|module|endpoint))", re.IGNORECASE), None),
]


def parse_plan(plan_path: str | Path) -> list[SwarmTask]:
    """
    Parse a markdown plan file into SwarmTask objects.

    Each ## heading becomes a task; in a plan that marks its tasks with
    "## Task: ...", only those do, and the rest of the plan is given to every
    task as notes. The parser auto-detects whether each task block is
    structured (has field markers) or freeform.
    """
    plan_path = Path(plan_path)
    if not plan_path.exists():
        raise FileNotFoundError(f"Plan file not found: {plan_path}")

    content = plan_path.read_text(encoding="utf-8")
    blocks = _split_into_blocks(content)

    if not blocks:
        raise ValueError(f"No task blocks found in {plan_path} — need at least one ## heading")

    # A plan that marks its tasks ("## Task: ...") has other sections too:
    # ground rules above the first heading, a "## How to measure". Those are
    # notes for every agent, not agents of their own.
    notes = ""
    if any(_is_marked_task(heading) for heading, _ in blocks):
        notes = _plan_notes(content, [(h, b) for h, b in blocks if not _is_marked_task(h)])
        blocks = [(h, b) for h, b in blocks if _is_marked_task(h)]

    tasks = []
    for heading, body in blocks:
        task = _parse_block(heading, body)
        tasks.append(task)

    # A task id names the task's worktree folder and branch. A heading with no
    # ASCII letters or digits slugs to "", and two headings can slug alike.
    seen: set[str] = set()
    for number, task in enumerate(tasks, start=1):
        base = task.id or f"task-{number}"
        task_id, suffix = base, 2
        while task_id in seen:
            task_id = f"{base}-{suffix}"
            suffix += 1
        seen.add(task_id)
        task.id = task_id

    # resolve freeform dependency references to actual task IDs
    _resolve_dependencies(tasks)

    # Added after the fields and dependencies are read, so a line in the notes
    # is never taken for one task's files or dependencies.
    if notes:
        for task in tasks:
            task.description = (
                f"{task.description}\n\n---\nNotes from the plan that apply to every task:\n\n{notes}"
            ).strip()

    logger.info(f"Parsed {len(tasks)} tasks from {plan_path}")
    for t in tasks:
        logger.debug(f"  {t.id}: {t.title} (files={t.file_scope}, deps={t.dependencies})")

    return tasks


def predict_conflicts(tasks: list[SwarmTask]) -> list[ConflictWarning]:
    """
    Check for file scope overlaps before launching anything.

    Returns warnings for task pairs that touch the same files,
    with a recommendation for each.
    """
    warnings: list[ConflictWarning] = []

    for i, task_a in enumerate(tasks):
        for task_b in tasks[i + 1:]:
            overlap = set(task_a.file_scope) & set(task_b.file_scope)
            if not overlap:
                continue

            # if one already depends on the other, they're serialized — no conflict
            if task_b.id in task_a.dependencies or task_a.id in task_b.dependencies:
                continue

            # figure out a recommendation
            if len(overlap) == 1:
                rec = "serialize"  # one shared file — just run them in order
            elif len(overlap) > len(task_a.file_scope) // 2:
                rec = "merge_scope"  # massive overlap — probably should be one task
            else:
                rec = "proceed"  # some overlap but manageable

            warnings.append(ConflictWarning(
                task_a_id=task_a.id,
                task_b_id=task_b.id,
                overlapping_files=sorted(overlap),
                recommendation=rec,
            ))

    if warnings:
        logger.warning(f"Found {len(warnings)} potential file conflicts in plan")
    return warnings


def auto_serialize_conflicts(
    tasks: list[SwarmTask],
    warnings: list[ConflictWarning],
) -> list[SwarmTask]:
    """
    For Flight Mode — automatically add dependencies to prevent conflicts.

    Instead of asking the user, we just make the second task wait for the first.
    """
    for warning in warnings:
        task_b = next((t for t in tasks if t.id == warning.task_b_id), None)
        if task_b and warning.task_a_id not in task_b.dependencies:
            task_b.dependencies.append(warning.task_a_id)
            logger.info(
                f"Auto-serialized: {warning.task_b_id} now depends on {warning.task_a_id} "
                f"(overlapping: {', '.join(warning.overlapping_files)})"
            )
    return tasks


# ---------------------------------------------------------------------------
# Internal parsing machinery
# ---------------------------------------------------------------------------

def _is_marked_task(heading: str) -> bool:
    return _TAG_RE.sub("", heading).strip().lower().startswith("task:")


def _plan_notes(content: str, sections: list[tuple[str, str]]) -> str:
    """The parts of a plan that are not tasks: the text above the first
    heading (without the plan's own title line) and the unmarked sections."""
    preamble_lines = []
    for line in content.split("\n"):
        if line.startswith("## ") or line.startswith("### "):
            break
        if not line.startswith("# "):
            preamble_lines.append(line)
    parts = ["\n".join(preamble_lines).strip()]
    parts += [f"{heading}\n{body}".strip() for heading, body in sections]
    return "\n\n".join(part for part in parts if part)


def _split_into_blocks(content: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) pairs on ## or ### boundaries.

    Tries ## first. If no ## headings found, falls back to ### so plans
    using deeper heading levels still work.
    """
    blocks = _extract_headings(content, "## ")
    if not blocks:
        # no ## headings — try ### (some plans use these as task boundaries)
        blocks = _extract_headings(content, "### ")
    return blocks


def _extract_headings(content: str, prefix: str) -> list[tuple[str, str]]:
    """Extract (heading, body) pairs for a given heading prefix."""
    blocks: list[tuple[str, str]] = []
    current_heading = None
    current_lines: list[str] = []
    prefix_len = len(prefix)

    for line in content.split("\n"):
        if line.startswith(prefix) and (prefix != "## " or not line.startswith("### ")):
            if current_heading is not None:
                blocks.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = line[prefix_len:].strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)

    # don't forget the last block
    if current_heading is not None:
        blocks.append((current_heading, "\n".join(current_lines).strip()))

    return blocks


def _parse_block(heading: str, body: str) -> SwarmTask:
    """Parse a single task block — auto-detects structured vs freeform."""
    # extract tags from heading and body
    tags: dict[str, str] = {}
    for match in _TAG_RE.finditer(heading):
        tags[match.group(1).strip()] = match.group(2).strip()
    for match in _TAG_RE.finditer(body):
        tags[match.group(1).strip()] = match.group(2).strip()

    # strip tags and "Task:" prefix if present (structured format)
    title = _TAG_RE.sub("", heading).strip()
    if title.lower().startswith("task:"):
        title = title[5:].strip()

    task_id = _slugify(title)

    # check if this block has structured fields
    has_structured = any(
        pattern.search(line)
        for line in body.split("\n")
        for pattern in [_FILES_RE, _DEPS_RE, _BACKEND_RE]
    )

    if has_structured:
        task = _parse_structured(task_id, title, body)
    else:
        task = _parse_freeform(task_id, title, body)
    
    task.tags.update(tags)
    return task


def _parse_structured(task_id: str, title: str, body: str) -> SwarmTask:
    """Parse a block with explicit field markers."""
    file_scope: list[str] = []
    dependencies: list[str] = []
    backend: str | None = None
    description_lines: list[str] = []

    for line in body.split("\n"):
        files_match = _FILES_RE.match(line.strip())
        deps_match = _DEPS_RE.match(line.strip())
        backend_match = _BACKEND_RE.match(line.strip())

        if files_match:
            file_scope = _split_list(files_match.group(1))
        elif deps_match:
            raw = deps_match.group(1).strip()
            if raw.lower() not in ("none", "n/a", "-"):
                dependencies = [_slugify(d) for d in _split_list(raw)]
        elif backend_match:
            raw = backend_match.group(1).strip().lower()
            if raw not in ("any", "auto", "none"):
                backend = raw
        else:
            description_lines.append(line)

    return SwarmTask(
        id=task_id,
        title=title,
        description="\n".join(description_lines).strip(),
        file_scope=file_scope,
        dependencies=dependencies,
        preferred_backend=backend,
        status=SwarmStatus.PENDING,
    )


def _parse_freeform(task_id: str, title: str, body: str) -> SwarmTask:
    """Parse a block with no explicit fields — infer what we can."""
    # infer file paths from the text
    file_scope = _PATH_RE.findall(body)
    # deduplicate while preserving order
    seen: set[str] = set()
    unique_files: list[str] = []
    for f in file_scope:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    # infer dependency references (these are title-based, resolved later)
    dep_refs: list[str] = []
    for pattern, _ in _DEP_PHRASES:
        for match in pattern.finditer(body):
            ref = match.group(1).strip()
            if ref:
                dep_refs.append(ref)

    return SwarmTask(
        id=task_id,
        title=title,
        description=body.strip(),
        file_scope=unique_files,
        dependencies=dep_refs,  # these are fuzzy refs, resolved in _resolve_dependencies
        preferred_backend=None,
        status=SwarmStatus.PENDING,
    )


def _resolve_dependencies(tasks: list[SwarmTask]) -> None:
    """
    Turn fuzzy dependency references into actual task IDs.

    Freeform tasks might reference "the auth API" when the task ID is
    "build-the-auth-api". We do fuzzy matching by comparing slugified
    versions of the reference against all task IDs and titles.
    """
    id_set = {t.id for t in tasks}
    title_to_id = {t.title.lower(): t.id for t in tasks}
    slug_to_id = {_slugify(t.title): t.id for t in tasks}

    for task in tasks:
        resolved: list[str] = []
        for dep in task.dependencies:
            # already a valid task ID?
            if dep in id_set:
                resolved.append(dep)
                continue

            # try slugifying the reference
            dep_slug = _slugify(dep)
            if dep_slug in slug_to_id:
                resolved.append(slug_to_id[dep_slug])
                continue

            # try fuzzy substring match against titles
            dep_lower = dep.lower()
            match = None
            for title_lower, tid in title_to_id.items():
                if dep_lower in title_lower or title_lower in dep_lower:
                    match = tid
                    break

            if match is None:
                # "T2", "task 2", "#2": the plan's second task. Plans a model
                # writes number their tasks this way.
                numbered = re.fullmatch(r"(?:t|task|step|#)?[\s-]*(\d{1,3})", dep_lower.strip())
                if numbered and 1 <= int(numbered.group(1)) <= len(tasks):
                    target = tasks[int(numbered.group(1)) - 1].id
                    match = target if target != task.id else None

            if match:
                resolved.append(match)
            else:
                logger.warning(
                    f"Task '{task.id}': could not resolve dependency '{dep}' "
                    f"to any known task — dropping it"
                )

        # remove self-references and duplicates
        task.dependencies = list(dict.fromkeys(
            d for d in resolved if d != task.id
        ))


def _split_list(raw: str) -> list[str]:
    """Split a field value into a clean list of items.

    Accepts both a bracketed list (`[a, b, c]`) and a bare comma list
    (`a, b, c`). Surrounding brackets and whitespace are stripped, and
    empty items are dropped.
    """
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [item.strip().strip("`'\"") for item in raw.split(",") if item.strip().strip("`'\"")]


def _slugify(text: str) -> str:
    """Turn 'Build the Auth API' into 'build-the-auth-api'."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")
