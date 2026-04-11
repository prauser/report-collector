"""sector_mention unique key: (report_id, sector) -> (report_id, sector, mention_type)

Revision ID: g1a2b3c4d5e6
Revises: f6e228957724
Create Date: 2026-04-07
"""
from alembic import op

revision = "g1a2b3c4d5e6"
down_revision = "7695787af907"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_report_sector", "report_sector_mentions", type_="unique")
    op.create_index(
        "uq_report_sector_type",
        "report_sector_mentions",
        ["report_id", "sector", "mention_type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_report_sector_type", table_name="report_sector_mentions")
    op.create_unique_constraint(
        "uq_report_sector",
        "report_sector_mentions",
        ["report_id", "sector"],
    )
