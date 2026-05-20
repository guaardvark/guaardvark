"""merge agent-provenance + film-crew migration heads

Two independent migration chains (008_session_mode→009_agent_action_provenance
and 5e8a1b2c3d4e→f63d6ff26f29) became parallel heads when their branches were
merged into main. This empty merge revision rejoins them so there is a single
head to stamp. No schema operations.
"""
from alembic import op
import sqlalchemy as sa

revision = '02d047e9bd68'
down_revision = ('009_agent_action_provenance', 'f63d6ff26f29')
branch_labels = None
depends_on = None

def upgrade():
    pass

def downgrade():
    pass
