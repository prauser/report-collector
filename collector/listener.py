"""실시간 메시지 수신 - Telethon event handler.

파이프라인:
  S2a(분류) → DB저장 → PDF다운 → Markdown변환 → Layer2 추출(Sonnet) → DB(분석 저장)
"""
import asyncio
import time
import structlog
from sqlalchemy import select
from telethon import events

from collector.telegram_client import get_client
from config.settings import settings
from db.models import Channel
from db.session import AsyncSessionLocal
from parser.registry import parse_messages
from parser.llm_parser import classify_message
from parser.quality import assess_parse_quality
from parser.normalizer import normalize_broker, normalize_opinion, parse_price
from storage import stock_mapper
from storage.pdf_archiver import (
    attempt_pdf_download,
    pdf_filename as _pdf_filename_shared,
    download_telegram_document,
    resolve_tme_links,
    download_pdf,
)
from storage.pending_repo import save_pending
from storage.report_repo import update_pipeline_status, upsert_report, update_pdf_info

log = structlog.get_logger(__name__)

_CHANNEL_CACHE: set[str] = set()
_CHANNEL_CACHE_TTL = 60  # 초
_channel_cache_at: float = 0.0

_TELEGRAM_SEM_LIMIT = 5
_telegram_sem = asyncio.Semaphore(_TELEGRAM_SEM_LIMIT)


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
    """Document 타입 PDF 메시지에서 파일명 추출. PDF가 아니면 None.

    Delegates to storage.pdf_archiver.pdf_filename (shared implementation).
    Kept here for backward compatibility — callers may import from collector.listener.
    """
    return _pdf_filename_shared(message)


def _apply_layer2_meta(report, meta: dict, session_needed: bool = False) -> dict:
    """
    Layer2 메타데이터로 report 필드 업데이트 값 dict 반환.
    실제 UPDATE는 호출자가 수행.
    """
    from parser.meta_updater import apply_layer2_meta
    return apply_layer2_meta(report, meta)


async def handle_new_message(event: events.NewMessage.Event) -> None:
    message = event.message
    channel = f"@{event.chat.username}" if event.chat.username else str(event.chat_id)

    active = await _get_active_channels()
    if channel not in active:
        return

    text = message.text or ""
    pdf_fname = _pdf_filename(message)  # 항상 체크
    if not text.strip():
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

        if s2a.message_type == "ambiguous" and not parsed.pdf_url and not pdf_fname:
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

            # S2a 완료 후 상태 기록
            await update_pipeline_status(session, report.id, "s2a_done")

            # 2) PDF 다운로드 (3-stage fallback: Telegram doc → t.me resolve → HTTP)
            if report.pdf_path:
                # PDF가 이미 있는 경우 → pdf_done 상태만 업데이트
                await update_pipeline_status(session, report.id, "pdf_done")
            else:
                # Stage 1 대상: pdf_fname이 있을 때만 원본 메시지에서 직접 다운로드
                tg_message = message if pdf_fname else None
                tme_links = parsed.tme_message_links if parsed.tme_message_links else None

                # _telegram_sem gates Telegram MTProto calls inside attempt_pdf_download
                # (download_telegram_document and resolve_tme_links are gated by it)
                success, rel_path, _size_kb, _fail_reason, _retryable = await attempt_pdf_download(
                    client=client,
                    report=report,
                    message=tg_message,
                    tme_links=tme_links,
                    session=session,
                    telegram_sem=_telegram_sem,
                )
                if success:
                    report.pdf_path = rel_path

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
