"""reports 테이블 CRUD - upsert 중심."""
import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Report
from parser.base import ParsedReport

log = structlog.get_logger(__name__)


async def upsert_report(session: AsyncSession, parsed: ParsedReport) -> tuple[Report, str]:
    """
    리포트를 upsert.
    Returns: (report, action) where action is 'inserted' | 'updated' | 'skipped'
    """
    values = {
        "broker": parsed.broker or "Unknown",
        "report_date": parsed.report_date,
        "analyst": parsed.analyst,
        "stock_name": parsed.stock_name,
        "title": parsed.title,
        "title_normalized": parsed.title_normalized,
        "stock_code": parsed.stock_code,
        "sector": parsed.sector,
        "report_type": parsed.report_type,
        "opinion": parsed.opinion,
        "target_price": parsed.target_price,
        "prev_opinion": parsed.prev_opinion,
        "prev_target_price": parsed.prev_target_price,
        "earnings_quarter": parsed.earnings_quarter,
        "est_revenue": parsed.est_revenue,
        "est_op_profit": parsed.est_op_profit,
        "est_eps": parsed.est_eps,
        "earnings_surprise": parsed.earnings_surprise,
        "pdf_url": parsed.pdf_url,
        "source_channel": parsed.source_channel,
        "source_message_id": parsed.source_message_id,
        "raw_text": parsed.raw_text,
    }

    stmt = insert(Report).values(**values)

    # 충돌 시: pdf_url 등 추가 정보가 있으면 업데이트
    update_set = {
        "pdf_url": stmt.excluded.pdf_url,
        "opinion": stmt.excluded.opinion,
        "target_price": stmt.excluded.target_price,
        "raw_text": stmt.excluded.raw_text,
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uix_report_dedup",
        set_=update_set,
        where=(
            (stmt.excluded.pdf_url.isnot(None)) |
            (stmt.excluded.opinion.isnot(None))
        ),
    ).returning(Report)

    result = await session.execute(stmt)
    await session.commit()
    row = result.scalar_one_or_none()

    if row is None:
        # DO NOTHING 케이스 - 기존 레코드 조회
        existing = await session.scalar(
            select(Report).where(Report.title_normalized == parsed.title_normalized)
        )
        log.info("report_skipped", title=parsed.title[:50])
        return existing, "skipped"

    action = "inserted" if row.created_at == row.updated_at else "updated"
    log.info("report_upserted", action=action, title=parsed.title[:50], broker=parsed.broker)
    return row, action


async def get_reports_needing_pdf(session: AsyncSession, limit: int = 100) -> list[Report]:
    """PDF URL은 있지만 아직 다운로드 안 된 리포트."""
    result = await session.execute(
        select(Report)
        .where(Report.pdf_url.isnot(None))
        .where(Report.pdf_path.is_(None))
        .where(Report.pdf_download_failed.is_(False))
        .limit(limit)
    )
    return result.scalars().all()


async def mark_pdf_failed(session: AsyncSession, report_id: int) -> None:
    await session.execute(
        update(Report).where(Report.id == report_id).values(pdf_download_failed=True)
    )
    await session.commit()


async def update_pdf_info(
    session: AsyncSession,
    report_id: int,
    pdf_path: str,
    pdf_size_kb: int | None,
    page_count: int | None,
) -> None:
    await session.execute(
        update(Report)
        .where(Report.id == report_id)
        .values(pdf_path=pdf_path, pdf_size_kb=pdf_size_kb, page_count=page_count)
    )
    await session.commit()
