"""Fix llm_messages.id autoincrement — link existing sequence to column default.

Revision ID: fix_autoincrement
Revises: b11b87bcf960
"""
from alembic import op

revision = 'fix_autoincrement'
down_revision = 'b11b87bcf960'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE llm_messages ALTER COLUMN id SET DEFAULT nextval('llm_messages_id_seq'::regclass)")
    op.execute("ALTER SEQUENCE llm_messages_id_seq OWNED BY llm_messages.id")


def downgrade():
    op.execute("ALTER TABLE llm_messages ALTER COLUMN id DROP DEFAULT")
    op.execute("ALTER SEQUENCE llm_messages_id_seq OWNED BY NONE")
