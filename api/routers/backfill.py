"""백필 실행 엔드포인트."""
import asyncio
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from db.models import Channel, Report
from db.session import get_session

router = APIRouter(prefix="/backfill", tags=["backfill"])

# 실행 중인 채널 트래킹 (in-memory, single process용)
_running: set[str] = set()


class RunRequest(BaseModel):
    channel: str          # 채널명 또는 "all"
    limit: int | None = None  # None = settings.backfill_limit 사용, 0 = 전체


class RunResponse(BaseModel):
    started: list[str]
    already_running: list[str]


async def _run_backfill(channel: str, limit: int | None = None) -> None:
    """백그라운드에서 백필 실행."""
    try:
        from collector.backfill import backfill_channel
        from collector.telegram_client import get_client

        client = get_client()
        if not client.is_connected():
            await client.start()
        await backfill_channel(channel, limit=limit)
    except Exception:
        pass  # 에러는 BackfillRun 레코드에 기록됨
    finally:
        _running.discard(channel)


async def _active_channels(session: AsyncSession) -> list[str]:
    rows = (await session.scalars(
        select(Channel).where(Channel.is_active == True)
    )).all()
    return [r.channel_username for r in rows] or settings.telegram_channels


@router.get("/channels")
async def list_channels(session: AsyncSession = Depends(get_session)):
    """DB의 활성 채널 목록 반환."""
    return {"channels": await _active_channels(session)}


@router.get("/running")
async def get_running():
    """현재 실행 중인 채널 목록."""
    return {"running": list(_running)}


@router.post("/run", response_model=RunResponse)
async def run_backfill(req: RunRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    """백필 실행 트리거. channel="all" 이면 전체 활성 채널."""
    if req.channel == "all":
        channels = await _active_channels(session)
    else:
        channels = [req.channel]

    started: list[str] = []
    already_running: list[str] = []

    for ch in channels:
        if ch in _running:
            already_running.append(ch)
        else:
            _running.add(ch)
            background_tasks.add_task(_run_backfill, ch, req.limit)
            started.append(ch)

    return RunResponse(started=started, already_running=already_running)


class PdfRetryResponse(BaseModel):
    reset_count: int


@router.post("/retry-pdf", response_model=PdfRetryResponse)
async def retry_failed_pdfs(
    background_tasks: BackgroundTasks,
    channel: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """pdf_download_failed=True인 리포트를 재시도 대상으로 리셋."""
    stmt = (
        update(Report)
        .where(Report.pdf_download_failed == True)
        .where(Report.pdf_url.isnot(None))
        .values(pdf_download_failed=False)
    )
    if channel:
        stmt = stmt.where(Report.source_channel == channel)

    result = await session.execute(stmt)
    await session.commit()
    reset_count = result.rowcount

    # 리셋된 건이 있으면 백그라운드에서 다운로드 시도
    if reset_count > 0:
        background_tasks.add_task(_retry_pdf_downloads)

    return PdfRetryResponse(reset_count=reset_count)


async def _retry_pdf_downloads() -> None:
    """리셋된 PDF를 순차 재다운로드."""
    from db.session import AsyncSessionLocal
    from storage.pdf_archiver import download_and_archive
    from storage.report_repo import get_reports_needing_pdf

    async with AsyncSessionLocal() as session:
        reports = await get_reports_needing_pdf(session, limit=200)
        for report in reports:
            await download_and_archive(report, session)
