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
import tracemalloc

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
tracemalloc.start()
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import csv
import datetime
import logging
import time
import warnings
from pathlib import Path

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
    build_user_content, build_batch_request, run_layer2_batch, submit_layer2_batch, make_layer2_result,
)
from storage.llm_usage_repo import record_llm_usage
from storage.analysis_repo import save_analysis, log_analysis_failure
from storage.report_repo import update_pipeline_status
from parser.meta_updater import apply_layer2_meta, apply_key_data_meta

log = structlog.get_logger(__name__)

_REPORT_TIMEOUT = 1800  # 건당 최대 30분 (각 단계에 자체 timeout 있음)
_CONCURRENCY = 4  # Phase 1 동시 처리 건수
_BATCH_THRESHOLD = 100  # Layer2 Batch 제출 임계값
_MAX_CONCURRENT_BATCHES = 3  # 동시에 폴링 중인 Batch 최대 수

# 퀀트 리포트 타입 (차트 수치화 대상)
_QUANT_REPORT_TYPES = {"퀀트"}

# 로그 파일 경로
_MARKDOWN_FAILURE_LOG = "logs/markdown_failures.csv"
_PENDING_BATCHES_PATH = Path("logs/pending_batches.jsonl")
_BATCH_FAILURE_LOG_PATH = Path("logs/layer2_batch_failures.log")
_MEMORY_LOG_PATH = Path("logs/memory_profile.log")
_MEMORY_SNAPSHOT_INTERVAL = 50  # N건마다 top allocator 스냅샷


def _log_memory(label: str, report_id: int = 0) -> None:
    """tracemalloc 기반 메모리 로깅."""
    current, peak = tracemalloc.get_traced_memory()
    cur_mb = current / 1024 / 1024
    peak_mb = peak / 1024 / 1024
    log.info("memory", label=label, report_id=report_id,
             current_mb=f"{cur_mb:.1f}", peak_mb=f"{peak_mb:.1f}")


def _dump_memory_snapshot(done_count: int) -> None:
    """Top memory allocators를 파일에 덤프."""
    snapshot = tracemalloc.take_snapshot()
    stats = snapshot.statistics("lineno")
    _MEMORY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_MEMORY_LOG_PATH, "a", encoding="utf-8") as f:
        current, peak = tracemalloc.get_traced_memory()
        f.write(f"\n=== Snapshot at {done_count} reports | "
                f"current={current/1024/1024:.1f}MB peak={peak/1024/1024:.1f}MB ===\n")
        for stat in stats[:20]:
            f.write(f"  {stat}\n")


def _log_markdown_failure(report_id: int, reason: str, pdf_path: str) -> None:
    """마크다운 실패를 CSV 파일에 기록."""
    log_path = Path(_MARKDOWN_FAILURE_LOG)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()
    with open(log_path, "a", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp)
        if is_new:
            writer.writerow(["timestamp", "report_id", "reason", "pdf_path"])
        writer.writerow([
            datetime.datetime.now().isoformat(),
            report_id,
            reason,
            pdf_path,
        ])


def _log_batch_failure(batch_num: int, report_ids: list[int], error: str) -> None:
    """Layer2 배치 실패를 로그 파일에 기록."""
    log_path = Path(_BATCH_FAILURE_LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} batch_attempt={batch_num} report_ids={report_ids} error={error}\n"
    with open(log_path, "a", encoding="utf-8") as fp:
        fp.write(line)


def _load_pending_batch_report_ids() -> set[int]:
    """pending_batches.jsonl에서 이미 배치 제출된 report ID 집합 반환."""
    ids: set[int] = set()
    if not _PENDING_BATCHES_PATH.exists():
        return ids
    try:
        import json
        for line in _PENDING_BATCHES_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            batch = json.loads(line)
            for cid in batch.get("custom_ids", []):
                # "report-12345" → 12345
                if cid.startswith("report-"):
                    ids.add(int(cid[7:]))
    except Exception as e:
        log.warning("pending_batches_parse_error", error=str(e))
    return ids


async def _get_unanalyzed_report_ids(limit: int | None) -> list[int]:
    """pdf_path 있고 report_analysis 없는 건의 ID만 조회. pdf_done 이상만 대상.
    pending_batches.jsonl에 이미 제출된 건은 제외."""
    from db.models import ReportAnalysis

    _ANALYZABLE_STATUSES = ("pdf_done", "analysis_pending")

    # 이미 배치 제출된 ID 제외
    pending_ids = _load_pending_batch_report_ids()
    if pending_ids:
        log.info("excluding_pending_batch_ids", count=len(pending_ids))

    async with AsyncSessionLocal() as session:
        analyzed_ids = select(ReportAnalysis.report_id).scalar_subquery()
        query = select(ReportModel.id).where(
            ReportModel.pdf_path.isnot(None),
            ReportModel.id.notin_(analyzed_ids),
            ReportModel.pipeline_status.in_(_ANALYZABLE_STATUSES),
        )
        if pending_ids:
            query = query.where(ReportModel.id.notin_(pending_ids))
        result = await session.execute(
            query.order_by(ReportModel.report_date.desc()).limit(limit)
        )
        return list(result.scalars().all())


async def _load_report(report_id: int) -> ReportModel | None:
    """단건 Report 로드."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReportModel).where(ReportModel.id == report_id)
        )
        return result.scalar_one_or_none()


async def process_single(report: ReportModel, chart_mode: str = "auto") -> dict:
    """단일 리포트 전체 분석 파이프라인. 각 단계 독립적으로 실패 허용.

    Args:
        report: ReportModel 인스턴스
        chart_mode: "auto" | "enabled" | "disabled"
            - "auto": 퀀트 리포트만 차트 수치화
            - "enabled": 모든 리포트 차트 수치화
            - "disabled": 차트 수치화 스킵
    """
    result = {"report_id": report.id, "status": "ok", "steps": {}}
    abs_path = settings.pdf_base_path / report.pdf_path
    _log_memory("start", report_id=report.id)

    if not abs_path.exists():
        result["status"] = "error"
        result["error"] = "pdf_not_found"
        return result

    channel = report.source_channel or ""
    _t_start = time.monotonic()

    # 분석 시작 전 상태 기록
    async with AsyncSessionLocal() as session:
        await update_pipeline_status(session, report.id, "analysis_pending")
        await session.commit()

    # ③ 키 데이터 추출
    _step_t = time.monotonic()
    log.info("step_start", report_id=report.id, step="key_data")
    key_data = None
    try:
        key_data = await extract_key_data(abs_path, report_id=report.id, channel=channel)
        if key_data:
            # key_data.date → report_date 업데이트 (잘못된 날짜 보정)
            parsed_date = None
            if key_data.date:
                try:
                    from datetime import date as _date
                    parsed_date = _date.fromisoformat(key_data.date)
                except (ValueError, TypeError):
                    pass

            key_meta = apply_key_data_meta(key_data, parsed_date=parsed_date)
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
    log.info("step_done", report_id=report.id, step="key_data",
             duration_s=round(time.monotonic() - _step_t, 2))

    # ① 마크다운 변환
    markdown_text = None
    _step_t = time.monotonic()
    log.info("step_start", report_id=report.id, step="markdown")
    try:
        markdown_text, converter_name = await convert_pdf_to_markdown(abs_path)
        result["steps"]["markdown"] = "ok" if markdown_text else "empty"
    except Exception as e:
        log.warning("markdown_error", report_id=report.id, error=str(e))
        result["steps"]["markdown"] = f"error: {e}"
    log.info("step_done", report_id=report.id, step="markdown",
             duration_s=round(time.monotonic() - _step_t, 2))
    _log_memory("after_markdown", report_id=report.id)

    # ② 이미지 추출 + ④ 차트 수치화
    images = []
    dig_result = None
    chart_texts = None

    # 차트 수치화 여부 결정
    key_data_report_type = key_data.report_type if key_data else None
    _should_digitize = (
        chart_mode == "enabled" or
        (chart_mode == "auto" and key_data_report_type in _QUANT_REPORT_TYPES)
    )

    _step_t = time.monotonic()
    log.info("step_start", report_id=report.id, step="images")
    try:
        images = await extract_images_from_pdf(abs_path)
        result["steps"]["images"] = f"{len(images)} extracted"
        if images:
            if _should_digitize:
                dig_result = await digitize_charts(images, report_id=report.id, channel=channel)
                if dig_result.texts:
                    chart_texts = dig_result.texts
                result["steps"]["charts"] = f"{dig_result.success_count}/{len(images)} digitized"
            else:
                log.info("charts_skipped", report_id=report.id,
                         report_type=key_data_report_type, reason="non_quant")
                result["steps"]["charts"] = "skipped"
        else:
            result["steps"]["charts"] = "no_images"
    except Exception as e:
        log.warning("image_chart_error", report_id=report.id, error=str(e))
        result["steps"]["images"] = f"error: {e}"
        result["steps"]["charts"] = "skipped"
    finally:
        # 이미지 바이트 즉시 해제 (건당 수 MB)
        n_images = len(images)
        del images
    log.info("step_done", report_id=report.id, step="images_charts",
             duration_s=round(time.monotonic() - _step_t, 2))
    _log_memory("after_images", report_id=report.id)

    # 품질 게이트: 마크다운이 너무 짧으면 skip
    _MIN_MARKDOWN_CHARS = 200
    if not markdown_text:
        log.warning("markdown_missing", report_id=report.id)
        result["status"] = "no_markdown"
        async with AsyncSessionLocal() as session:
            await update_pipeline_status(session, report.id, "analysis_failed")
            await session.commit()
        _log_markdown_failure(report.id, "no_markdown", str(abs_path))
        log.info("report_done", report_id=report.id,
                 duration_s=round(time.monotonic() - _t_start, 2), status="no_markdown")
        return result

    if len(markdown_text.strip()) < _MIN_MARKDOWN_CHARS:
        log.warning("markdown_too_short", report_id=report.id, chars=len(markdown_text.strip()))
        result["status"] = "low_quality_markdown"
        async with AsyncSessionLocal() as session:
            await update_pipeline_status(session, report.id, "analysis_failed")
            await session.commit()
        _log_markdown_failure(report.id, "low_quality_markdown", str(abs_path))
        log.info("report_done", report_id=report.id,
                 duration_s=round(time.monotonic() - _t_start, 2), status="low_quality_markdown")
        return result

    # 품질 게이트: 차트 수치화 과반 실패 시 warning (Layer2는 진행하되 기록)
    if n_images and dig_result:
        fail_rate = 1 - (dig_result.success_count / n_images) if n_images > 0 else 0
        if fail_rate > 0.5:
            log.warning("chart_digitize_low_quality",
                        report_id=report.id,
                        success=dig_result.success_count,
                        total=n_images,
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
        # markdown/chart 원본은 user_content에 합쳐졌으므로 해제
        del markdown_text, chart_texts
        result["layer2_input"] = {
            "user_content": user_content,
            "md_truncated": md_truncated,
            "md_chars": md_chars,
            "channel": channel,
        }
    else:
        result["status"] = "no_markdown"

    log.info("report_done", report_id=report.id,
             duration_s=round(time.monotonic() - _t_start, 2), status=result["status"])
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

            meta_updates = apply_layer2_meta(report, layer2.meta)
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


async def _submit_and_save_batch(layer2_inputs: dict, batch_num: int) -> str | None:
    """Layer2 Batch 제출만 (fire-and-forget). 폴링/저장 없음.

    Returns: batch_id if submitted successfully, None on failure.
    """
    batch_requests = [
        build_batch_request(cid, inp["layer2_input"]["user_content"])
        for cid, inp in layer2_inputs.items()
    ]
    log.info("batch_submit", batch_num=batch_num, count=len(batch_requests))

    try:
        batch_id = await submit_layer2_batch(batch_requests)
    except Exception as e:
        report_ids = [inp["report_id"] for inp in layer2_inputs.values()]
        _log_batch_failure(batch_num, report_ids, str(e))
        log.error("layer2_batch_failed", batch_num=batch_num, error=str(e))
        return None

    log.info("batch_submitted", batch_num=batch_num, batch_id=batch_id, count=len(layer2_inputs))
    return batch_id


async def main(args: argparse.Namespace) -> None:
    # Install asyncio exception handler for fire-and-forget task failures
    try:
        install_asyncio_handler(asyncio.get_event_loop(), "run_analysis")
    except RuntimeError:
        pass

    limit = args.limit if args.limit else None  # 0 또는 미지정 = 전체

    # Determine chart_mode from CLI flags
    if getattr(args, "enable_charts", False):
        chart_mode = "enabled"
    elif getattr(args, "disable_charts", False):
        chart_mode = "disabled"
    else:
        chart_mode = "auto"

    # Dump mode: write Layer2 inputs to JSONL instead of submitting to Anthropic
    dump_layer2 = getattr(args, "dump_layer2", False)
    dump_layer2_path = getattr(args, "dump_layer2_path", "logs/layer2_dump.jsonl") or "logs/layer2_dump.jsonl"

    print(f"=== Run Analysis ===")
    print(f"Limit: {limit}")
    print(f"Dry run: {args.dry_run}")
    print(f"Gemini: {'ON' if settings.gemini_api_key else 'OFF'}")
    print(f"Anthropic: {'ON' if settings.anthropic_api_key else 'OFF'}")
    if dump_layer2:
        print(f"Dump Layer2: ON → {dump_layer2_path}")
    print()

    report_ids = await _get_unanalyzed_report_ids(limit)
    total = len(report_ids)
    print(f"Unanalyzed reports with PDF: {total}")

    if not report_ids:
        print("Nothing to do.")
        return

    if args.dry_run:
        # dry-run은 소량이므로 개별 로드
        for rid in report_ids[:200]:
            r = await _load_report(rid)
            if r:
                print(f"  [{r.id}] {r.report_date} | {r.broker or '-':15s} | "
                      f"{r.stock_name or r.sector or '-':15s} | {(r.title or '')[:50]}")
        if total > 200:
            print(f"  ... 외 {total - 200}건")
        print(f"\n총 {total}건 대상 (--dry-run)")
        return

    # Phase 1 + 2 통합: PDF 분석 → N건 모이면 Layer2 Batch 제출 (streaming)
    concurrency = args.concurrency
    batch_threshold = args.batch_size
    max_concurrent_batches = getattr(args, "max_batches", _MAX_CONCURRENT_BATCHES)
    print(f"\n>>> 분석 시작 ({total}건, 동시 {concurrency}건, 배치 {batch_threshold}건)")
    done = 0
    l2_count = 0  # Layer2 대상 건수 (메모리 해제 후에도 카운트 유지)
    submitted_batch_ids: list[str] = []
    batch_num = 0

    # Layer2 버퍼: threshold 도달 시 batch 제출
    l2_buffer: dict[str, dict] = {}

    # 비동기 batch 추적
    _pending_batches: list[asyncio.Task] = []
    _batch_semaphore = asyncio.Semaphore(max_concurrent_batches)

    # Dump mode: JSONL 파일 핸들 (dump_layer2=True 일 때만 사용)
    _dump_file = None
    if dump_layer2:
        import json as _json
        _dump_path = Path(dump_layer2_path)
        _dump_path.parent.mkdir(parents=True, exist_ok=True)
        _dump_file = open(_dump_path, "a", encoding="utf-8")
        log.info("dump_layer2_mode", path=str(_dump_path))

    async def _flush_buffer():
        """버퍼에 쌓인 layer2_inputs를 asyncio.create_task()로 백그라운드 제출.

        dump_layer2 모드에서는 Anthropic API 대신 JSONL 파일에 기록.
        pipeline_status는 analysis_pending 유지 (done으로 전이하지 않음).
        """
        nonlocal batch_num
        if not l2_buffer:
            return

        # Dump mode: JSONL 파일에 기록하고 반환 (Anthropic API 호출 없음)
        if dump_layer2:
            import json as _json
            for cid, entry in l2_buffer.items():
                l2_inp = entry.get("layer2_input", {})
                record = {
                    "report_id": entry["report_id"],
                    "user_content": l2_inp.get("user_content", ""),
                    "md_truncated": l2_inp.get("md_truncated", False),
                    "md_chars": l2_inp.get("md_chars", 0),
                    "channel": l2_inp.get("channel", ""),
                }
                _dump_file.write(_json.dumps(record, ensure_ascii=False) + "\n")
            _dump_file.flush()
            l2_buffer.clear()
            return

        if not settings.anthropic_api_key:
            return
        batch_num += 1
        # 버퍼 복사 후 비우기 — copy 후 clear이므로 race condition 없음
        to_submit = dict(l2_buffer)
        l2_buffer.clear()
        current_batch_num = batch_num

        async def _batch_task():
            # 세마포어로 동시 batch 수 제한
            async with _batch_semaphore:
                try:
                    batch_id = await _submit_and_save_batch(to_submit, current_batch_num)
                    if batch_id:
                        submitted_batch_ids.append(batch_id)
                except BaseException as e:
                    log.error("batch_task_failed", batch_num=current_batch_num, error=str(e))
                finally:
                    # 제출 완료 후 user_content(markdown 전문) 해제
                    for entry in to_submit.values():
                        entry.pop("layer2_input", None)

        task = asyncio.create_task(_batch_task())
        _pending_batches.append(task)

    queue: asyncio.Queue[int] = asyncio.Queue()
    for rid in report_ids:
        queue.put_nowait(rid)
    del report_ids  # ID 리스트도 해제

    async def _worker():
        nonlocal done, l2_count
        while True:
            try:
                report_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            report = None
            try:
                report = await _load_report(report_id)
                if report is None:
                    log.warning("report_not_found", report_id=report_id)
                    continue
                r = await asyncio.wait_for(
                    process_single(report, chart_mode=chart_mode),
                    timeout=_REPORT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log.warning("analysis_timeout", report_id=report_id, timeout=_REPORT_TIMEOUT)
                r = {"report_id": report_id, "status": "timeout"}
                async with AsyncSessionLocal() as session:
                    await update_pipeline_status(session, report_id, "analysis_failed")
                    await session.commit()
            except Exception as e:
                log.error("analysis_error", report_id=report_id, error=str(e))
                r = {"report_id": report_id, "status": f"error: {e}"}
                async with AsyncSessionLocal() as session:
                    await update_pipeline_status(session, report_id, "analysis_failed")
                    await session.commit()
            finally:
                queue.task_done()
                del report  # ORM 객체 즉시 해제
            done += 1
            # pymupdf C 레벨 캐시 + Python GC 정리
            try:
                import gc
                import pymupdf
                pymupdf.TOOLS.store_shrink(100)
                pymupdf.TOOLS.glyph_cache_empty()
                gc.collect()
            except Exception:
                pass
            _log_memory("after_process", report_id=report_id)
            if done % _MEMORY_SNAPSHOT_INTERVAL == 0:
                _dump_memory_snapshot(done)
            status = r["status"]
            steps = r.get("steps", {})
            has_l2 = "layer2_input" in r
            log.info("analyzed", progress=f"{done}/{total}", report_id=report_id,
                     status=status, has_layer2=has_l2,
                     md=steps.get("markdown", "-"), charts=steps.get("charts", "-"))

            # Layer2 버퍼에 추가
            if has_l2:
                l2_count += 1
                cid = f"report-{r['report_id']}"
                l2_buffer[cid] = r
                if len(l2_buffer) >= batch_threshold:
                    await _flush_buffer()

    num_workers = min(concurrency, total)
    if num_workers > 0:
        workers = [asyncio.create_task(_worker()) for _ in range(num_workers)]
        await asyncio.gather(*workers)

    # 잔여 버퍼 flush
    if l2_buffer:
        await _flush_buffer()

    # 백그라운드 batch task 전부 완료 대기 (total_saved가 확정된 후 summary 출력)
    if _pending_batches:
        await asyncio.gather(*_pending_batches, return_exceptions=True)

    # Dump mode: 파일 닫기
    if _dump_file is not None:
        _dump_file.close()
        print(f"\nDump Layer2: {l2_count}건 → {dump_layer2_path}")

    if not dump_layer2 and not settings.anthropic_api_key and l2_count > 0:
        print(f"\nAnthropic API key not set — {l2_count}건 Layer2 미처리.")

    print(f"\n=== Done ===")
    print(f"  Processed: {done}")
    print(f"  Layer2 submitted: {l2_count}")
    if not dump_layer2:
        print(f"  Batches submitted: {batch_num}")
        for bid in submitted_batch_ids:
            print(f"    - {bid}")
        print(f"  (Results will be saved by recover_batches.py --from-pending)")

    mark_clean_exit()


def cli():
    parser = argparse.ArgumentParser(description="PDF 분석 (수집과 독립 실행)")
    parser.add_argument("--limit", type=int, default=0, help="처리 건수 제한 (0=전체)")
    parser.add_argument("--concurrency", type=int, default=_CONCURRENCY, help=f"Phase 1 동시 처리 건수 (기본값: {_CONCURRENCY})")
    parser.add_argument("--batch-size", type=int, default=_BATCH_THRESHOLD, help=f"Layer2 Batch 제출 단위 (기본값: {_BATCH_THRESHOLD})")
    parser.add_argument("--dry-run", action="store_true", help="대상만 확인")
    parser.add_argument(
        "--dump-layer2",
        action="store_true",
        default=False,
        help="Layer2 Batch 제출 대신 JSONL 파일로 덤프 (Anthropic API 키 불필요)",
    )
    parser.add_argument(
        "--dump-layer2-path",
        type=str,
        default="logs/layer2_dump.jsonl",
        help="--dump-layer2 출력 경로 (기본값: logs/layer2_dump.jsonl)",
    )

    charts_group = parser.add_mutually_exclusive_group()
    charts_group.add_argument("--enable-charts", action="store_true", default=False,
                               help="모든 리포트 타입에 차트 수치화 강제 활성화")
    charts_group.add_argument("--disable-charts", action="store_true", default=False,
                               help="모든 리포트 타입에 차트 수치화 강제 비활성화")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    setup_crash_logging(sentinel_name=".analysis_running", process_name="run_analysis")
    asyncio.run(main(args))
