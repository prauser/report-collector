"""Run backfill - PDF download + fail_reason only, no heavy processing."""
import os
import sys

# Windows cp949 인코딩 에러 방지
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
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
from sqlalchemy import select, update as sa_update

DEFAULT_LIMIT = 2000


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


async def retry_pdf_failed(retryable_only: bool = True, channel: str | None = None, limit: int = DEFAULT_LIMIT):
    """pipeline_status='pdf_failed' 건을 재다운로드 시도."""
    from db.models import Report
    from storage.pdf_archiver import download_pdf
    from storage.report_repo import update_pdf_info, mark_pdf_failed, update_pipeline_status

    async with AsyncSessionLocal() as session:
        query = select(Report).where(Report.pipeline_status == "pdf_failed")
        if retryable_only:
            query = query.where(Report.pdf_fail_retryable == True)
        if channel:
            query = query.where(Report.source_channel == channel)
        query = query.limit(limit)
        rows = (await session.scalars(query)).all()

    if not rows:
        print("No retryable PDF failures found.")
        return

    print(f"Retrying {len(rows)} PDF downloads...")
    ok = fail = 0
    for report in rows:
        # 실패 상태 초기화 후 재시도
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_update(Report).where(Report.id == report.id).values(
                    pdf_download_failed=False,
                    pdf_fail_reason=None,
                    pdf_fail_retryable=None,
                )
            )
            await session.commit()

        rel_path, size_kb, fail_reason = await download_pdf(report)
        async with AsyncSessionLocal() as session:
            if rel_path:
                await update_pdf_info(session, report.id, rel_path, size_kb, None)
                await update_pipeline_status(session, report.id, "pdf_done")
                await session.commit()
                ok += 1
            else:
                await mark_pdf_failed(session, report.id, fail_reason or "unknown")
                await session.commit()
                fail += 1
    print(f"Retry done: {ok} ok, {fail} failed")


async def retry_s2a_failed(channel: str | None = None, limit: int = DEFAULT_LIMIT):
    """pipeline_status='s2a_failed' 건을 재분류 시도."""
    from db.models import Report

    async with AsyncSessionLocal() as session:
        query = select(Report).where(Report.pipeline_status == "s2a_failed")
        if channel:
            query = query.where(Report.source_channel == channel)
        query = query.limit(limit)
        rows = (await session.scalars(query)).all()

    if not rows:
        print("No s2a_failed reports found.")
        return

    print(f"Found {len(rows)} s2a_failed reports.")
    print("Note: S2a retry requires Telegram client context. Use backfill_channel() for full re-run.")
    print("These reports already exist in DB — manual re-classification needed via run_analysis.py.")


async def retry_analysis_failed(channel: str | None = None, limit: int = DEFAULT_LIMIT):
    """pipeline_status='analysis_failed' 건 조회 안내."""
    from db.models import Report

    async with AsyncSessionLocal() as session:
        query = select(Report).where(Report.pipeline_status == "analysis_failed")
        if channel:
            query = query.where(Report.source_channel == channel)
        query = query.limit(limit)
        rows = (await session.scalars(query)).all()

    if not rows:
        print("No analysis_failed reports found.")
        return

    print(f"Found {len(rows)} analysis_failed reports.")
    print("Use `python run_analysis.py` to re-run analysis on these reports.")
    print("run_analysis.py queries pdf_path-present + no report_analysis records.")


async def run_retry_stage(args: argparse.Namespace):
    """--retry-stage 모드 실행."""
    stage = args.retry_stage
    channel = getattr(args, "channel", None)
    limit = args.limit

    print(f"=== Retry Stage: {stage} ===")
    if channel:
        print(f"Channel filter: {channel}")
    print(f"Limit: {limit}")
    print()

    if stage == "pdf_failed":
        retryable_only = not args.all_failures
        print(f"Retryable only: {retryable_only}")
        await retry_pdf_failed(retryable_only=retryable_only, channel=channel, limit=limit)
    elif stage == "s2a_failed":
        await retry_s2a_failed(channel=channel, limit=limit)
    elif stage == "analysis_failed":
        await retry_analysis_failed(channel=channel, limit=limit)
    else:
        print(f"Unknown stage: {stage}")


async def main(args: argparse.Namespace):
    if args.retry_stage:
        await run_retry_stage(args)
        return

    client = get_client()
    await client.start()

    # Phase 0: 기존 실패 리포트 재다운로드
    await retry_failed_downloads()

    channel_filter = getattr(args, "channel", None)

    if channel_filter:
        channels = [channel_filter]
    else:
        async with AsyncSessionLocal() as session:
            rows = (await session.scalars(
                select(Channel).where(Channel.is_active == True)
            )).all()
            channels = [r.channel_username for r in rows]

    limit = args.limit

    print(f"\nActive channels: {channels}")
    print(f"Limit per channel: {limit}")
    direction = "backward (최신→과거)" if getattr(args, 'reverse', False) else "forward (과거→최신)"
    print(f"Layer2: {'ON' if settings.analysis_enabled else 'OFF'} | Gemini: {'ON' if settings.gemini_api_key else 'OFF'} | Direction: {direction}")
    print("=" * 60)

    for ch in channels:
        print(f"\n>>> Backfilling {ch} (limit={limit})...")
        try:
            saved = await backfill_channel(ch, limit=limit, reverse=getattr(args, 'reverse', False))
            print(f"<<< {ch}: {saved} saved")
        except Exception as e:
            import traceback
            print(f"<<< {ch}: ERROR - {e}")
            traceback.print_exc()

    await client.disconnect()
    print("\n=== All done ===")


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="히스토리 백필 - PDF 다운로드 (분석 제외)"
    )
    parser.add_argument(
        "--retry-stage",
        choices=["s2a_failed", "pdf_failed", "analysis_failed"],
        default=None,
        help="해당 pipeline_status 건만 재처리",
    )
    parser.add_argument(
        "--all-failures",
        action="store_true",
        default=False,
        help="pdf_failed와 함께 사용: pdf_fail_retryable=False 건 포함 (기본값: retryable만 대상)",
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="특정 채널만 대상 (기본값: 전체 활성 채널)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"처리 건수 제한 (기본값: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        default=False,
        help="최신 메시지부터 역순 스캔 (기본: 오래된 것부터)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    asyncio.run(main(args))
