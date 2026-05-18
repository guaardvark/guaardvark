"""job_history — persistent terminal-status snapshot table

Establishes the persistence layer the unified Tasks/Jobs page reads its
History tab from. Rows land here whenever a Job reaches a terminal
status (completed/failed/cancelled). No automatic pruning — per the
data retention plan (plans/2026-04-29-data-retention.md), default
retention is keep-forever; auto-purge is opt-in per kind from a
Settings UI that lands in a separate slice.

Designed to be denormalized: every column needed for the History tab
is here so the page never has to JOIN against the native source table
(Task / TrainingJob / etc.). The native table can have its row deleted
without losing history.
"""
from alembic import op
import sqlalchemy as sa


revision = "006_job_history"
down_revision = "005_filename_uniqueness"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_history",
        sa.Column("id", sa.String(length=255), primary_key=True),  # "{kind}:{native_id}"
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("native_id", sa.String(length=255), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),  # terminal status only
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.String(length=255), nullable=True),
        sa.Column("job_metadata", sa.JSON(), nullable=True),  # named to avoid SA reserved word
        sa.Column("recorded_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # Index by kind + finished_at for the per-kind history filter on the page.
    op.create_index(
        "ix_job_history_kind_finished",
        "job_history",
        ["kind", sa.text("finished_at DESC")],
    )

    # Index by finished_at alone for the unfiltered "all kinds" history view.
    op.create_index(
        "ix_job_history_finished",
        "job_history",
        [sa.text("finished_at DESC")],
    )

    # Index on status so the summary chip "<N> failed (24h)" is cheap.
    op.create_index(
        "ix_job_history_status",
        "job_history",
        ["status"],
    )


def downgrade():
    op.drop_index("ix_job_history_status", table_name="job_history")
    op.drop_index("ix_job_history_finished", table_name="job_history")
    op.drop_index("ix_job_history_kind_finished", table_name="job_history")
    op.drop_table("job_history")
