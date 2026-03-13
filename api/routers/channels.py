"""채널 관리 엔드포인트."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Channel
from db.session import get_session
from config.settings import settings

router = APIRouter(prefix="/channels", tags=["channels"])


class ChannelOut(BaseModel):
    id: int
    username: str
    display_name: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class AddChannelRequest(BaseModel):
    username: str
    display_name: str | None = None


def _to_out(ch: Channel) -> ChannelOut:
    return ChannelOut(
        id=ch.id,
        username=ch.channel_username,
        display_name=ch.display_name,
        is_active=ch.is_active,
    )


@router.get("", response_model=list[ChannelOut])
async def list_channels(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Channel).order_by(Channel.id))).all()

    # 테이블이 비어 있으면 settings 기본값으로 시드
    if not rows:
        for username in settings.telegram_channels:
            session.add(Channel(channel_username=username, is_active=True))
        await session.commit()
        rows = (await session.scalars(select(Channel).order_by(Channel.id))).all()

    return [_to_out(ch) for ch in rows]


def _normalize_username(raw: str) -> str:
    """t.me/foo, https://t.me/foo, @foo, foo → @foo"""
    s = raw.strip().rstrip("/")
    # URL 형태 처리
    if "t.me/" in s:
        s = s.split("t.me/")[-1].split("/")[0].split("?")[0]
    # @ 제거 후 재부착
    s = s.lstrip("@")
    return f"@{s}"


@router.post("", response_model=ChannelOut, status_code=201)
async def add_channel(req: AddChannelRequest, session: AsyncSession = Depends(get_session)):
    username = _normalize_username(req.username)
    ch = Channel(channel_username=username, display_name=req.display_name, is_active=True)
    session.add(ch)
    await session.commit()
    await session.refresh(ch)
    return _to_out(ch)


@router.patch("/{channel_id}/toggle", response_model=ChannelOut)
async def toggle_channel(channel_id: int, session: AsyncSession = Depends(get_session)):
    ch = await session.get(Channel, channel_id)
    if not ch:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Channel not found")
    ch.is_active = not ch.is_active
    await session.commit()
    await session.refresh(ch)
    return _to_out(ch)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(channel_id: int, session: AsyncSession = Depends(get_session)):
    ch = await session.get(Channel, channel_id)
    if ch:
        await session.delete(ch)
        await session.commit()
