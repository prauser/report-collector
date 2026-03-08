"""백필 동작 확인 스크립트 - 소량만 테스트."""
import asyncio
from collector.telegram_client import get_client
from collector.backfill import backfill_channel


async def main():
    client = get_client()
    await client.start()
    saved = await backfill_channel("@repostory123", limit=20)
    print(f"저장된 리포트: {saved}건")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
