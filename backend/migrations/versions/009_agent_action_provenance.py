"""agent action provenance table"""

from alembic import op
import sqlalchemy as sa

revision = "009_agent_action_provenance"
down_revision = "008_session_mode"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_action_provenance",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False, index=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("iteration", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("params_snapshot", sa.JSON(), nullable=True),
        sa.Column("approval_scope", sa.String(32), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("outcome_success", sa.Boolean(), nullable=True),
        sa.Column("outcome_preview", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_agent_action_provenance_session",
        "agent_action_provenance",
        ["session_id"],
    )
    op.create_index(
        "ix_agent_action_provenance_created",
        "agent_action_provenance",
        ["created_at"],
    )


def downgrade():
    op.drop_index("ix_agent_action_provenance_created", table_name="agent_action_provenance")
    op.drop_index("ix_agent_action_provenance_session", table_name="agent_action_provenance")
    op.drop_table("agent_action_provenance")
