"""retention_audit — audit trail for every deletion in the system

Per plans/2026-04-29-data-retention.md §6. Default retention across the
system is keep-forever; auto-purge is opt-in per kind from a Settings UI.
Every actual deletion (manual, bulk, scheduled) writes a row here so
business/legal contexts have provenance for what was removed and why.

Used by /api/admin/retention-audit for export. Not auto-pruned —
the audit log itself is the load-bearing record.
"""
from alembic import op
import sqlalchemy as sa


revision = "007_retention_audit"
down_revision = "006_job_history"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "retention_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.String(length=32), nullable=False),  # 'user' | 'system'
        sa.Column("kind", sa.String(length=64), nullable=False),  # job_history, chat, cache, ...
        sa.Column("operation", sa.String(length=64), nullable=False),  # manual_delete | bulk_delete | auto_purge
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("bytes_freed", sa.BigInteger(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=True),  # filters / thresholds used
        sa.Column("triggered_by", sa.String(length=255), nullable=True),  # user_id | scheduler:<task_name>
    )
    op.create_index(
        "ix_retention_audit_occurred",
        "retention_audit",
        [sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_retention_audit_kind",
        "retention_audit",
        ["kind"],
    )


def downgrade():
    op.drop_index("ix_retention_audit_kind", table_name="retention_audit")
    op.drop_index("ix_retention_audit_occurred", table_name="retention_audit")
    op.drop_table("retention_audit")
