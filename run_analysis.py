"""PDF 분석 스크립트 — 수집과 독립적으로 실행.

DB에서 pdf_path가 있지만 report_analysis가 없는 건을 조회하여 분석.
파이프라인: 키데이터(Gemini) → 마크다운(pymupdf4llm) → 이미지추출 → 차트수치화(Gemini) → Layer2(Sonnet Batch)

사용법:
  python run_analysis.py                 # 미분석 전체
  python run_analysis.py --limit 50      # 최대 50건
  python run_analysis.py --dry-run       # 대상만 확인
"""
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import warnings

import structlog

warnings.filterwarnings("ignore", category=DeprecationWarning)
structlog.configure(
    processors=[structlog.dev.ConsoleRenderer()],
    wrapper_class=structlog.BoundLogger,
)

from utils.crash_logging import setup_crash_logging, install_asyncio_handler, mark_clean_exit

from sqlalchemy import select, update as sa_update
from sqlalchemy.exc import IntegrityError

from config.settings import settings
from db.session import AsyncSessionLocal
from db.models import Report as ReportModel
from parser.markdown_converter import convert_pdf_to_markdown
from parser.image_extractor import extract_images_from_pdf
from parser.chart_digitizer import digitize_charts
from parser.key_data_extractor import extract_key_data
from parser.layer2_extractor import (
    build_user_content, build_batch_request, run_layer2_batch, make_layer2_result,
)
from storage.llm_usage_repo import record_llm_usage
from storage.analysis_repo import save_analysis, log_analysis_failure
from storage.report_repo import update_pipeline_status
from collector.listener import _apply_layer2_meta

log = structlog.get_logger(__name__)

_REPORT_TIMEOUT = 1800  # 건당 최대 30분 (각 단계에 자체 timeout 있음)
_CONCURRENCY = 4  # Phase 1 동시 처리 건수
_BATCH_THRESHOLD = 100  # Layer2 Batch 제출 임계값
_MAX_CONCURRENT_BATCHES = 3  # 동시에 폴링 중인 Batch 최대 수


async def _get_unanalyzed_reports(limit: int) -> list[ReportModel]:
    """pdf_path 있고 report_analysis 없는 건 조회. pdf_done 이상만 대상."""
    from db.models import ReportAnalysis

    _ANALYZABLE_STATUSES = ("pdf_done", "analysis_pending")

    async with AsyncSessionLocal() as session:
        analyzed_ids = select(ReportAnalysis.report_id).scalar_subquery()
        result = await session.execute(
            select(ReportModel).where(
                ReportModel.pdf_path.isnot(None),
                ReportModel.id.notin_(analyzed_ids),
                ReportModel.pipeline_status.in_(_ANALYZABLE_STATUSES),
            )
            .order_by(ReportModel.report_date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def process_single(report: ReportModel) -> dict:
    """단일 리포트 전체 분석 파이프라인. 각 단계 독립적으로 실패 허용."""
    result = {"report_id": report.id, "status": "ok", "steps": {}}
    abs_path = settings.pdf_base_path / report.pdf_path

    if not abs_path.exists():
        result["status"] = "error"
        result["error"] = "pdf_not_found"
        return result

    channel = report.source_channel or ""

    # 분석 시작 전 상태 기록
    async with AsyncSessionLocal() as session:
        await update_pipeline_status(session, report.id, "analysis_pending")
        await session.commit()

    # ③ 키 데이터 추출
    log.info("step_start", report_id=report.id, step="key_data")
    try:
        key_data = await extract_key_data(abs_path, report_id=report.id, channel=channel)
        if key_data:
            def _trunc(val, maxlen):
                return val[:maxlen] if isinstance(val, str) and len(val) > maxlen else val

            # key_data.date → report_date 업데이트 (잘못된 날짜 보정)
            parsed_date = None
            if key_data.date:
                try:
                    from datetime import date as _date
                    parsed_date = _date.fromisoformat(key_data.date)
                except (ValueError, TypeError):
                    pass

            key_meta = {
                k: v for k, v in {
                    "broker": _trunc(key_data.broker, 50),
                    "analyst": _trunc(key_data.analyst, 100),
                    "stock_name": _trunc(key_data.stock_name, 100),
                    "stock_code": key_data.stock_code,
                    "opinion": _trunc(key_data.opinion, 20),
                    "target_price": key_data.target_price,
                    "report_type": _trunc(key_data.report_type, 50),
                    "title": _trunc(key_data.title, 500),
                    "report_date": parsed_date,
                }.items() if v is not None
            }
            if key_meta:
                async with AsyncSessionLocal() as session:
                    try:
                        await session.execute(
                            sa_update(ReportModel).where(ReportModel.id == report.id).values(**key_meta)
                        )
                        await session.commit()
                    except Exception:
                        await session.rollback()
            result["steps"]["key_data"] = "ok"
        else:
            result["steps"]["key_data"] = "empty"
    except Exception as e:
        log.warning("key_data_error", report_id=report.id, error=str(e))
        result["steps"]["key_data"] = f"error: {e}"

    # ① 마크다운 변환
    markdown_text = None
    log.info("step_start", report_id=report.id, step="markdown")
    try:
        markdown_text, converter_name = await convert_pdf_to_markdown(abs_path)
        result["steps"]["markdown"] = "ok" if markdown_text else "empty"
    except Exception as e:
        log.warning("markdown_error", report_id=report.id, error=str(e))
        result["steps"]["markdown"] = f"error: {e}"

    # ② 이미지 추출 + ④ 차트 수치화
    images = []
    dig_result = None
    chart_texts = None
    log.info("step_start", report_id=report.id, step="images")
    try:
        images = await extract_images_from_pdf(abs_path)
        result["steps"]["images"] = f"{len(images)} extracted"
        if images:
            dig_result = await digitize_charts(images, report_id=report.id, channel=channel)
            if dig_result.texts:
                chart_texts = dig_result.texts
            result["steps"]["charts"] = f"{dig_result.success_count}/{len(images)} digitized"
        else:
            result["steps"]["charts"] = "no_images"
    except Exception as e:
        log.warning("image_chart_error", report_id=report.id, error=str(e))
        result["steps"]["images"] = f"error: {e}"
        result["steps"]["charts"] = "skipped"

    # 품질 게이트: 마크다운이 너무 짧으면 skip
    _MIN_MARKDOWN_CHARS = 200
    if markdown_text and len(markdown_text.strip()) < _MIN_MARKDOWN_CHARS:
        log.warning("markdown_too_short", report_id=report.id, chars=len(markdown_text.strip()))
        result["status"] = "low_quality_markdown"
        return result

    # 품질 게이트: 차트 수치화 과반 실패 시 warning (Layer2는 진행하되 기록)
    if images and dig_result:
        fail_rate = 1 - (dig_result.success_count / len(images)) if len(images) > 0 else 0
        if fail_rate > 0.5:
            log.warning("chart_digitize_low_quality",
                        report_id=report.id,
                        success=dig_result.success_count,
                        total=len(images),
                        fail_rate=f"{fail_rate:.0%}")
            result["steps"]["chart_quality"] = "low"

    # Layer2 입력 준비 (실제 호출은 Batch로 일괄)
    if markdown_text:
        user_content, md_truncated, md_chars = build_user_content(
            text=report.raw_text or report.title,
            markdown=markdown_text,
            chart_texts=chart_texts,
            channel=channel,
        )
        result["layer2_input"] = {
            "user_content": user_content,
            "md_truncated": md_truncated,
            "md_chars": md_chars,
            "channel": channel,
        }
    else:
        result["status"] = "no_markdown"

    return result


async def _save_batch_results(
    batch_results: dict, failed_ids: list[str], layer2_inputs: dict,
) -> int:
    """Batch 결과를 DB에 저장. Returns: 저장 건수."""
    if failed_ids:
        for failed_cid in failed_ids:
            failed_inp = layer2_inputs.get(failed_cid)
            if failed_inp:
                async with AsyncSessionLocal() as session:
                    await update_pipeline_status(session, failed_inp["report_id"], "analysis_failed")
                    await session.commit()
                log.warning("layer2_batch_failed_set_status",
                            report_id=failed_inp["report_id"], custom_id=failed_cid)

    n_saved = 0
    for cid, (tool_input, in_tok, out_tok, cc_tok, cr_tok) in batch_results.items():
        inp = layer2_inputs[cid]
        l2_input = inp["layer2_input"]

        layer2 = make_layer2_result(
            tool_input, in_tok, out_tok, cc_tok, cr_tok,
            l2_input["md_truncated"], l2_input["md_chars"],
            is_batch=True,
        )
        if not layer2:
            log.warning("layer2_no_result", report_id=inp["report_id"])
            continue

        report_id = inp["report_id"]

        await record_llm_usage(
            model=settings.llm_pdf_model,
            purpose="layer2_batch",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_tokens=cc_tok,
            cache_read_tokens=cr_tok,
            is_batch=True,
            source_channel=l2_input["channel"],
            report_id=report_id,
        )

        async with AsyncSessionLocal() as session:
            report = await session.get(ReportModel, report_id)
            if not report:
                continue

            meta_updates = _apply_layer2_meta(report, layer2.meta)
            if meta_updates:
                try:
                    async with session.begin_nested():
                        await session.execute(
                            sa_update(ReportModel).where(ReportModel.id == report_id).values(**meta_updates)
                        )
                except IntegrityError:
                    log.debug("meta_update_skipped", report_id=report_id)

            try:
                await save_analysis(session, report_id, layer2)
                await session.commit()
                n_saved += 1
            except Exception as e:
                log.warning("analysis_save_failed", report_id=report_id, error=str(e))
                await session.rollback()
                async with AsyncSessionLocal() as err_session:
                    await log_analysis_failure(err_session, report_id, "layer2_batch", str(e))
                    await err_session.commit()

    return n_saved


async def _submit_and_save_batch(layer2_inputs: dict, batch_num: int) -> int:
    """Layer2 Batch 제출 → 폴링 → 저장. Returns: 저장 건수."""
    batch_requests = [
        build_batch_request(cid, inp["layer2_input"]["user_content"])
        for cid, inp in layer2_inputs.items()
    ]
    log.info("batch_submit", batch_num=batch_num, count=len(batch_requests))

    try:
        batch_results, failed_ids = await run_layer2_batch(batch_requests)
    except Exception as e:
        log.error("layer2_batch_failed", batch_num=batch_num, error=str(e))
        return 0

    n_saved = await _save_batch_results(batch_results, failed_ids, layer2_inputs)
    log.info("batch_saved", batch_num=batch_num, saved=n_saved, total=len(layer2_inputs))
    return n_saved


async def main(args: argparse.Namespace) -> None:
    # Install asyncio exception handler for fire-and-forget task failures
    try:
        install_asyncio_handler(asyncio.get_event_loop(), "run_analysis")
    except RuntimeError:
        pass

    limit = args.limit or 9999

    print(f"=== Run Analysis ===")
    print(f"Limit: {limit}")
    print(f"Dry run: {args.dry_run}")
    print(f"Gemini: {'ON' if settings.gemini_api_key else 'OFF'}")
    print(f"Anthropic: {'ON' if settings.anthropic_api_key else 'OFF'}")
    print()

    reports = await _get_unanalyzed_reports(limit)
    print(f"Unanalyzed reports with PDF: {len(reports)}")

    if not reports:
        print("Nothing to do.")
        return

    if args.dry_run:
        for r in reports:
            print(f"  [{r.id}] {r.report_date} | {r.broker or '-':15s} | "
                  f"{r.stock_name or r.sector or '-':15s} | {(r.title or '')[:50]}")
        print(f"\n총 {len(reports)}건 대상 (--dry-run)")
        return

    # Phase 1 + 2 통합: PDF 분석 → N건 모이면 Layer2 Batch 제출 (streaming)
    concurrency = args.concurrency
    batch_threshold = args.batch_size
    max_concurrent_batches = getattr(args, "max_batches", _MAX_CONCURRENT_BATCHES)
    print(f"\n>>> 분석 시작 ({len(reports)}건, 동시 {concurrency}건, 배치 {batch_threshold}건)")
    results: list[dict] = []
    done = 0
    total_saved = 0
    batch_num = 0

    # Layer2 버퍼: threshold 도달 시 batch 제출
    l2_buffer: dict[str, dict] = {}

    # 비동기 batch 추적
    _pending_batches: list[asyncio.Task] = []
    _batch_semaphore = asyncio.Semaphore(max_concurrent_batches)

    async def _flush_buffer():
        """버퍼에 쌓인 layer2_inputs를 asyncio.create_task()로 백그라운드 제출."""
        nonlocal batch_num
        if not l2_buffer or not settings.anthropic_api_key:
            return
        batch_num += 1
        # 버퍼 복사 후 비우기 — copy 후 clear이므로 race condition 없음
        to_submit = dict(l2_buffer)
        l2_buffer.clear()
        current_batch_num = batch_num

        async def _batch_task():
            nonlocal total_saved
            # 세마포어로 동시 batch 수 제한
            async with _batch_semaphore:
                try:
                    n = await _submit_and_save_batch(to_submit, current_batch_num)
                    # asyncio는 single-thread: await 사이에서만 switching
                    # 따라서 total_saved += n 은 atomic하게 동작
                    total_saved += n
                except BaseException as e:
                    log.error("batch_task_failed", batch_num=current_batch_num, error=str(e))

        task = asyncio.create_task(_batch_task())
        _pending_batches.append(task)

    queue: asyncio.Queue = asyncio.Queue()
    for report in reports:
        queue.put_nowait(report)

    async def _worker():
        nonlocal done
        while True:
            try:
                report = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                r = await asyncio.wait_for(process_single(report), timeout=_REPORT_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning("analysis_timeout", report_id=report.id, timeout=_REPORT_TIMEOUT)
                r = {"report_id": report.id, "status": "timeout"}
                async with AsyncSessionLocal() as session:
                    await update_pipeline_status(session, report.id, "analysis_failed")
                    await session.commit()
            except Exception as e:
                log.error("analysis_error", report_id=report.id, error=str(e))
                r = {"report_id": report.id, "status": f"error: {e}"}
                async with AsyncSessionLocal() as session:
                    await update_pipeline_status(session, report.id, "analysis_failed")
                    await session.commit()
            finally:
                queue.task_done()
            done += 1
            status = r["status"]
            steps = r.get("steps", {})
            has_l2 = "layer2_input" in r
            log.info("analyzed", progress=f"{done}/{len(reports)}", report_id=report.id,
                     status=status, has_layer2=has_l2,
                     md=steps.get("markdown", "-"), charts=steps.get("charts", "-"))
            results.append(r)

            # Layer2 버퍼에 추가
            if has_l2:
                cid = f"report-{r['report_id']}"
                l2_buffer[cid] = r
                if len(l2_buffer) >= batch_threshold:
                    await _flush_buffer()

    num_workers = min(concurrency, len(reports))
    if num_workers > 0:
        workers = [asyncio.create_task(_worker()) for _ in range(num_workers)]
        await asyncio.gather(*workers)

    # 잔여 버퍼 flush
    if l2_buffer:
        await _flush_buffer()

    # 백그라운드 batch task 전부 완료 대기 (total_saved가 확정된 후 summary 출력)
    if _pending_batches:
        await asyncio.gather(*_pending_batches, return_exceptions=True)

    if not settings.anthropic_api_key and any("layer2_input" in r for r in results):
        l2_ready = sum(1 for r in results if "layer2_input" in r)
        print(f"\nAnthropic API key not set — {l2_ready}건 Layer2 미처리.")

    print(f"\n=== Done ===")
    print(f"  Processed: {len(results)}")
    print(f"  Batches submitted: {batch_num}")
    print(f"  Layer2 saved: {total_saved}")

    mark_clean_exit()


def cli():
    parser = argparse.ArgumentParser(description="PDF 분석 (수집과 독립 실행)")
    parser.add_argument("--limit", type=int, default=0, help="처리 건수 제한 (0=전체)")
    parser.add_argument("--concurrency", type=int, default=_CONCURRENCY, help=f"Phase 1 동시 처리 건수 (기본값: {_CONCURRENCY})")
    parser.add_argument("--batch-size", type=int, default=_BATCH_THRESHOLD, help=f"Layer2 Batch 제출 단위 (기본값: {_BATCH_THRESHOLD})")
    parser.add_argument("--dry-run", action="store_true", help="대상만 확인")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    setup_crash_logging(sentinel_name=".analysis_running", process_name="run_analysis")
    asyncio.run(main(args))
