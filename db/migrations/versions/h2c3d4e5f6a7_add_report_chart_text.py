"""add_report_chart_text

Revision ID: h2c3d4e5f6a7
Revises: g1a2b3c4d5e6
Create Date: 2026-04-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'h2c3d4e5f6a7'
down_revision: Union[str, None] = 'g1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'report_chart_text',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('chart_texts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('image_count', sa.Integer(), nullable=False),
        sa.Column('success_count', sa.Integer(), nullable=False),
        sa.Column('model', sa.String(length=60), nullable=True),
        sa.Column('total_input_tokens', sa.Integer(), nullable=True),
        sa.Column('total_output_tokens', sa.Integer(), nullable=True),
        sa.Column('total_cost_usd', sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('report_id'),
    )


def downgrade() -> None:
    op.drop_table('report_chart_text')
