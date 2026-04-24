"""lesson pearls — add lesson_id to tool_feedback"""
from alembic import op
import sqlalchemy as sa

revision = "004_lesson_pearls"
down_revision = "003_cluster_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tool_feedback",
        sa.Column("lesson_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_tool_feedback_lesson_id",
        "tool_feedback",
        ["lesson_id"],
    )


def downgrade():
    op.drop_index("ix_tool_feedback_lesson_id", table_name="tool_feedback")
    op.drop_column("tool_feedback", "lesson_id")
