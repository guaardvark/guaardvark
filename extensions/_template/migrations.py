"""Columns added after a version shipped. create_all() creates tables but never
alters them, so a new column on an existing table goes here. The loader checks
whether the column exists before running the DDL, so plain ADD COLUMN is
enough and portable. ``migrate(db)`` is the escape hatch for anything else."""

ADD_COLUMNS = [
    ("template_notes", "pinned", "ALTER TABLE template_notes ADD COLUMN pinned BOOLEAN"),
]


def migrate(db):
    """Optional. Runs after ADD_COLUMNS, inside the app context."""
    return None
