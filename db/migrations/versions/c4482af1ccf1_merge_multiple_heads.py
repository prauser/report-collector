"""merge_multiple_heads

Revision ID: c4482af1ccf1
Revises: a2b4c6d8e0f1, b3f1e2d9a847
Create Date: 2026-03-26 15:20:31.420978

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4482af1ccf1'
down_revision: Union[str, None] = ('a2b4c6d8e0f1', 'b3f1e2d9a847')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
