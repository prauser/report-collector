"""실시간 메시지 수신 - Telethon event handler."""
import structlog
from telethon import events

from collector.telegram_client import get_client
from config.settings import settings
from db.session import AsyncSessionLocal
from parser.registry import parse_message
from storage import stock_mapper
from parser.llm_parser import classify_message, extract_metadata
from parser.pdf_analyzer import analyze_pdf
from storage.pdf_archiver import download_pdf
from storage.pending_repo import save_pending
from storage.report_repo import mark_pdf_failed, update_ai_fields, update_pdf_info, upsert_report

log = structlog.get_logger(__name__)


def _build_pdf_meta_context(pdf_url: str | None) -> str | None:
    """다운로드된 PDF의 메타데이터를 S2b 컨텍스트 문자열로 변환."""
    if not pdf_url:
        return None
    try:
        import io, requests
        from urllib.parse import urlparse, parse_qs
        from pypdf import PdfReader

        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(pdf_url, timeout=20, headers=headers, allow_redirects=True)

        # Google Docs viewer 언래핑
        if "docs.google.com/viewer" in r.url:
            qs = parse_qs(urlparse(r.url).query)
            real_url = qs.get("url", [None])[0]
            if real_url:
                r = requests.get(real_url, timeout=20, headers=headers)

        ct = r.headers.get("Content-Type", "")
        if "pdf" not in ct and "octet-stream" not in ct:
            return None

        reader = PdfReader(io.BytesIO(r.content))
        meta = reader.metadata
        if not meta:
            return None

        def _decode(v):
            if v is None:
                return None
            if hasattr(v, "original_bytes"):
                raw = v.original_bytes
                if raw.startswith(b"\xfe\xff"):
                    try:
                        return raw[2:].decode("utf-16-be")
                    except Exception:
                        pass
                for enc in ("euc-kr", "cp949", "utf-8"):
                    try:
                        return raw.decode(enc)
                    except Exception:
                        pass
            return str(v)

        parts = []
        if kw := _decode(meta.get("/Keywords")):
            parts.append(f"Keywords: {kw}")
        if au := _decode(meta.get("/Author")):
            parts.append(f"Author: {au}")
        if ti := _decode(meta.get("/Title")):
            parts.append(f"Title: {ti}")
        parts.append(f"Pages: {len(reader.pages)}")

        return "\n".join(parts) if parts else None

    except Exception as e:
        log.debug("pdf_meta_context_failed", error=str(e))
        return None


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

    if parsed.report_date is None:
        parsed.report_date = message.date.date()

    # S2a: 분류
    s2a = await classify_message(parsed)

    if s2a.message_type in ("news", "general"):
        log.debug("s2a_filtered", type=s2a.message_type, channel=channel)
        return

    if s2a.message_type == "ambiguous":
        async with AsyncSessionLocal() as session:
            await save_pending(
                session,
                source_channel=channel,
                source_message_id=message.id,
                raw_text=parsed.raw_text,
                pdf_url=parsed.pdf_url,
                s2a_label="ambiguous",
                s2a_reason=s2a.reason,
            )
            await session.commit()
        log.info("s2a_ambiguous_saved", channel=channel, message_id=message.id)
        return

    # broker_report → S2b
    if parsed.stock_name and not parsed.stock_code:
        parsed.stock_code = await stock_mapper.get_code(parsed.stock_name)

    # PDF 메타데이터를 S2b 컨텍스트로 제공 (있으면)
    pdf_meta_ctx = _build_pdf_meta_context(parsed.pdf_url)

    parsed = await extract_metadata(parsed, pdf_meta_context=pdf_meta_ctx)

    async with AsyncSessionLocal() as session:
        report, action = await upsert_report(session, parsed)

        if report and report.pdf_url and not report.pdf_path:
            rel_path, size_kb = await download_pdf(report)
            if rel_path:
                await update_pdf_info(session, report.id, rel_path, size_kb, None)
                report.pdf_path = rel_path
                analysis = await analyze_pdf(report)
                if analysis:
                    await update_ai_fields(
                        session, report.id,
                        analysis["summary"],
                        analysis["sentiment"],
                        analysis["keywords"],
                    )
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
