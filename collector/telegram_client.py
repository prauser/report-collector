"""Telethon 클라이언트 래퍼."""
from telethon import TelegramClient
from telethon.sessions import StringSession

from config.settings import settings

_client: TelegramClient | None = None


def get_client() -> TelegramClient:
    global _client
    if _client is None:
        _client = TelegramClient(
            settings.telegram_session_name,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
    return _client
