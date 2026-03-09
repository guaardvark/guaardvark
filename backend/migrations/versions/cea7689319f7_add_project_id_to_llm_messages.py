from alembic import op
import sqlalchemy as sa

revision = 'cea7689319f7'
down_revision = 'v2_4_1_pg'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('llm_messages', sa.Column('project_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_llm_messages_project_id'), 'llm_messages', ['project_id'], unique=False)
    op.create_foreign_key(
        'fk_llmmessage_project_id', 'llm_messages', 'projects',
        ['project_id'], ['id'], ondelete='SET NULL'
    )

def downgrade():
    op.drop_constraint('fk_llmmessage_project_id', 'llm_messages', type_='foreignkey')
    op.drop_index(op.f('ix_llm_messages_project_id'), table_name='llm_messages')
    op.drop_column('llm_messages', 'project_id')
