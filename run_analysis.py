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
from collector.listener import _apply_layer2_meta

log = structlog.get_logger(__name__)

_REPORT_TIMEOUT = 300  # 건당 최대 5분


async def _get_unanalyzed_reports(limit: int) -> list[ReportModel]:
    """pdf_path 있고 report_analysis 없는 건 조회."""
    from db.models import ReportAnalysis

    async with AsyncSessionLocal() as session:
        analyzed_ids = select(ReportAnalysis.report_id).scalar_subquery()
        result = await session.execute(
            select(ReportModel).where(
                ReportModel.pdf_path.isnot(None),
                ReportModel.id.notin_(analyzed_ids),
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

    # ③ 키 데이터 추출
    try:
        key_data = await extract_key_data(abs_path, report_id=report.id, channel=channel)
        if key_data:
            def _trunc(val, maxlen):
                return val[:maxlen] if isinstance(val, str) and len(val) > maxlen else val

            key_meta = {
                k: v for k, v in {
                    "broker": _trunc(key_data.broker, 50),
                    "analyst": _trunc(key_data.analyst, 100),
                    "stock_name": _trunc(key_data.stock_name, 100),
                    "stock_code": key_data.stock_code,
                    "opinion": _trunc(key_data.opinion, 20),
                    "target_price": key_data.target_price,
                    "report_type": _trunc(key_data.report_type, 50),
                }.items() if v
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


async def main(args: argparse.Namespace) -> None:
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

    # Phase 1: 개별 분석 (키데이터 + 마크다운 + 이미지 + 차트)
    print(f"\n>>> Phase 1: PDF 분석 ({len(reports)}건)...")
    results = []
    for i, report in enumerate(reports, 1):
        try:
            r = await asyncio.wait_for(process_single(report), timeout=_REPORT_TIMEOUT)
            results.append(r)
            status = r["status"]
            steps = r.get("steps", {})
            has_l2 = "layer2_input" in r
            log.info("analyzed", progress=f"{i}/{len(reports)}", report_id=report.id,
                     status=status, has_layer2=has_l2,
                     md=steps.get("markdown", "-"), charts=steps.get("charts", "-"))
        except asyncio.TimeoutError:
            log.warning("analysis_timeout", report_id=report.id, timeout=_REPORT_TIMEOUT)
            results.append({"report_id": report.id, "status": "timeout"})
        except Exception as e:
            log.error("analysis_error", report_id=report.id, error=str(e))
            results.append({"report_id": report.id, "status": f"error: {e}"})

    # Phase 2: Layer2 Batch API
    layer2_inputs = {}
    for r in results:
        if "layer2_input" in r:
            cid = f"report-{r['report_id']}"
            layer2_inputs[cid] = r

    if not layer2_inputs:
        print("\nNo reports ready for Layer2 (no markdown).")
        print(f"=== Done: {len(results)} processed, 0 Layer2 ===")
        return

    if not settings.anthropic_api_key:
        print(f"\nAnthropic API key not set — skipping Layer2.")
        print(f"=== Done: {len(results)} processed, {len(layer2_inputs)} ready for Layer2 ===")
        return

    print(f"\n>>> Phase 2: Layer2 Batch API ({len(layer2_inputs)}건)...")
    batch_requests = [
        build_batch_request(cid, inp["layer2_input"]["user_content"])
        for cid, inp in layer2_inputs.items()
    ]

    try:
        batch_results = await run_layer2_batch(batch_requests)
    except Exception as e:
        log.error("layer2_batch_failed", error=str(e))
        print(f"\nLayer2 Batch failed: {e}")
        print(f"=== Done: {len(results)} processed, Layer2 FAILED ===")
        return

    # Phase 3: Batch 결과 저장
    print(f"\n>>> Phase 3: 결과 저장 ({len(batch_results)}/{len(layer2_inputs)}건)...")
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

    print(f"\n=== Done ===")
    print(f"  Processed: {len(results)}")
    print(f"  Layer2 submitted: {len(layer2_inputs)}")
    print(f"  Layer2 saved: {n_saved}")


def cli():
    parser = argparse.ArgumentParser(description="PDF 분석 (수집과 독립 실행)")
    parser.add_argument("--limit", type=int, default=0, help="처리 건수 제한 (0=전체)")
    parser.add_argument("--dry-run", action="store_true", help="대상만 확인")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    asyncio.run(main(args))
