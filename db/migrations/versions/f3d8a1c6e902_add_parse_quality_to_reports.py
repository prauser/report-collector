"""add_parse_quality_to_reports

Revision ID: f3d8a1c6e902
Revises: e7c3f1a9b820
Create Date: 2026-03-13 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f3d8a1c6e902'
down_revision: Union[str, None] = 'e7c3f1a9b820'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reports', sa.Column('parse_quality', sa.String(10), nullable=True))
    op.create_index('ix_reports_parse_quality', 'reports', ['parse_quality'])


def downgrade() -> None:
    op.drop_index('ix_reports_parse_quality', table_name='reports')
    op.drop_column('reports', 'parse_quality')
