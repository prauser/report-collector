"""raw_text 재파싱 스크립트 - 파싱 실패한 레코드를 재처리."""
import asyncio
import structlog
from sqlalchemy import select, update

from db.models import Report
from db.session import AsyncSessionLocal
from parser.registry import parse_message

log = structlog.get_logger(__name__)


async def reparse_failed(limit: int = 500) -> None:
    """broker가 'Unknown'이거나 title_normalized가 없는 레코드 재파싱."""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Report)
            .where(
                (Report.broker == "Unknown") | (Report.title_normalized.is_(None))
            )
            .limit(limit)
        )).scalars().all()

    log.info("reparse_target", count=len(rows))
    updated = 0

    for row in rows:
        if not row.raw_text:
            continue
        parsed = parse_message(row.raw_text, row.source_channel, row.source_message_id)
        if parsed is None:
            continue

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Report).where(Report.id == row.id).values(
                    broker=parsed.broker or row.broker,
                    stock_name=parsed.stock_name or row.stock_name,
                    stock_code=parsed.stock_code or row.stock_code,
                    title_normalized=parsed.title_normalized or row.title_normalized,
                    opinion=parsed.opinion or row.opinion,
                    target_price=parsed.target_price or row.target_price,
                    pdf_url=parsed.pdf_url or row.pdf_url,
                )
            )
            await session.commit()
        updated += 1

    log.info("reparse_done", updated=updated)


if __name__ == "__main__":
    asyncio.run(reparse_failed())
