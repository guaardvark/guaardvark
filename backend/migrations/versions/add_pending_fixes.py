"""Add pending_fixes table for self-improvement review queue.

Revision ID: add_pending_fixes
Revises: fix_autoincrement
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_pending_fixes'
down_revision = 'fix_autoincrement'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pending_fixes',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('file_path', sa.String(1024), nullable=False),
        sa.Column('original_content', sa.Text(), nullable=True),
        sa.Column('proposed_new_content', sa.Text(), nullable=True),
        sa.Column('proposed_diff', sa.Text(), nullable=False),
        sa.Column('fix_description', sa.Text(), nullable=True),
        sa.Column('severity', sa.String(20), server_default='medium'),
        sa.Column('status', sa.String(20), server_default='proposed'),
        sa.Column('reviewed_by', sa.String(50), nullable=True),
        sa.Column('review_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['run_id'], ['self_improvement_runs.id'],
                                name='fk_pendingfix_run_id', ondelete='CASCADE'),
    )
    op.execute("ALTER TABLE pending_fixes ALTER COLUMN id SET DEFAULT nextval('pending_fixes_id_seq'::regclass)")


def downgrade():
    op.drop_table('pending_fixes')
