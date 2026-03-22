"""add_price_cache

Revision ID: a8f3c1d2e945
Revises: f6e228957724
Create Date: 2026-03-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a8f3c1d2e945'
down_revision: Union[str, None] = 'f6e228957724'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'price_cache',
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('open', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('high', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('low', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('close', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('volume', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('symbol', 'date'),
    )
    op.create_index('ix_price_cache_symbol', 'price_cache', ['symbol'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_price_cache_symbol', table_name='price_cache')
    op.drop_table('price_cache')
