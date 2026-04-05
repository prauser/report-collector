"""히스토리 백필 스크립트 - 채널의 과거 메시지를 소급 수집.

파이프라인 (listener와 동일):
  S2a(분류) → DB저장 → PDF다운 → Markdown변환 → Layer2 추출 → DB(분석 저장)
"""
import asyncio
import structlog
from dataclasses import dataclass
from datetime import date, datetime, timezone
from telethon.errors import FloodWaitError
from telethon.tl.types import Message

from collector.telegram_client import get_client
from config.settings import settings
from db.session import AsyncSessionLocal
from db.models import BackfillRun, Channel, Report as ReportModel, ReportAnalysis
from parser.registry import parse_messages
from storage import stock_mapper
from parser.llm_parser import classify_message
from parser.quality import assess_parse_quality
from parser.markdown_converter import convert_pdf_to_markdown
from parser.image_extractor import extract_images_from_pdf
from parser.chart_digitizer import digitize_charts
from parser.key_data_extractor import extract_key_data
from parser.layer2_extractor import (
    build_user_content, build_batch_request, run_layer2_batch, make_layer2_result,
)
from storage.llm_usage_repo import record_llm_usage
from storage.pending_repo import save_pending
from storage.pdf_archiver import attempt_pdf_download, pdf_filename as _pdf_filename_shared
from sqlalchemy.exc import IntegrityError
from storage.report_repo import upsert_report, update_pipeline_status
from storage.analysis_repo import save_analysis, log_analysis_failure
from parser.meta_updater import apply_layer2_meta
from sqlalchemy import select, update as sa_update, func

log = structlog.get_logger(__name__)

_BACKFILL_CONCURRENCY = 5  # 동시 처리 리포트 수
_SKIP_STATUSES = ("s2a_done", "pdf_done", "pdf_failed", "analysis_pending", "analysis_failed", "done")


def _pdf_filename(message: Message) -> str | None:
    """Document 타입 PDF 메시지에서 파일명 추출. PDF가 아니면 None.

    Delegates to storage.pdf_archiver.pdf_filename (shared implementation).
    Kept here for backward compatibility — callers may import from collector.backfill.
    """
    return _pdf_filename_shared(message)


@dataclass
class _ReportTask:
    parsed: object
    message: Message
    pdf_fname: str | None
    channel_username: str
    client: object


@dataclass
class _Layer2Input:
    """Batch Layer2 처리용 입력 데이터."""
    report_id: int
    user_content: str
    channel: str
    md_was_truncated: bool
    md_original_chars: int


@dataclass
class _ReportResult:
    action: str  # 'saved' | 'pending' | 'skipped' | 'error'
    message_id: int
    layer2_input: _Layer2Input | None = None


async def _process_single_report(task: _ReportTask) -> _ReportResult:
    """단일 ParsedReport 전체 파이프라인 처리 (S2a → upsert → PDF → layer2)."""
    parsed = task.parsed
    message = task.message
    channel_username = task.channel_username
    client = task.client

    if parsed.report_date is None or parsed.report_date == date.today():
        parsed.report_date = message.date.date()

    # S2a: 분류
    s2a = await classify_message(parsed)

    if s2a.message_type in ("news", "general"):
        return _ReportResult("skipped", message.id)

    if s2a.message_type == "ambiguous" and not parsed.pdf_url and not task.pdf_fname:
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

        # S2a 완료 후 상태 기록 (upsert 이후 report.id 사용 가능)
        await update_pipeline_status(session, report.id, "s2a_done")

        # 2) PDF 다운로드 (3-stage fallback: Telegram doc → t.me resolve → HTTP)
        if report.pdf_path:
            # PDF가 이미 있는 경우 → pdf_done 상태만 업데이트
            await update_pipeline_status(session, report.id, "pdf_done")
        else:
            # Stage 1 대상: pdf_fname이 있을 때만 원본 메시지에서 직접 다운로드
            tg_message = message if task.pdf_fname else None
            tme_links = parsed.tme_message_links if parsed.tme_message_links else None

            # 다운로드 소스가 있을 때만 attempt_pdf_download 호출 (no_source 내부 commit 방지)
            if tg_message is not None or tme_links or report.pdf_url:
                success, rel_path, _size_kb, _fail_reason, _retryable = await attempt_pdf_download(
                    client=client,
                    report=report,
                    message=tg_message,
                    tme_links=tme_links,
                    session=session,
                )
                if success:
                    report.pdf_path = rel_path

        # 이미 분석된 리포트는 Layer2 skip
        already_analyzed = await session.scalar(
            select(ReportAnalysis.id).where(ReportAnalysis.report_id == report.id)
        )
        if already_analyzed:
            await session.commit()
            return _ReportResult(action, message.id)

        # 3) 키 데이터 추출 + Markdown 변환 + 이미지 추출
        #    analysis_enabled=False 일 때는 분석 단계 전부 스킵
        markdown_text = None
        chart_texts: list[str] | None = None
        if settings.analysis_enabled and report.pdf_path:
            abs_path = settings.pdf_base_path / report.pdf_path
            if abs_path.exists():
                # ③ 키 데이터 추출 (첫 페이지만, Flash-Lite)
                key_data = await extract_key_data(
                    abs_path, report_id=report.id, channel=channel_username,
                )
                if key_data:
                    _t = lambda v, n: v[:n] if isinstance(v, str) and len(v) > n else v
                    parsed_date = None
                    if key_data.date:
                        try:
                            parsed_date = date.fromisoformat(key_data.date)
                        except (ValueError, TypeError):
                            pass
                    key_meta = {
                        k: v for k, v in {
                            "broker": _t(key_data.broker, 50),
                            "analyst": _t(key_data.analyst, 100),
                            "stock_name": _t(key_data.stock_name, 100),
                            "stock_code": key_data.stock_code,
                            "opinion": _t(key_data.opinion, 20),
                            "target_price": key_data.target_price,
                            "report_type": _t(key_data.report_type, 50),
                            "title": _t(key_data.title, 500),
                            "report_date": parsed_date,
                        }.items() if v
                    }
                    if key_meta:
                        try:
                            async with session.begin_nested():
                                await session.execute(
                                    sa_update(ReportModel)
                                    .where(ReportModel.id == report.id)
                                    .values(**key_meta)
                                )
                        except Exception:
                            log.debug("key_meta_update_skipped", report_id=report.id)

                markdown_text, converter_name = await convert_pdf_to_markdown(abs_path)

                # ② 차트/테이블 이미지 분리 → ④ Gemini 수치화
                # Gemini 키가 없으면 이미지 추출도 스킵 (수치화 불가 시 추출 자체가 낭비)
                if settings.gemini_api_key:
                    images = await extract_images_from_pdf(abs_path)
                    if images:
                        dig_result = await digitize_charts(
                            images, report_id=report.id, channel=channel_username,
                        )
                        if dig_result.texts:
                            chart_texts = dig_result.texts

                # key_data + markdown + charts 완료 → analysis_pending
                await update_pipeline_status(session, report.id, "analysis_pending")

        # 4) Layer2 입력 준비 (실제 호출은 Batch API로 일괄 처리)
        #    markdown 없으면 스킵 (PDF 다운로드 실패 시 텍스트만으론 품질 부족)
        layer2_input = None
        if markdown_text and settings.analysis_enabled and settings.anthropic_api_key:
            user_content, md_truncated, md_chars = build_user_content(
                text=parsed.raw_text,
                markdown=markdown_text,
                chart_texts=chart_texts,
                channel=channel_username,
            )
            layer2_input = _Layer2Input(
                report_id=report.id,
                user_content=user_content,
                channel=channel_username,
                md_was_truncated=md_truncated,
                md_original_chars=md_chars,
            )

        await session.commit()

    return _ReportResult(action, message.id, layer2_input=layer2_input)


async def backfill_channel(channel_username: str, limit: int | None = None, reverse: bool = False) -> int:
    """
    채널의 히스토리를 백필.
    reverse=False (기본): last_message_id 이후 → 오래된 것부터 (forward)
    reverse=True: 최신 메시지부터 → 과거로 (backward)
    Returns: 저장된 레코드 수
    """
    client = get_client()

    async with AsyncSessionLocal() as session:
        channel_row = await session.scalar(
            select(Channel).where(Channel.channel_username == channel_username)
        )
        min_id = channel_row.last_message_id if channel_row else 0
        reverse_min_id = channel_row.reverse_min_id if channel_row else None

    # 런 기록 생성
    run = BackfillRun(
        channel_username=channel_username,
        run_date=date.today(),
        from_message_id=reverse_min_id if reverse else (min_id or None),
        status="running",
    )
    async with AsyncSessionLocal() as session:
        session.add(run)
        await session.commit()
        await session.refresh(run)
    run_id = run.id

    log.info("backfill_start", channel=channel_username, min_id=min_id, run_id=run_id,
             direction="backward" if reverse else "forward")

    n_scanned = n_saved = n_pending = n_skipped = 0
    effective_limit = limit or settings.backfill_limit or None
    all_message_ids: list[int] = []

    try:
        # Phase 0.5: 이미 처리된 message_id 조회 (S2a 재호출 방지)
        async with AsyncSessionLocal() as session:
            existing_msg_ids = set((await session.scalars(
                select(ReportModel.source_message_id).where(
                    ReportModel.source_channel == channel_username,
                    ReportModel.source_message_id.isnot(None),
                    ReportModel.pipeline_status.in_(_SKIP_STATUSES),
                )
            )).all())
        log.info("existing_reports_loaded", channel=channel_username, count=len(existing_msg_ids))

        # Phase 1: 메시지 수집 + 파싱 (빠름)
        tasks: list[_ReportTask] = []
        n_already_done = 0
        iter_kwargs = {"limit": effective_limit}
        if reverse:
            # 최신→과거: offset_id부터 아래로 (Telethon 기본 = 최신부터)
            iter_kwargs["reverse"] = False
            if reverse_min_id:
                iter_kwargs["offset_id"] = reverse_min_id
                log.info("reverse_resume", channel=channel_username, offset_id=reverse_min_id)
        else:
            # 과거→최신: min_id 이후, reverse=True (오래된 것부터)
            iter_kwargs["min_id"] = min_id or 0
            iter_kwargs["reverse"] = True
        async for message in client.iter_messages(
            channel_username,
            **iter_kwargs,
        ):
            if not isinstance(message, Message):
                continue

            text = message.text or ""
            pdf_fname = _pdf_filename(message)  # 항상 체크
            if not text.strip():
                if not pdf_fname:
                    continue
                text = pdf_fname

            n_scanned += 1
            all_message_ids.append(message.id)

            # 이미 처리된 메시지는 S2a 호출 없이 skip
            if message.id in existing_msg_ids:
                n_already_done += 1
                n_skipped += 1
                continue

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
                 messages=n_scanned, tasks=len(tasks), already_done=n_already_done)

        # Phase 2: Queue + Worker 패턴 (타임아웃이 큐 대기 시간 제외)
        _TASK_TIMEOUT = 300  # 건당 최대 5분
        _CHECKPOINT_INTERVAL = 100  # N건마다 진행 위치 저장
        results: list[_ReportResult | Exception] = []
        layer2_inputs: dict[str, _Layer2Input] = {}
        n_processed = 0

        async def _save_checkpoint():
            """현재까지 처리한 진행 위치를 DB에 저장."""
            if not all_message_ids:
                return
            try:
                async with AsyncSessionLocal() as session:
                    channel_row = await session.scalar(
                        select(Channel).where(Channel.channel_username == channel_username)
                    )
                    if not channel_row:
                        return
                    if reverse:
                        new_min = min(all_message_ids)
                        if not channel_row.reverse_min_id or new_min < channel_row.reverse_min_id:
                            channel_row.reverse_min_id = new_min
                            log.info("checkpoint_reverse_min_id", channel=channel_username, reverse_min_id=new_min)
                    else:
                        channel_row.last_message_id = max(all_message_ids)
                        log.info("checkpoint_last_message_id", channel=channel_username, last_id=max(all_message_ids))
                    await session.commit()
            except Exception as e:
                log.warning("checkpoint_failed", channel=channel_username, error=str(e))

        async def _worker():
            nonlocal n_processed
            while True:
                try:
                    task = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    result = await asyncio.wait_for(
                        _process_single_report(task), timeout=_TASK_TIMEOUT
                    )
                    results.append(result)
                    if result.layer2_input:
                        cid = f"report-{result.layer2_input.report_id}"
                        layer2_inputs[cid] = result.layer2_input
                except asyncio.TimeoutError:
                    log.warning("task_timeout", message_id=task.message.id, timeout=_TASK_TIMEOUT)
                    results.append(_ReportResult("error", task.message.id))
                except Exception as exc:
                    log.warning("backfill_task_error", error=str(exc), message_id=task.message.id)
                    results.append(exc)
                finally:
                    queue.task_done()
                    n_processed += 1
                    if n_processed % _CHECKPOINT_INTERVAL == 0:
                        await _save_checkpoint()

        queue = asyncio.Queue()
        for t in tasks:
            queue.put_nowait(t)

        num_workers = min(_BACKFILL_CONCURRENCY, len(tasks))
        if num_workers > 0:
            workers = [asyncio.create_task(_worker()) for _ in range(num_workers)]
            total_timeout = max(600, _TASK_TIMEOUT * len(tasks) // num_workers + 120)
            try:
                await asyncio.wait_for(asyncio.gather(*workers), timeout=total_timeout)
            except asyncio.TimeoutError:
                log.error("backfill_worker_total_timeout",
                          channel=channel_username, timeout=total_timeout,
                          tasks=len(tasks), num_workers=num_workers)
                for w in workers:
                    w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

        # 결과 집계
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

        # Phase 3: Batch API로 Layer2 일괄 추출 (50% 할인 + Prompt Caching)
        if layer2_inputs:
            log.info("layer2_batch_start", count=len(layer2_inputs))
            batch_requests = [
                build_batch_request(cid, inp.user_content)
                for cid, inp in layer2_inputs.items()
            ]
            batch_results, failed_ids = await run_layer2_batch(batch_requests)

            # failed_ids → pipeline_status='analysis_failed'
            for failed_cid in failed_ids:
                failed_inp = layer2_inputs.get(failed_cid)
                if failed_inp:
                    async with AsyncSessionLocal() as session:
                        await update_pipeline_status(session, failed_inp.report_id, "analysis_failed")
                        await session.commit()
                    log.warning("layer2_batch_failed_set_status", report_id=failed_inp.report_id, custom_id=failed_cid)

            # Phase 4: Batch 결과 저장
            for cid, (tool_input, in_tok, out_tok, cc_tok, cr_tok) in batch_results.items():
                inp = layer2_inputs[cid]
                layer2 = make_layer2_result(
                    tool_input, in_tok, out_tok, cc_tok, cr_tok,
                    inp.md_was_truncated, inp.md_original_chars,
                    is_batch=True,
                )
                if not layer2:
                    log.warning("layer2_batch_no_result", report_id=inp.report_id)
                    continue

                await record_llm_usage(
                    model=settings.llm_pdf_model,
                    purpose="layer2_batch",
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cache_creation_tokens=cc_tok,
                    cache_read_tokens=cr_tok,
                    is_batch=True,
                    source_channel=inp.channel,
                    report_id=inp.report_id,
                )

                async with AsyncSessionLocal() as session:
                    report = await session.get(ReportModel, inp.report_id)
                    if not report:
                        continue
                    meta_updates = apply_layer2_meta(report, layer2.meta)
                    if meta_updates:
                        try:
                            async with session.begin_nested():
                                await session.execute(
                                    sa_update(ReportModel)
                                    .where(ReportModel.id == inp.report_id)
                                    .values(**meta_updates)
                                )
                        except IntegrityError:
                            log.debug("meta_update_skipped_dedup", report_id=inp.report_id)
                    try:
                        await save_analysis(session, inp.report_id, layer2)
                    except Exception as e:
                        log.warning("analysis_save_failed", report_id=inp.report_id, error=str(e))
                        await session.rollback()
                        async with AsyncSessionLocal() as err_session:
                            await log_analysis_failure(err_session, inp.report_id, "layer2_batch", str(e))
                            await err_session.commit()
                        continue
                    await session.commit()

            log.info("layer2_batch_done",
                     submitted=len(layer2_inputs), succeeded=len(batch_results))

        last_id = max(all_message_ids) if all_message_ids else 0

    except FloodWaitError as e:
        log.warning("flood_wait", seconds=e.seconds, channel=channel_username)
        await asyncio.sleep(e.seconds)
        last_id = max(all_message_ids) if all_message_ids else 0
    except Exception as e:
        # 에러 시에도 처리한 만큼 last_id 기록 (재스캔 방지)
        last_id = max(all_message_ids) if all_message_ids else 0
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
                run_row.to_message_id = last_id or None
                await session.commit()
        raise
    finally:
        # 정상/에러 모두: 진행 위치 업데이트
        if all_message_ids:
            try:
                async with AsyncSessionLocal() as session:
                    channel_row = await session.scalar(
                        select(Channel).where(Channel.channel_username == channel_username)
                    )
                    if not channel_row:
                        channel_row = Channel(channel_username=channel_username)
                        session.add(channel_row)

                    if reverse:
                        # reverse: 가장 작은 ID로 reverse_min_id 갱신
                        new_min = min(all_message_ids)
                        if not channel_row.reverse_min_id or new_min < channel_row.reverse_min_id:
                            channel_row.reverse_min_id = new_min
                            log.info("reverse_min_id_updated", channel=channel_username, reverse_min_id=new_min)
                    else:
                        # forward: 가장 큰 ID로 last_message_id 갱신
                        channel_row.last_message_id = max(all_message_ids)
                        log.info("last_message_id_updated", channel=channel_username, last_id=max(all_message_ids))

                    await session.commit()
            except Exception as update_err:
                log.warning("progress_update_failed", channel=channel_username, error=str(update_err))

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
        total_with_url = await session.scalar(
            select(func.count()).where(
                ReportModel.source_channel == channel_username,
                ReportModel.pdf_url.isnot(None),
            )
        )
        total_failed = await session.scalar(
            select(func.count()).where(
                ReportModel.source_channel == channel_username,
                ReportModel.pdf_download_failed.is_(True),
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
