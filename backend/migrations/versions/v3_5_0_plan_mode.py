"""Plan mode: llm_sessions.plan_mode and the chat_plans table.

The add-column list in backend/app.py adds the column on boot and create_all
makes the table; ``IF NOT EXISTS`` keeps this revision safe where those ran.

Revision ID: v3_5_0_plan_mode
Revises: v3_4_0_project_root_path
Create Date: 2026-10-26
"""
from alembic import op

revision = "v3_5_0_plan_mode"
down_revision = "v3_4_0_project_root_path"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE llm_sessions ADD COLUMN IF NOT EXISTS plan_mode BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_plans (
            id VARCHAR(32) PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL
                CONSTRAINT fk_chatplan_session_id REFERENCES llm_sessions (id) ON DELETE CASCADE,
            project_id INTEGER,
            parent_id VARCHAR(32),
            revision INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(24) NOT NULL DEFAULT 'awaiting_approval',
            paused_reason VARCHAR(24),
            objective TEXT NOT NULL,
            goal TEXT,
            steps JSON NOT NULL DEFAULT '[]'::json,
            checks JSON NOT NULL DEFAULT '[]'::json,
            risks JSON NOT NULL DEFAULT '[]'::json,
            questions JSON NOT NULL DEFAULT '[]'::json,
            warnings JSON NOT NULL DEFAULT '[]'::json,
            markdown TEXT,
            attachments JSON NOT NULL DEFAULT '[]'::json,
            options JSON NOT NULL DEFAULT '{}'::json,
            approval_mode VARCHAR(24),
            granted_tools JSON NOT NULL DEFAULT '[]'::json,
            model VARCHAR(160),
            wording_sha VARCHAR(16),
            compose_path VARCHAR(16),
            research JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            approved_at TIMESTAMP WITHOUT TIME ZONE,
            finished_at TIMESTAMP WITHOUT TIME ZONE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_plans_session_id ON chat_plans (session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_plans_status ON chat_plans (status)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS chat_plans")
    op.execute("ALTER TABLE llm_sessions DROP COLUMN IF EXISTS plan_mode")
