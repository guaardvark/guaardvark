"""add entity link columns to folders table

Revision ID: 0f596bba4373
Revises: 85f0f092f5db
Create Date: 2026-03-10 22:53:35
"""
from alembic import op
import sqlalchemy as sa

revision = '0f596bba4373'
down_revision = '85f0f092f5db'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('folders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('client_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('project_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('website_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('tags', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))
        batch_op.create_foreign_key('fk_folder_client_id', 'clients', ['client_id'], ['id'])
        batch_op.create_foreign_key('fk_folder_project_id', 'projects', ['project_id'], ['id'])
        batch_op.create_foreign_key('fk_folder_website_id', 'wordpress_sites', ['website_id'], ['id'])


def downgrade():
    with op.batch_alter_table('folders', schema=None) as batch_op:
        batch_op.drop_constraint('fk_folder_website_id', type_='foreignkey')
        batch_op.drop_constraint('fk_folder_project_id', type_='foreignkey')
        batch_op.drop_constraint('fk_folder_client_id', type_='foreignkey')
        batch_op.drop_column('notes')
        batch_op.drop_column('tags')
        batch_op.drop_column('website_id')
        batch_op.drop_column('project_id')
        batch_op.drop_column('client_id')
