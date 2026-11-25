"""Outreach drafts can carry an image: social_outreach_log.media_json.

The add-column list in backend/app.py adds the column on boot; ``IF NOT
EXISTS`` keeps this revision safe where that ran.

Revision ID: v3_5_2_outreach_media
Revises: v3_5_1_outreach_source
Create Date: 2026-11-12
"""
from alembic import op

revision = "v3_5_2_outreach_media"
down_revision = "v3_5_1_outreach_source"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE social_outreach_log ADD COLUMN IF NOT EXISTS media_json TEXT")


def downgrade():
    op.execute("ALTER TABLE social_outreach_log DROP COLUMN IF EXISTS media_json")
