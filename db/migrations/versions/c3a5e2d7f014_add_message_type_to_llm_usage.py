"""add_message_type_to_llm_usage

Revision ID: c3a5e2d7f014
Revises: b2e4f1c8d903
Create Date: 2026-03-09 15:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c3a5e2d7f014'
down_revision: Union[str, None] = 'b2e4f1c8d903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('llm_usage', sa.Column('message_type', sa.String(length=20), nullable=True))
    op.create_index('ix_llm_usage_message_type', 'llm_usage', ['message_type'])


def downgrade() -> None:
    op.drop_index('ix_llm_usage_message_type', table_name='llm_usage')
    op.drop_column('llm_usage', 'message_type')
