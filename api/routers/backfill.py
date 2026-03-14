"""백필 실행 엔드포인트."""
import asyncio
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from db.models import Channel
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
