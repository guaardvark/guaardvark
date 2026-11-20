"""Night shift: tasks.queue_rank, the drag order of jobs scheduled for later.

The add-column list in backend/app.py adds the column on boot; ``IF NOT
EXISTS`` keeps this revision safe where that ran.

Revision ID: v3_7_0_night_shift
Revises: v3_5_0_plan_mode
Create Date: 2026-11-19
"""
from alembic import op

revision = "v3_7_0_night_shift"
down_revision = "v3_5_0_plan_mode"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS queue_rank DOUBLE PRECISION")


def downgrade():
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS queue_rank")
