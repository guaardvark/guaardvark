"""agent-mode — add mode column to llm_sessions

Adds a `mode` column ("chat"|"agent") so a chat session can be flipped
into agent-mode where every non-slash message routes straight to the
agent loop instead of the chat LLM. Default "chat" so all existing
sessions behave unchanged.
"""
from alembic import op
import sqlalchemy as sa

revision = "008_session_mode"
down_revision = "007_retention_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "llm_sessions",
        sa.Column(
            "mode",
            sa.String(20),
            nullable=False,
            server_default="chat",
        ),
    )


def downgrade():
    op.drop_column("llm_sessions", "mode")
