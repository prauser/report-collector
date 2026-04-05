"""기존 리포트 Layer 2 분석 백필 스크립트.

analysis_status = 'pending'인 리포트를 배치 처리하여 Layer 2 추출을 수행.

사용법:
  python -m scripts.run_analysis                   # pending 전부
  python -m scripts.run_analysis --limit 50        # 최대 50건
  python -m scripts.run_analysis --dry-run         # 실제 LLM 호출 없이 대상 확인
  python -m scripts.run_analysis --reprocess v0    # 특정 버전을 재처리
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update as sa_update, func

from config.settings import settings
from db.session import AsyncSessionLocal
from db.models import Report, ReportMarkdown
from parser.markdown_converter import convert_pdf_to_markdown
from parser.layer2_extractor import extract_layer2
from storage.analysis_repo import save_markdown, save_analysis, log_analysis_failure

log = structlog.get_logger(__name__)


async def get_pending_reports(
    limit: int,
    reprocess_version: str | None = None,
) -> list[Report]:
    """분석 대기 중인 리포트 목록 조회."""
    async with AsyncSessionLocal() as session:
        query = select(Report)

        if reprocess_version:
            # 특정 버전 재처리
            query = query.where(Report.analysis_version == reprocess_version)
        else:
            # pending 상태만
            query = query.where(
                Report.analysis_status.in_(["pending", None])
            )

        query = query.order_by(Report.report_date.desc()).limit(limit)
        result = await session.execute(query)
        return list(result.scalars().all())


async def process_report(report: Report) -> bool:
    """단일 리포트 Layer 2 분석 처리. 성공 시 True."""

    # 1) Markdown 변환 (PDF 있으면)
    markdown_text = None
    converter_name = ""
    if report.pdf_path:
        abs_path = settings.pdf_base_path / report.pdf_path
        if abs_path.exists():
            # 기존 마크다운 확인
            async with AsyncSessionLocal() as session:
                existing_md = await session.scalar(
                    select(ReportMarkdown.markdown_text)
                    .where(ReportMarkdown.report_id == report.id)
                )
            if existing_md:
                markdown_text = existing_md
                converter_name = "cached"
            else:
                markdown_text, converter_name = await convert_pdf_to_markdown(abs_path)

    # 2) Layer 2 추출
    layer2 = await extract_layer2(
        text=report.raw_text or report.title,
        markdown=markdown_text,
        channel=report.source_channel,
        report_id=report.id,
    )

    if not layer2:
        log.warning("analysis_skipped", report_id=report.id, reason="layer2_returned_none")
        async with AsyncSessionLocal() as session:
            await log_analysis_failure(session, report.id, "extract_layer2", "LLM returned None")
            await session.commit()
        return False

    # 3) 저장
    async with AsyncSessionLocal() as session:
        if markdown_text and converter_name != "cached":
            await save_markdown(session, report.id, markdown_text, converter_name)

        # 메타데이터 보강
        from parser.meta_updater import apply_layer2_meta
        meta_updates = apply_layer2_meta(report, layer2.meta)
        if meta_updates:
            await session.execute(
                sa_update(Report)
                .where(Report.id == report.id)
                .values(**meta_updates)
            )

        try:
            await save_analysis(session, report.id, layer2)
            await session.commit()
            return True
        except Exception as e:
            log.error("analysis_save_failed", report_id=report.id, error=str(e))
            await session.rollback()
            async with AsyncSessionLocal() as err_session:
                await log_analysis_failure(err_session, report.id, "extract_layer2", str(e))
                await err_session.commit()
            return False


async def main(args: argparse.Namespace) -> None:
    limit = args.limit or settings.analysis_batch_size
    reprocess = args.reprocess

    log.info("run_analysis_start", limit=limit, reprocess=reprocess, dry_run=args.dry_run)

    reports = await get_pending_reports(limit, reprocess)

    if not reports:
        log.info("no_pending_reports")
        return

    log.info("found_reports", count=len(reports))

    if args.dry_run:
        import sys, io
        out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        for r in reports:
            has_pdf = bool(r.pdf_path)
            out.write(
                f"  [{r.id}] {r.report_date} | {r.broker or '-':15s} | "
                f"{r.stock_name or r.sector or '-':15s} | "
                f"PDF={'Y' if has_pdf else 'N'} | "
                f"{(r.title or '')[:40]}\n"
            )
        out.write(f"\n총 {len(reports)}건 대상 (--dry-run 모드)\n")
        out.flush()
        return

    n_success = 0
    n_fail = 0

    for i, report in enumerate(reports, 1):
        log.info(
            "processing",
            progress=f"{i}/{len(reports)}",
            report_id=report.id,
            title=(report.title or "")[:40],
        )
        try:
            ok = await process_report(report)
            if ok:
                n_success += 1
            else:
                n_fail += 1
        except Exception as e:
            log.error("process_error", report_id=report.id, error=str(e))
            n_fail += 1

    log.info("run_analysis_done", success=n_success, fail=n_fail, total=len(reports))


def cli():
    parser = argparse.ArgumentParser(description="기존 리포트 Layer 2 분석 백필")
    parser.add_argument("--limit", type=int, default=0, help="처리 건수 제한 (0=batch_size)")
    parser.add_argument("--dry-run", action="store_true", help="대상만 확인, 실제 처리 안 함")
    parser.add_argument("--reprocess", type=str, default=None,
                        help="특정 analysis_version을 재처리 (예: v0)")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    asyncio.run(main(args))
