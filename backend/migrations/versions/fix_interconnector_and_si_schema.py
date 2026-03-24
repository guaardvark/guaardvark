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


def _column_exists(conn, table, column):
    """Check if a column exists in a table via information_schema."""
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :table AND column_name = :column"
    ), {"table": table, "column": column})
    return result.fetchone() is not None


def upgrade():
    conn = op.get_bind()

    # --- interconnector_nodes: add GPU orchestration columns ---
    if not _column_exists(conn, 'interconnector_nodes', 'model_name'):
        op.add_column('interconnector_nodes',
                      sa.Column('model_name', sa.String(100), nullable=True))
    if not _column_exists(conn, 'interconnector_nodes', 'vram_total'):
        op.add_column('interconnector_nodes',
                      sa.Column('vram_total', sa.Integer(), nullable=True))
    if not _column_exists(conn, 'interconnector_nodes', 'vram_free'):
        op.add_column('interconnector_nodes',
                      sa.Column('vram_free', sa.Integer(), nullable=True))
    if not _column_exists(conn, 'interconnector_nodes', 'specialties'):
        op.add_column('interconnector_nodes',
                      sa.Column('specialties', sa.Text(), server_default='[]', nullable=True))
    if not _column_exists(conn, 'interconnector_nodes', 'current_load'):
        op.add_column('interconnector_nodes',
                      sa.Column('current_load', sa.Float(), server_default='0.0', nullable=True))

    # Drop stale column only if it actually exists
    if _column_exists(conn, 'interconnector_nodes', 'hardware_profile'):
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
