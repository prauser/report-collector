"""Run backfill - PDF download + fail_reason only, no heavy processing."""
import os
import sys

# Windows cp949 인코딩 에러 방지
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio
import warnings
import structlog

warnings.filterwarnings('ignore', category=DeprecationWarning)
structlog.configure(
    processors=[structlog.dev.ConsoleRenderer()],
    wrapper_class=structlog.BoundLogger,
)

from config.settings import settings
# Phase 1: 메시지 수집 + PDF 다운로드만 (분석은 별도 실행)
settings.analysis_enabled = False
settings.gemini_api_key = None

from collector.backfill import backfill_channel
from collector.telegram_client import get_client
from db.session import AsyncSessionLocal
from db.models import Channel
from sqlalchemy import select

LIMIT = 200

async def retry_failed_downloads():
    """기존 리포트 중 pdf_url 있지만 다운로드 안 된 건 재시도."""
    from db.models import Report
    from storage.pdf_archiver import download_pdf
    from storage.report_repo import update_pdf_info, mark_pdf_failed

    async with AsyncSessionLocal() as session:
        rows = (await session.scalars(
            select(Report).where(
                Report.pdf_url.isnot(None),
                Report.pdf_path.is_(None),
                Report.pdf_download_failed == False,
            )
        )).all()

    if not rows:
        print("No retry-able reports found.")
        return

    print(f"\n>>> Retrying {len(rows)} failed PDF downloads...")
    ok = fail = 0
    for report in rows:
        rel_path, size_kb, fail_reason = await download_pdf(report)
        async with AsyncSessionLocal() as session:
            if rel_path:
                await update_pdf_info(session, report.id, rel_path, size_kb, None)
                await session.commit()
                ok += 1
            else:
                await mark_pdf_failed(session, report.id, fail_reason or "unknown")
                await session.commit()
                fail += 1
    print(f"<<< Retry done: {ok} ok, {fail} failed")


async def main():
    client = get_client()
    await client.start()

    # Phase 0: 기존 실패 리포트 재다운로드
    await retry_failed_downloads()

    async with AsyncSessionLocal() as session:
        rows = (await session.scalars(
            select(Channel).where(Channel.is_active == True)
        )).all()
        channels = [r.channel_username for r in rows]

    print(f"\nActive channels: {channels}")
    print(f"Limit per channel: {LIMIT}")
    print(f"Layer2: {'ON' if settings.analysis_enabled else 'OFF'} | Gemini: {'ON' if settings.gemini_api_key else 'OFF'}")
    print("=" * 60)

    for ch in channels:
        print(f"\n>>> Backfilling {ch} (limit={LIMIT})...")
        try:
            saved = await backfill_channel(ch, limit=LIMIT)
            print(f"<<< {ch}: {saved} saved")
        except Exception as e:
            import traceback
            print(f"<<< {ch}: ERROR - {e}")
            traceback.print_exc()

    await client.disconnect()
    print("\n=== All done ===")

asyncio.get_event_loop().run_until_complete(main())
