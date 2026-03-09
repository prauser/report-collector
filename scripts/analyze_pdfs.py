"""기존 PDF 리포트 배치 AI 분석 스크립트."""
import argparse
import asyncio
import structlog

from db.session import AsyncSessionLocal
from parser.pdf_analyzer import analyze_pdf
from storage.report_repo import get_reports_needing_ai, update_ai_fields

log = structlog.get_logger(__name__)


async def analyze_batch(limit: int = 100, dry_run: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        reports = await get_reports_needing_ai(session, limit=limit)

    total = len(reports)
    log.info("analyze_pdfs_start", total=total, dry_run=dry_run)
    done = 0

    for report in reports:
        analysis = await analyze_pdf(report)
        if analysis is None:
            log.debug("analyze_skipped", report_id=report.id, title=report.title[:40])
            continue

        if dry_run:
            log.info(
                "analyze_dry_run",
                report_id=report.id,
                title=report.title[:40],
                sentiment=float(analysis["sentiment"]),
                keywords=analysis["keywords"][:5],
                summary=analysis["summary"][:80],
            )
        else:
            async with AsyncSessionLocal() as session:
                await update_ai_fields(
                    session,
                    report.id,
                    analysis["summary"],
                    analysis["sentiment"],
                    analysis["keywords"],
                )
            done += 1

    log.info("analyze_pdfs_done", done=done, total=total)


def main():
    parser = argparse.ArgumentParser(description="PDF 배치 AI 분석")
    parser.add_argument("--limit", type=int, default=100, help="처리할 리포트 수")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 결과만 출력")
    args = parser.parse_args()
    asyncio.run(analyze_batch(limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
