"""Telethon 클라이언트 래퍼."""
from telethon import TelegramClient
from telethon.sessions import StringSession

from config.settings import settings

_client: TelegramClient | None = None


def get_client() -> TelegramClient:
    global _client
    if _client is None:
        if settings.telegram_session_string:
            session = StringSession(settings.telegram_session_string)
        else:
            session = settings.telegram_session_name
        _client = TelegramClient(
            session,
            settings.telegram_api_id,
            settings.telegram_api_hash,
            connection_retries=3,
            retry_delay=5,
            timeout=30,
            request_retries=3,
        )
    return _client
