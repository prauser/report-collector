"""Telegram 최초 인증 스크립트. 터미널에서 직접 실행."""
import asyncio
from telethon import TelegramClient
from config.settings import settings


async def auth():
    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.start()
    print("인증 완료. 세션 파일 생성됨.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(auth())
