"""add demonstration and demo_step tables

Revision ID: beebd6a4ac5d
Revises: fix_interconnector_si
Create Date: 2026-03-29
"""
from alembic import op
import sqlalchemy as sa

revision = 'beebd6a4ac5d'
down_revision = 'fix_interconnector_si'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'demonstrations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(255), nullable=True, index=True),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('context_url', sa.String(1024), nullable=True),
        sa.Column('context_app', sa.String(255), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('autonomy_level', sa.String(20), nullable=False, server_default='guided', index=True),
        sa.Column('success_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('parent_demonstration_id', sa.Integer(),
                  sa.ForeignKey('demonstrations.id', name='fk_demo_parent_id', ondelete='SET NULL'),
                  nullable=True, index=True),
        sa.Column('is_complete', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "autonomy_level IN ('guided', 'supervised', 'autonomous')",
            name='ck_demo_autonomy_level',
        ),
    )

    op.create_table(
        'demo_steps',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('demonstration_id', sa.Integer(),
                  sa.ForeignKey('demonstrations.id', name='fk_demostep_demo_id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('step_index', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.String(20), nullable=False),
        sa.Column('target_description', sa.Text(), nullable=False),
        sa.Column('element_context', sa.Text(), nullable=True),
        sa.Column('coordinates_x', sa.Integer(), nullable=True),
        sa.Column('coordinates_y', sa.Integer(), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('keys', sa.String(255), nullable=True),
        sa.Column('intent', sa.Text(), nullable=True),
        sa.Column('precondition', sa.Text(), nullable=True),
        sa.Column('variability', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('wait_condition', sa.Text(), nullable=True),
        sa.Column('is_mistake', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('screenshot_before', sa.String(1024), nullable=True),
        sa.Column('screenshot_after', sa.String(1024), nullable=True),
        sa.CheckConstraint(
            "action_type IN ('click', 'type', 'hotkey', 'scroll')",
            name='ck_demostep_action_type',
        ),
        sa.UniqueConstraint('demonstration_id', 'step_index', name='uq_demo_step_index'),
    )


def downgrade():
    op.drop_table('demo_steps')
    op.drop_table('demonstrations')
