"""Guaardvark v2.5.2 — Full consolidated schema.

All schema is defined in models.py and created by db.create_all().
This migration exists solely as an Alembic head for stamping.

On fresh installs: db.create_all() creates tables, then stamp to this revision.
On existing installs: db.create_all() adds any missing tables, then stamp to this revision.
No migration replay is ever needed.

Revision ID: v2_5_2_full
Revises: (none — new base after migration flatten)
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = 'v2_5_2_full'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Schema is managed by db.create_all() from models.py.
    # This migration is a no-op stamp target.
    pass


def downgrade():
    # No downgrade — this is the base state.
    pass
