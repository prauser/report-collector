"""add_llm_usage_table

Revision ID: b2e4f1c8d903
Revises: 425dd3c249af
Create Date: 2026-03-09 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2e4f1c8d903'
down_revision: Union[str, None] = '425dd3c249af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_usage',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('called_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('model', sa.String(length=60), nullable=False),
        sa.Column('purpose', sa.String(length=40), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column('report_id', sa.BigInteger(), nullable=True),
        sa.Column('source_channel', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llm_usage_called_at', 'llm_usage', ['called_at'])
    op.create_index('ix_llm_usage_purpose_date', 'llm_usage', ['purpose', 'called_at'])
    op.create_index('ix_llm_usage_model_date', 'llm_usage', ['model', 'called_at'])


def downgrade() -> None:
    op.drop_index('ix_llm_usage_model_date', table_name='llm_usage')
    op.drop_index('ix_llm_usage_purpose_date', table_name='llm_usage')
    op.drop_index('ix_llm_usage_called_at', table_name='llm_usage')
    op.drop_table('llm_usage')
