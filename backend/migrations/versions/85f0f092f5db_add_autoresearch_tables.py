"""add autoresearch tables

Revision ID: 85f0f092f5db
Revises: 72d866c5aac9
Create Date: 2026-03-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "85f0f092f5db"
down_revision = "72d866c5aac9"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if "experiment_runs" not in existing_tables:
        op.create_table(
            "experiment_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_tag", sa.String(100), nullable=True, index=True),
            sa.Column("phase", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("parameter_changed", sa.String(200), nullable=False),
            sa.Column("old_value", sa.String(500), nullable=True),
            sa.Column("new_value", sa.String(500), nullable=False),
            sa.Column("hypothesis", sa.Text(), nullable=True),
            sa.Column("composite_score", sa.Float(), nullable=False),
            sa.Column("baseline_score", sa.Float(), nullable=True),
            sa.Column("delta", sa.Float(), nullable=True),
            sa.Column(
                "status", sa.String(20), nullable=False, server_default="discard"
            ),
            sa.Column("eval_details", sa.JSON(), nullable=True),
            sa.Column("duration_seconds", sa.Float(), nullable=True),
            sa.Column("node_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True, index=True),
        )

    if "eval_pairs" not in existing_tables:
        op.create_table(
            "eval_pairs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("eval_generation_id", sa.String(50), nullable=True, index=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("expected_answer", sa.Text(), nullable=False),
            sa.Column(
                "source_doc_id",
                sa.Integer(),
                sa.ForeignKey("documents.id"),
                nullable=True,
            ),
            sa.Column("source_chunk_hash", sa.String(64), nullable=True),
            sa.Column("corpus_type", sa.String(20), nullable=True),
            sa.Column("quality_score", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if "research_configs" not in existing_tables:
        op.create_table(
            "research_configs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("composite_score", sa.Float(), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=True,
                index=True,
                server_default="false",
            ),
            sa.Column("promoted_at", sa.DateTime(), nullable=True),
            sa.Column("source", sa.String(30), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )


def downgrade():
    op.drop_table("research_configs")
    op.drop_table("eval_pairs")
    op.drop_table("experiment_runs")
