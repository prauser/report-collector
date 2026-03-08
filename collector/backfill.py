"""히스토리 백필 스크립트 - 채널의 과거 메시지를 소급 수집."""
import asyncio
import structlog
from datetime import date
from telethon.errors import FloodWaitError
from telethon.tl.types import Message

from collector.telegram_client import get_client
from config.settings import settings
from db.session import AsyncSessionLocal
from db.models import Channel
from parser.registry import parse_message
from storage import stock_mapper
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
    last_id = 0

    async with AsyncSessionLocal() as session:
        channel_row = await session.scalar(
            select(Channel).where(Channel.channel_username == channel_username)
        )
        min_id = channel_row.last_message_id if channel_row else 0

    log.info("backfill_start", channel=channel_username, min_id=min_id)

    effective_limit = limit or settings.backfill_limit or None

    try:
        async for message in client.iter_messages(
            channel_username,
            limit=effective_limit,
            min_id=min_id or 0,
            reverse=True,
        ):
            if not isinstance(message, Message) or not message.text:
                continue

            parsed = parse_message(message.text, channel_username, message_id=message.id)
            if parsed is None:
                continue

            # message.date (UTC aware datetime) → report_date fallback
            if parsed.report_date is None or parsed.report_date == date.today():
                parsed.report_date = message.date.date()

            # stock_code 보완
            if parsed.stock_name and not parsed.stock_code:
                parsed.stock_code = await stock_mapper.get_code(parsed.stock_name)

            async with AsyncSessionLocal() as session:
                _, action = await upsert_report(session, parsed)
                if action == "inserted":
                    saved += 1

            last_id = message.id

    except FloodWaitError as e:
        log.warning("flood_wait", seconds=e.seconds, channel=channel_username)
        await asyncio.sleep(e.seconds)

    # last_message_id 업데이트
    if last_id:
        async with AsyncSessionLocal() as session:
            channel_row = await session.scalar(
                select(Channel).where(Channel.channel_username == channel_username)
            )
            if channel_row:
                channel_row.last_message_id = last_id
                session.add(channel_row)
            else:
                session.add(Channel(
                    channel_username=channel_username,
                    last_message_id=last_id,
                ))
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
