"""add production tables

Revision ID: 4c59d33e3b3a
Revises: 008_session_mode
Create Date: 2026-05-07 16:32:53.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4c59d33e3b3a'
down_revision = '008_session_mode'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('subjects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('ref_image_paths', sa.JSON(), nullable=False),
        sa.Column('lora_path', sa.String(length=512), nullable=True),
        sa.Column('lora_version', sa.Integer(), nullable=False),
        sa.Column('training_status', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_subjects_kind'), 'subjects', ['kind'], unique=False)
    op.create_index(op.f('ix_subjects_training_status'), 'subjects', ['training_status'], unique=False)

    op.create_table('productions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('script_text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=64), nullable=False),
        sa.Column('current_stage', sa.String(length=64), nullable=False),
        sa.Column('settings_json', sa.JSON(), nullable=False),
        sa.Column('error_blob', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_productions_project_id'), 'productions', ['project_id'], unique=False)
    op.create_index(op.f('ix_productions_status'), 'productions', ['status'], unique=False)

    op.create_table('production_shots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('production_id', sa.Integer(), nullable=False),
        sa.Column('scene_number', sa.Integer(), nullable=False),
        sa.Column('shot_number', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('camera_angle', sa.String(length=128), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=False),
        sa.Column('dialogue_text', sa.Text(), nullable=True),
        sa.Column('voice_subject_id', sa.Integer(), nullable=True),
        sa.Column('storyboard_image_path', sa.String(length=512), nullable=True),
        sa.Column('video_clip_path', sa.String(length=512), nullable=True),
        sa.Column('approved', sa.Boolean(), nullable=False),
        sa.Column('regen_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['production_id'], ['productions.id'], ),
        sa.ForeignKeyConstraint(['voice_subject_id'], ['subjects.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_production_shots_production_id'), 'production_shots', ['production_id'], unique=False)

    op.create_table('production_shot_subjects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shot_id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['shot_id'], ['production_shots.id'], ),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_production_shot_subjects_shot_id'), 'production_shot_subjects', ['shot_id'], unique=False)
    op.create_index(op.f('ix_production_shot_subjects_subject_id'), 'production_shot_subjects', ['subject_id'], unique=False)

    op.create_table('swarm_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('production_id', sa.Integer(), nullable=False),
        sa.Column('agent_name', sa.String(length=64), nullable=False),
        sa.Column('input_json', sa.JSON(), nullable=False),
        sa.Column('output_json', sa.JSON(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('model', sa.String(length=128), nullable=True),
        sa.Column('tokens_in', sa.Integer(), nullable=True),
        sa.Column('tokens_out', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('error_text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['production_id'], ['productions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_swarm_messages_production_id'), 'swarm_messages', ['production_id'], unique=False)
    op.create_index(op.f('ix_swarm_messages_agent_name'), 'swarm_messages', ['agent_name'], unique=False)
    op.create_index(op.f('ix_swarm_messages_created_at'), 'swarm_messages', ['created_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_swarm_messages_created_at'), table_name='swarm_messages')
    op.drop_index(op.f('ix_swarm_messages_agent_name'), table_name='swarm_messages')
    op.drop_index(op.f('ix_swarm_messages_production_id'), table_name='swarm_messages')
    op.drop_table('swarm_messages')

    op.drop_index(op.f('ix_production_shot_subjects_subject_id'), table_name='production_shot_subjects')
    op.drop_index(op.f('ix_production_shot_subjects_shot_id'), table_name='production_shot_subjects')
    op.drop_table('production_shot_subjects')

    op.drop_index(op.f('ix_production_shots_production_id'), table_name='production_shots')
    op.drop_table('production_shots')

    op.drop_index(op.f('ix_productions_status'), table_name='productions')
    op.drop_index(op.f('ix_productions_project_id'), table_name='productions')
    op.drop_table('productions')

    op.drop_index(op.f('ix_subjects_training_status'), table_name='subjects')
    op.drop_index(op.f('ix_subjects_kind'), table_name='subjects')
    op.drop_table('subjects')
