"""production subject join

Revision ID: 5e8a1b2c3d4e
Revises: 4d70a1b2cad3
Create Date: 2026-05-07 19:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5e8a1b2c3d4e'
down_revision = '4d70a1b2cad3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('production_subjects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('production_id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['production_id'], ['productions.id'], name='fk_production_subject_production_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], name='fk_production_subject_subject_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('production_id', 'subject_id', name='uq_production_subject')
    )
    op.create_index(op.f('ix_production_subjects_production_id'), 'production_subjects', ['production_id'], unique=False)
    op.create_index(op.f('ix_production_subjects_subject_id'), 'production_subjects', ['subject_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_production_subjects_subject_id'), table_name='production_subjects')
    op.drop_index(op.f('ix_production_subjects_production_id'), table_name='production_subjects')
    op.drop_table('production_subjects')
