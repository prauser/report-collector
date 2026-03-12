"""add_pending_messages

Revision ID: d4b7e9f2a183
Revises: c3a5e2d7f014
Create Date: 2026-03-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd4b7e9f2a183'
down_revision: Union[str, None] = 'c3a5e2d7f014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pending_messages',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('source_channel', sa.String(length=100), nullable=False),
        sa.Column('source_message_id', sa.BigInteger(), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('pdf_url', sa.Text(), nullable=True),
        sa.Column('s2a_label', sa.String(length=20), nullable=True),   # ambiguous / etc
        sa.Column('s2a_reason', sa.Text(), nullable=True),              # LLM 이유
        sa.Column('review_status', sa.String(20), server_default='pending', nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_pending_messages_channel', 'pending_messages', ['source_channel'])
    op.create_index('ix_pending_messages_status', 'pending_messages', ['review_status'])
    op.create_index('ix_pending_messages_created', 'pending_messages', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_pending_messages_created', table_name='pending_messages')
    op.drop_index('ix_pending_messages_status', table_name='pending_messages')
    op.drop_index('ix_pending_messages_channel', table_name='pending_messages')
    op.drop_table('pending_messages')
