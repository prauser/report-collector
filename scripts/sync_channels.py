"""config의 채널 목록을 channels 테이블과 동기화."""
import asyncio
import structlog
from sqlalchemy.dialects.postgresql import insert
from db.models import Channel
from db.session import AsyncSessionLocal
from config.settings import settings

log = structlog.get_logger(__name__)


async def sync_channels() -> None:
    async with AsyncSessionLocal() as session:
        for username in settings.telegram_channels:
            stmt = insert(Channel).values(
                channel_username=username,
                is_active=True,
            ).on_conflict_do_nothing(index_elements=["channel_username"])
            await session.execute(stmt)
        await session.commit()
    log.info("channels_synced", count=len(settings.telegram_channels))


if __name__ == "__main__":
    asyncio.run(sync_channels())
