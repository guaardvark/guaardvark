"""cluster foundation — hardware_profile + online columns; drop capabilities"""
from alembic import op
import sqlalchemy as sa

revision = "003_cluster_foundation"
down_revision = "v2_5_2_full"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "interconnector_nodes",
        sa.Column("hardware_profile", sa.Text(), nullable=True, server_default="{}"),
    )
    op.add_column(
        "interconnector_nodes",
        sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.drop_column("interconnector_nodes", "capabilities")


def downgrade():
    op.add_column("interconnector_nodes", sa.Column("capabilities", sa.Text(), nullable=True))
    op.drop_column("interconnector_nodes", "online")
    op.drop_column("interconnector_nodes", "hardware_profile")
