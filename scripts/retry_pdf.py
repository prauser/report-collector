"""pdf_download_failed=True인 레코드 재시도."""
import asyncio
import structlog

from db.session import AsyncSessionLocal
from storage.report_repo import get_reports_needing_pdf
from storage.pdf_archiver import download_and_archive

log = structlog.get_logger(__name__)


async def retry_failed_pdfs(limit: int = 50) -> None:
    async with AsyncSessionLocal() as session:
        reports = await get_reports_needing_pdf(session, limit)

    log.info("retry_pdf_start", count=len(reports))
    success = 0

    for report in reports:
        async with AsyncSessionLocal() as session:
            if await download_and_archive(report, session):
                success += 1

    log.info("retry_pdf_done", success=success, total=len(reports))


if __name__ == "__main__":
    asyncio.run(retry_failed_pdfs())
