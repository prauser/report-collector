"""실시간 메시지 수신 - Telethon event handler."""
import structlog
from telethon import events

from collector.telegram_client import get_client
from config.settings import settings
from db.session import AsyncSessionLocal
from parser.registry import parse_message
from storage.pdf_archiver import download_pdf
from storage.report_repo import mark_pdf_failed, update_pdf_info, upsert_report

log = structlog.get_logger(__name__)


async def handle_new_message(event: events.NewMessage.Event) -> None:
    message = event.message
    channel = f"@{event.chat.username}" if event.chat.username else str(event.chat_id)

    text = message.text or ""
    if not text.strip():
        return

    parsed = parse_message(text, channel, message_id=message.id)
    if parsed is None:
        log.debug("parse_skipped", channel=channel, message_id=message.id)
        return

    async with AsyncSessionLocal() as session:
        report, action = await upsert_report(session, parsed)

        if report and report.pdf_url and not report.pdf_path:
            rel_path, size_kb = await download_pdf(report)
            if rel_path:
                await update_pdf_info(session, report.id, rel_path, size_kb, None)
            else:
                await mark_pdf_failed(session, report.id)


async def start_listener() -> None:
    client = get_client()
    await client.start()

    channels = settings.telegram_channels
    log.info("listener_starting", channels=channels)

    client.add_event_handler(
        handle_new_message,
        events.NewMessage(chats=channels),
    )

    log.info("listener_running")
    await client.run_until_disconnected()
