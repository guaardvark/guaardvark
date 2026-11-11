"""Outreach drafts keep what they answer: social_outreach_log.source_json.

The add-column list in backend/app.py adds the column on boot; ``IF NOT
EXISTS`` keeps this revision safe where that ran.

Revision ID: v3_5_1_outreach_source
Revises: v3_5_0_plan_mode
Create Date: 2026-11-11
"""
from alembic import op

revision = "v3_5_1_outreach_source"
down_revision = "v3_5_0_plan_mode"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE social_outreach_log ADD COLUMN IF NOT EXISTS source_json TEXT")


def downgrade():
    op.execute("ALTER TABLE social_outreach_log DROP COLUMN IF EXISTS source_json")
