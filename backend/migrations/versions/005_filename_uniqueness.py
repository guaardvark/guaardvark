"""filename uniqueness — UNIQUE (folder_id, filename) NULLS NOT DISTINCT on documents

Adds the constraint that anchors the filename-structure plan
(plans/2026-04-29-filename-structure.md). Pre-flight: any existing
duplicate (folder_id, filename) combinations get renamed in place using
the resolver's Files-app suffix convention BEFORE the constraint lands,
so the migration can't fail on legacy duplicates.

Postgres 15+ syntax (`NULLS NOT DISTINCT`) — confirmed PG16 in this deploy.
"""
from alembic import op
import sqlalchemy as sa
import os
import re
import logging

revision = "005_filename_uniqueness"
down_revision = "004_lesson_pearls"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def _split_existing_suffix(stem: str) -> tuple[str, int]:
    m = re.match(r"^(?P<base>.*) \((?P<n>\d+)\)$", stem)
    if not m:
        return stem, 1
    return m.group("base"), int(m.group("n"))


def _next_free(conn, folder_id, desired):
    """Find the next free name for (folder_id, desired). Pure SQL — can't
    import the Python resolver here because alembic env may not have the
    full app context at migration time."""
    base_stem, ext = os.path.splitext(desired)
    base_stem, attempt = _split_existing_suffix(base_stem)
    candidate = desired
    while True:
        existing = conn.execute(
            sa.text(
                "SELECT 1 FROM documents WHERE "
                "(folder_id IS NOT DISTINCT FROM :fid) AND filename = :fn LIMIT 1"
            ),
            {"fid": folder_id, "fn": candidate},
        ).first()
        if existing is None:
            return candidate
        attempt += 1
        if attempt > 999:
            raise RuntimeError(f"too many collisions for {desired!r}")
        candidate = f"{base_stem} ({attempt}){ext}"


def upgrade():
    conn = op.get_bind()

    # Pre-flight: enumerate every (folder_id, filename) duplicate group, ordered
    # by id so the OLDEST row keeps its name and newer rows get suffixed. This
    # mirrors what would have happened if the resolver had been in place.
    dup_groups = conn.execute(
        sa.text(
            """
            SELECT folder_id, filename, array_agg(id ORDER BY id) AS ids
            FROM documents
            GROUP BY folder_id, filename
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    if dup_groups:
        logger.info(
            "Migration 005: %d duplicate (folder_id, filename) groups found; renaming",
            len(dup_groups),
        )
        for row in dup_groups:
            folder_id, filename, ids = row.folder_id, row.filename, row.ids
            # First id keeps the name; rename the rest with the next-free suffix
            for doc_id in ids[1:]:
                new_name = _next_free(conn, folder_id, filename)
                conn.execute(
                    sa.text("UPDATE documents SET filename = :fn WHERE id = :id"),
                    {"fn": new_name, "id": doc_id},
                )
                logger.info(
                    "  doc id=%d in folder %s: %r → %r",
                    doc_id, folder_id, filename, new_name,
                )

    # Drop the existing NON-UNIQUE composite index if present — older DB
    # snapshots may not have this index even though it's declared in the
    # model (create_all() builds it for fresh DBs but no prior migration
    # added it for live ones). IF EXISTS keeps this migration safe across
    # both shapes.
    op.execute("DROP INDEX IF EXISTS ix_doc_folder_filename")

    # Add the unique constraint. NULLS NOT DISTINCT means root-level docs
    # (folder_id IS NULL) also have to have unique filenames among themselves,
    # which is what users expect in a Files-app-style flat root.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_doc_folder_filename
        ON documents (folder_id, filename) NULLS NOT DISTINCT
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_doc_folder_filename")
    # Recreate the non-unique index only if it didn't already exist (some
    # deploys never had it before the upgrade ran).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_doc_folder_filename "
        "ON documents (folder_id, filename)"
    )
    # Note: we don't un-rename the suffixed dups on downgrade. The renamed
    # rows keep their new names (no easy way to know which were renamed),
    # but downgrade restores the non-unique index so they can coexist.
