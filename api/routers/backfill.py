"""백필 실행 엔드포인트."""
import asyncio
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from config.settings import settings

router = APIRouter(prefix="/backfill", tags=["backfill"])

# 실행 중인 채널 트래킹 (in-memory, single process용)
_running: set[str] = set()


class RunRequest(BaseModel):
    channel: str  # 채널명 또는 "all"


class RunResponse(BaseModel):
    started: list[str]
    already_running: list[str]


async def _run_backfill(channel: str) -> None:
    """백그라운드에서 백필 실행."""
    try:
        from collector.backfill import backfill_channel
        from collector.telegram_client import get_client

        client = get_client()
        if not client.is_connected():
            await client.start()
        await backfill_channel(channel)
    except Exception:
        pass  # 에러는 BackfillRun 레코드에 기록됨
    finally:
        _running.discard(channel)


@router.get("/channels")
async def list_channels():
    """설정된 채널 목록 반환."""
    return {"channels": settings.telegram_channels}


@router.get("/running")
async def get_running():
    """현재 실행 중인 채널 목록."""
    return {"running": list(_running)}


@router.post("/run", response_model=RunResponse)
async def run_backfill(req: RunRequest, background_tasks: BackgroundTasks):
    """백필 실행 트리거. channel="all" 이면 전체 채널."""
    if req.channel == "all":
        channels = settings.telegram_channels
    else:
        channels = [req.channel]

    started: list[str] = []
    already_running: list[str] = []

    for ch in channels:
        if ch in _running:
            already_running.append(ch)
        else:
            _running.add(ch)
            background_tasks.add_task(_run_backfill, ch)
            started.append(ch)

    return RunResponse(started=started, already_running=already_running)
