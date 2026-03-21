"""실시간 메시지 수신 - Telethon event handler.

파이프라인:
  S2a(분류) → DB저장 → PDF다운 → Markdown변환 → Layer2 추출(Sonnet) → DB(분석 저장)
"""
import time
import structlog
from sqlalchemy import select
from telethon import events
from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

from collector.telegram_client import get_client
from config.settings import settings
from db.models import Channel
from db.session import AsyncSessionLocal
from parser.registry import parse_messages
from parser.llm_parser import classify_message
from parser.quality import assess_parse_quality
from parser.markdown_converter import convert_pdf_to_markdown
from parser.image_extractor import extract_images_from_pdf
from parser.chart_digitizer import digitize_charts
from parser.key_data_extractor import extract_key_data
from parser.layer2_extractor import extract_layer2
from parser.normalizer import normalize_broker, normalize_opinion, parse_price
from storage import stock_mapper
from storage.pdf_archiver import download_pdf, download_telegram_document, resolve_tme_links
from storage.pending_repo import save_pending
from storage.report_repo import mark_pdf_failed, update_pdf_info, upsert_report
from storage.analysis_repo import save_markdown, save_analysis, log_analysis_failure

log = structlog.get_logger(__name__)

_CHANNEL_CACHE: set[str] = set()
_CHANNEL_CACHE_TTL = 60  # 초
_channel_cache_at: float = 0.0


async def _get_active_channels() -> set[str]:
    global _CHANNEL_CACHE, _channel_cache_at
    if time.monotonic() - _channel_cache_at < _CHANNEL_CACHE_TTL:
        return _CHANNEL_CACHE
    async with AsyncSessionLocal() as s:
        rows = (await s.scalars(select(Channel.channel_username).where(Channel.is_active == True))).all()
    _CHANNEL_CACHE = set(rows) if rows else set(settings.telegram_channels)
    _channel_cache_at = time.monotonic()
    return _CHANNEL_CACHE


def _pdf_filename(message) -> str | None:
    """Document 타입 PDF 메시지에서 파일명 추출. PDF가 아니면 None."""
    if not isinstance(message.media, MessageMediaDocument):
        return None
    doc = message.media.document
    if "pdf" not in getattr(doc, "mime_type", ""):
        return None
    for attr in doc.attributes or []:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None


def _apply_layer2_meta(report, meta: dict, session_needed: bool = False) -> dict:
    """
    Layer2 메타데이터로 report 필드 업데이트 값 dict 반환.
    실제 UPDATE는 호출자가 수행.
    """
    if not meta:
        return {}

    updates = {}
    _t = lambda v, n: v[:n] if isinstance(v, str) and len(v) > n else v

    def _pick(key, normalizer=None, maxlen=None):
        val = meta.get(key)
        if val:
            val = normalizer(val) if normalizer else val
            return _t(val, maxlen) if maxlen and isinstance(val, str) else val
        return None

    if v := _pick("broker", normalize_broker, 50):
        updates["broker"] = v
    if v := _pick("stock_name", maxlen=100):
        updates["stock_name"] = v
    if v := _pick("stock_code"):
        updates["stock_code"] = v
    if v := _pick("analyst", maxlen=100):
        updates["analyst"] = v
    if v := _pick("opinion", normalize_opinion, 20):
        updates["opinion"] = v
    if v := _pick("sector", maxlen=100):
        updates["sector"] = v
    if v := _pick("report_type", maxlen=50):
        updates["report_type"] = v
    if v := _pick("prev_opinion", normalize_opinion, 20):
        updates["prev_opinion"] = v

    tp = meta.get("target_price")
    if isinstance(tp, int) and tp > 0:
        updates["target_price"] = tp
    elif isinstance(tp, str):
        parsed_tp = parse_price(tp)
        if parsed_tp:
            updates["target_price"] = parsed_tp

    ptp = meta.get("prev_target_price")
    if isinstance(ptp, int) and ptp > 0:
        updates["prev_target_price"] = ptp
    elif isinstance(ptp, str):
        parsed_ptp = parse_price(ptp)
        if parsed_ptp:
            updates["prev_target_price"] = parsed_ptp

    return updates


async def handle_new_message(event: events.NewMessage.Event) -> None:
    message = event.message
    channel = f"@{event.chat.username}" if event.chat.username else str(event.chat_id)

    active = await _get_active_channels()
    if channel not in active:
        return

    text = message.text or ""
    pdf_fname = None
    if not text.strip():
        pdf_fname = _pdf_filename(message)
        if not pdf_fname:
            return
        text = pdf_fname

    parsed_list = parse_messages(text, channel, message_id=message.id)
    if not parsed_list:
        log.debug("parse_skipped", channel=channel, message_id=message.id)
        return

    client = get_client()

    for parsed in parsed_list:
        if parsed.report_date is None:
            parsed.report_date = message.date.date()

        # S2a: 분류
        s2a = await classify_message(parsed)

        if s2a.message_type in ("news", "general"):
            log.debug("s2a_filtered", type=s2a.message_type, channel=channel)
            continue

        if s2a.message_type == "ambiguous" and not parsed.pdf_url:
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
            continue

        # broker_report 확정 → 종목 매핑
        if parsed.stock_name and not parsed.stock_code:
            parsed.stock_code = await stock_mapper.get_code(parsed.stock_name)

        parsed.parse_quality = assess_parse_quality(parsed)

        async with AsyncSessionLocal() as session:
            report, action = await upsert_report(session, parsed)
            if not report:
                continue

            # 2) PDF 다운로드
            if pdf_fname and not report.pdf_path:
                rel_path, size_kb = await download_telegram_document(client, message, report)
                if rel_path:
                    await update_pdf_info(session, report.id, rel_path, size_kb, None)
                    report.pdf_path = rel_path

            # t.me 메시지 링크에서 PDF URL/document resolve
            if not report.pdf_url and not report.pdf_path and parsed.tme_message_links:
                tme_url, tme_msg = await resolve_tme_links(client, parsed.tme_message_links)
                if tme_url:
                    report.pdf_url = tme_url
                    from sqlalchemy import update as sa_update
                    from db.models import Report as ReportModel
                    await session.execute(
                        sa_update(ReportModel).where(ReportModel.id == report.id)
                        .values(pdf_url=tme_url)
                    )
                elif tme_msg:
                    rel_path, size_kb = await download_telegram_document(client, tme_msg, report)
                    if rel_path:
                        await update_pdf_info(session, report.id, rel_path, size_kb, None)
                        report.pdf_path = rel_path

            if report.pdf_url and not report.pdf_path:
                rel_path, size_kb, fail_reason = await download_pdf(report)
                if rel_path:
                    await update_pdf_info(session, report.id, rel_path, size_kb, None)
                    report.pdf_path = rel_path
                else:
                    await mark_pdf_failed(session, report.id, fail_reason or "unknown")

            # 분석(키데이터/마크다운/이미지/Gemini/Layer2)은 run_analysis.py에서 배치 처리
            await session.commit()
            log.info("report_collected", report_id=report.id, has_pdf=bool(report.pdf_path))


async def start_listener() -> None:
    client = get_client()
    await client.start()

    channels = await _get_active_channels()
    log.info("listener_starting", channels=list(channels))

    client.add_event_handler(handle_new_message, events.NewMessage())

    log.info("listener_running")
    await client.run_until_disconnected()
