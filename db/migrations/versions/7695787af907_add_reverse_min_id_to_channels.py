"""add reverse_min_id to channels

Revision ID: 7695787af907
Revises: c4482af1ccf1
Create Date: 2026-03-31 05:43:06.429204

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7695787af907'
down_revision: Union[str, None] = 'c4482af1ccf1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('channels', sa.Column('reverse_min_id', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('channels', 'reverse_min_id')
