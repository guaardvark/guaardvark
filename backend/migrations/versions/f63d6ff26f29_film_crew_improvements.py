from alembic import op
import sqlalchemy as sa

revision = 'f63d6ff26f29'
down_revision = '5e8a1b2c3d4e'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('subjects', sa.Column('voice_id', sa.String(length=128), nullable=True))
    op.add_column('production_shots', sa.Column('scene_mood', sa.String(length=64), nullable=True))
    op.add_column('production_shots', sa.Column('character_name', sa.String(length=255), nullable=True))

def downgrade():
    op.drop_column('production_shots', 'character_name')
    op.drop_column('production_shots', 'scene_mood')
    op.drop_column('subjects', 'voice_id')
