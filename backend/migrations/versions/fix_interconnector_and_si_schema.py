"""Fix interconnector_nodes missing GPU columns and self_improvement_runs autoincrement.

interconnector_nodes: add model_name, vram_total, vram_free, specialties, current_load;
                     drop stale hardware_profile column.
self_improvement_runs: link existing sequence to id column default.

Revision ID: fix_interconnector_si
Revises: 8b4758422e7b
"""
from alembic import op
import sqlalchemy as sa

revision = 'fix_interconnector_si'
down_revision = '8b4758422e7b'
branch_labels = None
depends_on = None


def upgrade():
    # --- interconnector_nodes: add GPU orchestration columns ---
    op.add_column('interconnector_nodes',
                  sa.Column('model_name', sa.String(100), nullable=True))
    op.add_column('interconnector_nodes',
                  sa.Column('vram_total', sa.Integer(), nullable=True))
    op.add_column('interconnector_nodes',
                  sa.Column('vram_free', sa.Integer(), nullable=True))
    op.add_column('interconnector_nodes',
                  sa.Column('specialties', sa.Text(), server_default='[]', nullable=True))
    op.add_column('interconnector_nodes',
                  sa.Column('current_load', sa.Float(), server_default='0.0', nullable=True))

    # Drop stale column that no longer exists in the model
    op.drop_column('interconnector_nodes', 'hardware_profile')

    # --- self_improvement_runs: fix id autoincrement ---
    # Sequence exists but was never linked to the column default
    op.execute(
        "ALTER TABLE self_improvement_runs "
        "ALTER COLUMN id SET DEFAULT nextval('self_improvement_runs_id_seq'::regclass)"
    )
    op.execute(
        "ALTER SEQUENCE self_improvement_runs_id_seq OWNED BY self_improvement_runs.id"
    )


def downgrade():
    # Reverse self_improvement_runs fix
    op.execute("ALTER TABLE self_improvement_runs ALTER COLUMN id DROP DEFAULT")
    op.execute("ALTER SEQUENCE self_improvement_runs_id_seq OWNED BY NONE")

    # Re-add hardware_profile, drop new columns
    op.add_column('interconnector_nodes',
                  sa.Column('hardware_profile', sa.Text(), nullable=True))
    op.drop_column('interconnector_nodes', 'current_load')
    op.drop_column('interconnector_nodes', 'specialties')
    op.drop_column('interconnector_nodes', 'vram_free')
    op.drop_column('interconnector_nodes', 'vram_total')
    op.drop_column('interconnector_nodes', 'model_name')
