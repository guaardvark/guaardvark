"""Filename collision resolver.

Single source of truth for "what to call this file when a sibling already
holds the name." Used by upload, move, generator-output, and editor-render
call sites so the rule is applied uniformly.

Rule: Files-app convention. If the desired name is unused in the target
folder, return it as-is. Otherwise append ' (2)', ' (3)', ... before the
extension. Never random hex — that's the anti-pattern this resolver exists
to retire.

Pairs with a `UNIQUE (folder_id, filename) NULLS NOT DISTINCT` constraint
on `documents` (migration 005). The constraint catches anything that
bypasses this resolver; the resolver does the work to keep the constraint
satisfied.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Cap on collision iterations. 999 siblings is absurd; the cap is a sanity
# guard so a programming error producing an infinite same-name loop dies
# loudly instead of spinning. In normal use we never hit 10.
_MAX_COLLISION_ATTEMPTS = 999

# Tolerate a "name (2).ext" already on the user's input — when re-uploading
# what's already a suffixed copy, restart the count from the suffix rather
# than appending " (2) (2)". Pattern matches the Files-app convention.
_SUFFIX_PATTERN = re.compile(r"^(?P<base>.*) \((?P<n>\d+)\)$")


class FilenameCollisionError(Exception):
    """Raised when more than _MAX_COLLISION_ATTEMPTS siblings hold the same
    base name. Indicates a bug in the caller, not a user-data problem."""


def _split_existing_suffix(stem: str) -> tuple[str, int]:
    """If stem already ends in ' (N)', return (base, N) so we resume from there.

    Example: 'report (3)' → ('report', 3).
    Plain stems return (stem, 1) meaning "next collision should try (2)."
    """
    m = _SUFFIX_PATTERN.match(stem)
    if not m:
        return stem, 1
    return m.group("base"), int(m.group("n"))


def resolve_filename(
    folder_id: Optional[int],
    desired_name: str,
    db_session,
    document_model,
    *,
    exclude_id: Optional[int] = None,
) -> str:
    """Pick a name that doesn't collide with any sibling Document.

    Args:
        folder_id: Target folder's primary key, or None for root.
        desired_name: The name the caller wants (e.g. 'report.pdf').
        db_session: SQLAlchemy session for the collision check.
        document_model: The Document SQLAlchemy class. Passed in to avoid
            a circular import at module load time (this util is imported
            from `register_file`, which is imported from API blueprints
            that are auto-discovered before models are guaranteed to be
            ready in some boot orders).
        exclude_id: When renaming an existing row in place, pass its id
            so we don't see ourselves as a collision.

    Returns:
        A name guaranteed unused by any other row with the same folder_id.

    Raises:
        FilenameCollisionError if more than _MAX_COLLISION_ATTEMPTS siblings
        hold the same base name.
    """
    base_stem, ext = os.path.splitext(desired_name)
    base_stem, start_n = _split_existing_suffix(base_stem)
    candidate = desired_name
    attempt = start_n

    while True:
        query = db_session.query(document_model).filter(
            document_model.folder_id == folder_id,
            document_model.filename == candidate,
        )
        if exclude_id is not None:
            query = query.filter(document_model.id != exclude_id)

        if query.first() is None:
            return candidate

        attempt += 1
        if attempt > _MAX_COLLISION_ATTEMPTS:
            raise FilenameCollisionError(
                f"More than {_MAX_COLLISION_ATTEMPTS} files named like "
                f"{desired_name!r} in folder_id={folder_id}. Probable bug."
            )

        candidate = f"{base_stem} ({attempt}){ext}"


def find_duplicate_groups(db_session, document_model) -> list[dict]:
    """Pre-flight audit: find every (folder_id, filename) duplicate group.

    Used by the migration's pre-flight phase. Returns a list of
    {folder_id, filename, ids: [...]} for each duplicated combination,
    so the migration can rename the latecomers in place before adding
    the UNIQUE constraint.

    Empty result means the constraint can be added cleanly.
    """
    from sqlalchemy import func

    rows = (
        db_session.query(
            document_model.folder_id,
            document_model.filename,
            func.count().label("n"),
            func.array_agg(document_model.id).label("ids"),
        )
        .group_by(document_model.folder_id, document_model.filename)
        .having(func.count() > 1)
        .all()
    )

    return [
        {"folder_id": r.folder_id, "filename": r.filename, "ids": list(r.ids)}
        for r in rows
    ]
