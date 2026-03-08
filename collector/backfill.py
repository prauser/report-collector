"""히스토리 백필 스크립트 - 채널의 과거 메시지를 소급 수집."""
import asyncio
import structlog
from telethon.tl.types import Message

from collector.telegram_client import get_client
from config.settings import settings
from db.session import AsyncSessionLocal
from db.models import Channel
from parser.registry import parse_message
from storage.report_repo import upsert_report
from sqlalchemy import select

log = structlog.get_logger(__name__)


async def backfill_channel(channel_username: str, limit: int | None = None) -> int:
    """
    채널의 히스토리를 백필.
    channels 테이블의 last_message_id 이후 메시지만 수집.
    Returns: 저장된 레코드 수
    """
    client = get_client()
    saved = 0

    async with AsyncSessionLocal() as session:
        channel_row = await session.scalar(
            select(Channel).where(Channel.channel_username == channel_username)
        )
        min_id = channel_row.last_message_id if channel_row else 0

    log.info("backfill_start", channel=channel_username, min_id=min_id)

    effective_limit = limit or settings.backfill_limit or None

    async for message in client.iter_messages(
        channel_username,
        limit=effective_limit,
        min_id=min_id or 0,
        reverse=True,  # 오래된 것부터
    ):
        if not isinstance(message, Message) or not message.text:
            continue

        parsed = parse_message(message.text, channel_username, message_id=message.id)
        if parsed is None:
            continue

        async with AsyncSessionLocal() as session:
            _, action = await upsert_report(session, parsed)
            if action == "inserted":
                saved += 1

    # last_message_id 업데이트
    async with AsyncSessionLocal() as session:
        if channel_row:
            channel_row.last_message_id = message.id if message else channel_row.last_message_id
            await session.commit()

    log.info("backfill_done", channel=channel_username, saved=saved)
    return saved


async def backfill_all() -> None:
    client = get_client()
    await client.start()

    for channel in settings.telegram_channels:
        try:
            await backfill_channel(channel)
        except Exception as e:
            log.error("backfill_error", channel=channel, error=str(e))

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(backfill_all())
