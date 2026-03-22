"""add_pipeline_status

Revision ID: a1b2c3d4e5f6
Revises: f6e228957724
Create Date: 2026-03-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f6e228957724'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. pipeline_status 컬럼 추가 (default 'new')
    op.add_column('reports', sa.Column('pipeline_status', sa.String(length=30), nullable=False, server_default='new'))
    # 2. index 생성
    op.create_index('ix_reports_pipeline_status', 'reports', ['pipeline_status'], unique=False)
    # 3. pdf_fail_retryable 컬럼 추가 (nullable)
    op.add_column('reports', sa.Column('pdf_fail_retryable', sa.Boolean(), nullable=True))

    # 4. pipeline_status backfill
    op.execute("""
        UPDATE reports
        SET pipeline_status = 'done'
        WHERE id IN (SELECT report_id FROM report_analysis)
    """)
    op.execute("""
        UPDATE reports
        SET pipeline_status = 'pdf_failed'
        WHERE pdf_download_failed = true
          AND id NOT IN (SELECT report_id FROM report_analysis)
    """)
    op.execute("""
        UPDATE reports
        SET pipeline_status = 'pdf_done'
        WHERE pdf_path IS NOT NULL
          AND pdf_download_failed = false
          AND id NOT IN (SELECT report_id FROM report_analysis)
    """)
    # 나머지는 이미 server_default 'new' 로 처리됨

    # 5. pdf_fail_retryable backfill
    # 확정 비재시도: http_404, http_410, not_pdf:*, no_url, unsupported_host:*
    op.execute("""
        UPDATE reports
        SET pdf_fail_retryable = false
        WHERE pdf_fail_reason IN ('http_404', 'http_410', 'no_url')
           OR pdf_fail_reason LIKE 'not_pdf%'
           OR pdf_fail_reason LIKE 'unsupported_host:%'
    """)
    # 재시도 가능: fail_reason이 있으나 위에 해당하지 않는 경우
    op.execute("""
        UPDATE reports
        SET pdf_fail_retryable = true
        WHERE pdf_fail_reason IS NOT NULL
          AND pdf_fail_retryable IS NULL
    """)


def downgrade() -> None:
    op.drop_index('ix_reports_pipeline_status', table_name='reports')
    op.drop_column('reports', 'pipeline_status')
    op.drop_column('reports', 'pdf_fail_retryable')
