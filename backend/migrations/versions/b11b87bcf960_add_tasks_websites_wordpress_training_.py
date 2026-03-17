"""Add tasks, websites, wordpress, training tables + fix missing PKs.

Tables created via scripts/fix_missing_tables.sql (raw SQL).
PKs were missing on all existing tables due to SQLite→PostgreSQL migration.
"""

from alembic import op
import sqlalchemy as sa

revision = "b11b87bcf960"
down_revision = "0f596bba4373"
branch_labels = None
depends_on = None


def upgrade():
    # Tables and PKs created via scripts/fix_missing_tables.sql
    # This migration is a stamp-only placeholder.
    pass


def downgrade():
    op.drop_table("wordpress_pages")
    op.drop_table("wordpress_sites")
    op.drop_table("training_jobs")
    op.drop_table("training_datasets")
    op.drop_table("tasks")
    op.drop_table("websites")
    op.drop_table("system_settings")
