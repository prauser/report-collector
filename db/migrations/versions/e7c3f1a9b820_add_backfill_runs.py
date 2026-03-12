"""add_backfill_runs

Revision ID: e7c3f1a9b820
Revises: d4b7e9f2a183
Create Date: 2026-03-12 01:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e7c3f1a9b820'
down_revision: Union[str, None] = 'd4b7e9f2a183'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'backfill_runs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('channel_username', sa.String(100), nullable=False),
        sa.Column('run_date', sa.Date(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('from_message_id', sa.BigInteger(), nullable=True),
        sa.Column('to_message_id', sa.BigInteger(), nullable=True),
        sa.Column('n_scanned', sa.Integer(), server_default='0', nullable=False),
        sa.Column('n_saved', sa.Integer(), server_default='0', nullable=False),
        sa.Column('n_pending', sa.Integer(), server_default='0', nullable=False),
        sa.Column('n_skipped', sa.Integer(), server_default='0', nullable=False),
        sa.Column('status', sa.String(20), server_default='running', nullable=False),
        sa.Column('error_msg', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_backfill_runs_channel', 'backfill_runs', ['channel_username', 'run_date'])


def downgrade() -> None:
    op.drop_index('ix_backfill_runs_channel', table_name='backfill_runs')
    op.drop_table('backfill_runs')
