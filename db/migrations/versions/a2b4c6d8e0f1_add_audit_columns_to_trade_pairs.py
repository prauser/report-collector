"""add_audit_columns_to_trade_pairs

Revision ID: a2b4c6d8e0f1
Revises: a8f3c1d2e945
Create Date: 2026-03-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b4c6d8e0f1'
down_revision: Union[str, None] = 'a8f3c1d2e945'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # matched_qty: server_default='0' allows adding NOT NULL to existing rows
    op.add_column('trade_pairs',
        sa.Column('matched_qty', sa.Integer(), nullable=False, server_default='0')
    )
    # Remove the server_default after backfill so new inserts must supply the value
    op.alter_column('trade_pairs', 'matched_qty', server_default=None)

    op.add_column('trade_pairs',
        sa.Column('buy_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0')
    )
    op.alter_column('trade_pairs', 'buy_amount', server_default=None)

    op.add_column('trade_pairs',
        sa.Column('sell_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0')
    )
    op.alter_column('trade_pairs', 'sell_amount', server_default=None)

    op.add_column('trade_pairs',
        sa.Column('buy_fee', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0')
    )
    op.add_column('trade_pairs',
        sa.Column('sell_fee', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0')
    )


def downgrade() -> None:
    op.drop_column('trade_pairs', 'sell_fee')
    op.drop_column('trade_pairs', 'buy_fee')
    op.drop_column('trade_pairs', 'sell_amount')
    op.drop_column('trade_pairs', 'buy_amount')
    op.drop_column('trade_pairs', 'matched_qty')
