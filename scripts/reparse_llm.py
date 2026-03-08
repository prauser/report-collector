"""기존 레코드의 raw_text를 LLM으로 재파싱하여 메타데이터 교정."""
import argparse
import asyncio
import structlog
from sqlalchemy import select, update

from db.models import Report
from db.session import AsyncSessionLocal
from parser.registry import parse_message
from parser.llm_parser import enrich_with_llm

log = structlog.get_logger(__name__)


async def reparse_with_llm(
    limit: int = 500,
    batch_size: int = 50,
    dry_run: bool = False,
) -> None:
    """모든 레코드를 LLM으로 재파싱."""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Report)
            .order_by(Report.id)
            .limit(limit)
        )).scalars().all()

    total = len(rows)
    log.info("reparse_llm_start", total=total, dry_run=dry_run)
    updated = 0
    filtered = 0

    for i, row in enumerate(rows):
        if not row.raw_text:
            continue

        # Stage 1: 정규식 파싱
        parsed = parse_message(row.raw_text, row.source_channel, row.source_message_id)
        if parsed is None:
            continue

        # Stage 2: LLM 보강
        enriched = await enrich_with_llm(parsed)

        if enriched is None:
            filtered += 1
            if not dry_run:
                log.debug("reparse_llm_filtered", report_id=row.id, title=row.title[:50])
            continue

        if dry_run:
            changes = {}
            if enriched.broker != row.broker:
                changes["broker"] = f"{row.broker} → {enriched.broker}"
            if enriched.stock_name != row.stock_name:
                changes["stock_name"] = f"{row.stock_name} → {enriched.stock_name}"
            if enriched.title != row.title:
                changes["title"] = f"{row.title[:30]} → {enriched.title[:30]}"
            if enriched.opinion != row.opinion:
                changes["opinion"] = f"{row.opinion} → {enriched.opinion}"
            if enriched.target_price != row.target_price:
                changes["target_price"] = f"{row.target_price} → {enriched.target_price}"
            if changes:
                log.info("reparse_llm_diff", report_id=row.id, **changes)
            continue

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Report).where(Report.id == row.id).values(
                    broker=enriched.broker or row.broker,
                    stock_name=enriched.stock_name or row.stock_name,
                    stock_code=enriched.stock_code or row.stock_code,
                    title=enriched.title or row.title,
                    analyst=enriched.analyst or row.analyst,
                    opinion=enriched.opinion or row.opinion,
                    target_price=enriched.target_price or row.target_price,
                    prev_opinion=enriched.prev_opinion or row.prev_opinion,
                    prev_target_price=enriched.prev_target_price or row.prev_target_price,
                    sector=enriched.sector or row.sector,
                    report_type=enriched.report_type or row.report_type,
                )
            )
            await session.commit()
        updated += 1

        if (i + 1) % batch_size == 0:
            log.info("reparse_llm_progress", processed=i + 1, total=total, updated=updated)

    log.info("reparse_llm_done", updated=updated, filtered=filtered, total=total)


def main():
    parser = argparse.ArgumentParser(description="LLM 기반 재파싱")
    parser.add_argument("--limit", type=int, default=500, help="처리할 레코드 수")
    parser.add_argument("--batch-size", type=int, default=50, help="배치 크기")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 diff만 출력")
    args = parser.parse_args()

    asyncio.run(reparse_with_llm(
        limit=args.limit,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
