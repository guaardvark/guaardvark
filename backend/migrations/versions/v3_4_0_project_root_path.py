"""projects.root_path: a project's folder on disk.

One nullable column. The add-column list in backend/app.py adds it on boot as
well; ``IF NOT EXISTS`` keeps this revision safe where that already ran.

Revision ID: v3_4_0_project_root_path
Revises: v3_3_0_mv_revisions
Create Date: 2026-10-23
"""
from alembic import op

revision = "v3_4_0_project_root_path"
down_revision = "v3_3_0_mv_revisions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS root_path VARCHAR(2048)")


def downgrade():
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS root_path")
