"""히스토리 백필 스크립트 - 채널의 과거 메시지를 소급 수집.

파이프라인 (listener와 동일):
  S2a(분류) → DB저장 → PDF다운 → Markdown변환 → Layer2 추출 → DB(분석 저장)
"""
import asyncio
import structlog
from dataclasses import dataclass
from datetime import date, datetime, timezone
from telethon.errors import FloodWaitError
from telethon.tl.types import Message, MessageMediaDocument, DocumentAttributeFilename

from collector.telegram_client import get_client
from config.settings import settings
from db.session import AsyncSessionLocal
from db.models import BackfillRun, Channel
from parser.registry import parse_messages
from storage import stock_mapper
from parser.llm_parser import classify_message
from parser.quality import assess_parse_quality
from parser.markdown_converter import convert_pdf_to_markdown
from parser.layer2_extractor import extract_layer2
from storage.pending_repo import save_pending
from storage.pdf_archiver import download_telegram_document, download_pdf
from storage.report_repo import update_pdf_info, mark_pdf_failed, upsert_report
from storage.analysis_repo import save_markdown, save_analysis, log_analysis_failure
from collector.listener import _apply_layer2_meta
from sqlalchemy import select, update as sa_update, func

log = structlog.get_logger(__name__)

_BACKFILL_CONCURRENCY = 5  # 동시 처리 리포트 수


def _pdf_filename(message: Message) -> str | None:
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


@dataclass
class _ReportTask:
    parsed: object
    message: Message
    pdf_fname: str | None
    channel_username: str
    client: object


@dataclass
class _ReportResult:
    action: str  # 'saved' | 'pending' | 'skipped' | 'error'
    message_id: int


async def _process_single_report(task: _ReportTask, semaphore: asyncio.Semaphore) -> _ReportResult:
    """단일 ParsedReport 전체 파이프라인 처리 (S2a → upsert → PDF → layer2)."""
    parsed = task.parsed
    message = task.message
    channel_username = task.channel_username
    client = task.client

    async with semaphore:
        if parsed.report_date is None or parsed.report_date == date.today():
            parsed.report_date = message.date.date()

        # S2a: 분류
        s2a = await classify_message(parsed)

        if s2a.message_type in ("news", "general"):
            return _ReportResult("skipped", message.id)

        if s2a.message_type == "ambiguous" and not parsed.pdf_url:
            async with AsyncSessionLocal() as session:
                await save_pending(
                    session,
                    source_channel=channel_username,
                    source_message_id=message.id,
                    raw_text=parsed.raw_text,
                    pdf_url=parsed.pdf_url,
                    s2a_label="ambiguous",
                    s2a_reason=s2a.reason,
                )
                await session.commit()
            return _ReportResult("pending", message.id)

        # broker_report 확정 (ambiguous여도 pdf_url 있으면 계속 처리)
        if parsed.stock_name and not parsed.stock_code:
            parsed.stock_code = await stock_mapper.get_code(parsed.stock_name)

        parsed.parse_quality = assess_parse_quality(parsed)

        async with AsyncSessionLocal() as session:
            # 1) 리포트 저장
            report, action = await upsert_report(session, parsed)

            if not report:
                return _ReportResult("skipped", message.id)

            # 2) PDF 다운로드
            if task.pdf_fname and not report.pdf_path:
                rel_path, size_kb = await download_telegram_document(client, message, report)
                if rel_path:
                    await update_pdf_info(session, report.id, rel_path, size_kb, None)
                    report.pdf_path = rel_path
            elif report.pdf_url and not report.pdf_path:
                rel_path, size_kb = await download_pdf(report)
                if rel_path:
                    await update_pdf_info(session, report.id, rel_path, size_kb, None)
                    report.pdf_path = rel_path
                else:
                    await mark_pdf_failed(session, report.id)

            # 이미 분석된 리포트는 Layer2 skip
            from db.models import ReportAnalysis
            already_analyzed = await session.scalar(
                select(ReportAnalysis.id).where(ReportAnalysis.report_id == report.id)
            )
            if already_analyzed:
                await session.commit()
                return _ReportResult(action, message.id)

            # 3) Markdown 변환
            markdown_text = None
            if report.pdf_path:
                abs_path = settings.pdf_base_path / report.pdf_path
                if abs_path.exists():
                    markdown_text, converter_name = await convert_pdf_to_markdown(abs_path)
                    if markdown_text:
                        await save_markdown(session, report.id, markdown_text, converter_name)

            # 4) Layer 2 추출
            layer2 = await extract_layer2(
                text=parsed.raw_text,
                markdown=markdown_text,
                channel=channel_username,
                report_id=report.id,
            )

            if layer2:
                from db.models import Report as ReportModel
                from sqlalchemy.exc import IntegrityError
                meta_updates = _apply_layer2_meta(report, layer2.meta)
                if meta_updates:
                    try:
                        async with session.begin_nested():
                            await session.execute(
                                sa_update(ReportModel)
                                .where(ReportModel.id == report.id)
                                .values(**meta_updates)
                            )
                    except IntegrityError:
                        log.debug("meta_update_skipped_dedup", report_id=report.id)
                try:
                    await save_analysis(session, report.id, layer2)
                except Exception as e:
                    log.warning("analysis_save_failed", report_id=report.id, error=str(e))
                    await session.rollback()
                    async with AsyncSessionLocal() as err_session:
                        await log_analysis_failure(err_session, report.id, "extract_layer2", str(e))
                        await err_session.commit()
                    return _ReportResult("error", message.id)

            await session.commit()

        return _ReportResult(action, message.id)


async def backfill_channel(channel_username: str, limit: int | None = None) -> int:
    """
    채널의 히스토리를 백필.
    channels 테이블의 last_message_id 이후 메시지만 수집.
    Returns: 저장된 레코드 수
    """
    client = get_client()

    async with AsyncSessionLocal() as session:
        channel_row = await session.scalar(
            select(Channel).where(Channel.channel_username == channel_username)
        )
        min_id = channel_row.last_message_id if channel_row else 0

    # 런 기록 생성
    run = BackfillRun(
        channel_username=channel_username,
        run_date=date.today(),
        from_message_id=min_id or None,
        status="running",
    )
    async with AsyncSessionLocal() as session:
        session.add(run)
        await session.commit()
        await session.refresh(run)
    run_id = run.id

    log.info("backfill_start", channel=channel_username, min_id=min_id, run_id=run_id)

    n_scanned = n_saved = n_pending = n_skipped = 0
    effective_limit = limit or settings.backfill_limit or None
    all_message_ids: list[int] = []

    try:
        # Phase 1: 메시지 수집 + 파싱 (빠름)
        tasks: list[_ReportTask] = []
        async for message in client.iter_messages(
            channel_username,
            limit=effective_limit,
            min_id=min_id or 0,
            reverse=True,
        ):
            if not isinstance(message, Message):
                continue

            text = message.text or ""
            pdf_fname = None
            if not text:
                pdf_fname = _pdf_filename(message)
                if not pdf_fname:
                    continue
                text = pdf_fname

            n_scanned += 1
            all_message_ids.append(message.id)

            parsed_list = parse_messages(text, channel_username, message_id=message.id)
            if not parsed_list:
                n_skipped += 1
                continue

            for parsed in parsed_list:
                tasks.append(_ReportTask(
                    parsed=parsed,
                    message=message,
                    pdf_fname=pdf_fname,
                    channel_username=channel_username,
                    client=client,
                ))

        log.info("backfill_phase1_done", channel=channel_username,
                 messages=n_scanned, tasks=len(tasks))

        # Phase 2: 병렬 처리 (느린 S2a + Layer2)
        semaphore = asyncio.Semaphore(_BACKFILL_CONCURRENCY)
        results = await asyncio.gather(
            *[_process_single_report(t, semaphore) for t in tasks],
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, Exception):
                log.warning("backfill_task_error", error=str(r))
                continue
            if r.action == "inserted":
                n_saved += 1
            elif r.action == "pending":
                n_pending += 1
            elif r.action in ("skipped", "error"):
                n_skipped += 1

        last_id = max(all_message_ids) if all_message_ids else 0

    except FloodWaitError as e:
        log.warning("flood_wait", seconds=e.seconds, channel=channel_username)
        await asyncio.sleep(e.seconds)
        last_id = 0
    except Exception as e:
        async with AsyncSessionLocal() as session:
            run_row = await session.get(BackfillRun, run_id)
            if run_row:
                run_row.status = "error"
                run_row.error_msg = str(e)[:500]
                run_row.finished_at = datetime.now(timezone.utc)
                run_row.n_scanned = n_scanned
                run_row.n_saved = n_saved
                run_row.n_pending = n_pending
                run_row.n_skipped = n_skipped
                await session.commit()
        raise

    # last_message_id 업데이트
    if last_id:
        async with AsyncSessionLocal() as session:
            channel_row = await session.scalar(
                select(Channel).where(Channel.channel_username == channel_username)
            )
            if channel_row:
                channel_row.last_message_id = last_id
                session.add(channel_row)
            else:
                session.add(Channel(
                    channel_username=channel_username,
                    last_message_id=last_id,
                ))
            await session.commit()

    # 런 완료 기록
    async with AsyncSessionLocal() as session:
        run_row = await session.get(BackfillRun, run_id)
        if run_row:
            run_row.status = "done"
            run_row.finished_at = datetime.now(timezone.utc)
            run_row.n_scanned = n_scanned
            run_row.n_saved = n_saved
            run_row.n_pending = n_pending
            run_row.n_skipped = n_skipped
            run_row.to_message_id = last_id or None
            await session.commit()

    # PDF 실패율 체크
    async with AsyncSessionLocal() as session:
        from db.models import Report as _Report
        total_with_url = await session.scalar(
            select(func.count()).where(
                _Report.source_channel == channel_username,
                _Report.pdf_url.isnot(None),
            )
        )
        total_failed = await session.scalar(
            select(func.count()).where(
                _Report.source_channel == channel_username,
                _Report.pdf_download_failed.is_(True),
            )
        )
        if total_with_url and total_failed / total_with_url >= 0.5:
            log.warning(
                "pdf_failure_rate_high",
                channel=channel_username,
                failed=total_failed,
                total_with_url=total_with_url,
                rate=f"{total_failed / total_with_url:.0%}",
            )

    log.info("backfill_done", channel=channel_username, run_id=run_id,
             saved=n_saved, pending=n_pending, skipped=n_skipped)
    return n_saved


async def backfill_all() -> None:
    client = get_client()
    await client.start()

    async with AsyncSessionLocal() as session:
        rows = (await session.scalars(
            select(Channel).where(Channel.is_active == True)
        )).all()
        channels = [r.channel_username for r in rows] or settings.telegram_channels

    for channel in channels:
        try:
            await backfill_channel(channel)
        except Exception as e:
            log.error("backfill_error", channel=channel, error=str(e))

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(backfill_all())
