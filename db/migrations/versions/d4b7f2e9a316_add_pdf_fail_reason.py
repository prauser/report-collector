"""add_pdf_fail_reason

Revision ID: d4b7f2e9a316
Revises: 1f0105e742c5
Create Date: 2026-03-16 23:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd4b7f2e9a316'
down_revision: Union[str, None] = '1f0105e742c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reports', sa.Column('pdf_fail_reason', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('reports', 'pdf_fail_reason')
