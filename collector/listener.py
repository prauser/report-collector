"""실시간 메시지 수신 - Telethon event handler.

파이프라인:
  S2a(분류) → DB저장 → PDF다운 → Markdown변환 → Layer2 추출(Sonnet) → DB(분석 저장)
"""
import structlog
from telethon import events
from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

from collector.telegram_client import get_client
from config.settings import settings
from db.session import AsyncSessionLocal
from parser.registry import parse_messages
from parser.llm_parser import classify_message
from parser.quality import assess_parse_quality
from parser.markdown_converter import convert_pdf_to_markdown
from parser.layer2_extractor import extract_layer2
from parser.normalizer import normalize_broker, normalize_opinion, parse_price
from storage import stock_mapper
from storage.pdf_archiver import download_pdf, download_telegram_document
from storage.pending_repo import save_pending
from storage.report_repo import mark_pdf_failed, update_pdf_info, upsert_report
from storage.analysis_repo import save_markdown, save_analysis, log_analysis_failure

log = structlog.get_logger(__name__)


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

    def _pick(key, normalizer=None):
        val = meta.get(key)
        if val:
            return normalizer(val) if normalizer else val
        return None

    if v := _pick("broker", normalize_broker):
        updates["broker"] = v
    if v := _pick("stock_name"):
        updates["stock_name"] = v
    if v := _pick("stock_code"):
        updates["stock_code"] = v
    if v := _pick("analyst"):
        updates["analyst"] = v
    if v := _pick("opinion", normalize_opinion):
        updates["opinion"] = v
    if v := _pick("sector"):
        updates["sector"] = v
    if v := _pick("report_type"):
        updates["report_type"] = v
    if v := _pick("prev_opinion", normalize_opinion):
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

            if report.pdf_url and not report.pdf_path:
                rel_path, size_kb = await download_pdf(report)
                if rel_path:
                    await update_pdf_info(session, report.id, rel_path, size_kb, None)
                    report.pdf_path = rel_path
                else:
                    await mark_pdf_failed(session, report.id)

            # 3) Markdown 변환
            markdown_text = None
            converter_name = ""
            if report.pdf_path:
                abs_path = settings.pdf_base_path / report.pdf_path
                if abs_path.exists():
                    markdown_text, converter_name = await convert_pdf_to_markdown(abs_path)
                    if markdown_text:
                        await save_markdown(session, report.id, markdown_text, converter_name)

            # 4) Layer 2 추출 (Sonnet — 메타데이터 + 분석 통합)
            layer2 = await extract_layer2(
                text=parsed.raw_text,
                markdown=markdown_text,
                channel=channel,
                report_id=report.id,
            )

            if layer2:
                # Layer2 메타로 report 필드 보강
                from sqlalchemy import update as sa_update
                from db.models import Report as ReportModel
                meta_updates = _apply_layer2_meta(report, layer2.meta)
                if meta_updates:
                    await session.execute(
                        sa_update(ReportModel)
                        .where(ReportModel.id == report.id)
                        .values(**meta_updates)
                    )

                # 분석 결과 저장
                try:
                    await save_analysis(session, report.id, layer2)
                    await session.commit()
                except Exception as e:
                    log.warning("analysis_save_failed", report_id=report.id, error=str(e))
                    await session.rollback()
                    async with AsyncSessionLocal() as err_session:
                        await log_analysis_failure(err_session, report.id, "extract_layer2", str(e))
                        await err_session.commit()
            else:
                await session.commit()


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
