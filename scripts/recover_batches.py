"""Anthropic Batch 복구 스크립트.

완료된 batch 결과를 DB에 저장하거나, analysis_pending 상태인 리포트를 확인한다.

사용법:
  python scripts/recover_batches.py --help
  python scripts/recover_batches.py --batch-ids msgbatch_abc msgbatch_def
  python scripts/recover_batches.py --batch-ids-file batches.txt
  python scripts/recover_batches.py --recover-all
  python scripts/recover_batches.py --list-pending
  python scripts/recover_batches.py --recover-all --apply
"""
import sys
import os
import argparse
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from pathlib import Path

import structlog

from db.session import AsyncSessionLocal
from db.models import Report as ReportModel, ReportAnalysis
from storage.analysis_repo import save_analysis
from storage.llm_usage_repo import record_llm_usage
from storage.report_repo import update_pipeline_status
from parser.layer2_extractor import make_layer2_result, _remove_pending_batch
from parser.meta_updater import apply_layer2_meta as _apply_layer2_meta
from config.settings import settings
from sqlalchemy import select, update as sa_update
from sqlalchemy.exc import IntegrityError

_PENDING_BATCHES_PATH = Path(__file__).parent.parent / "logs" / "pending_batches.jsonl"

log = structlog.get_logger(__name__)


async def _analysis_exists(session, report_id: int) -> bool:
    """report_id에 대한 ReportAnalysis 행이 있는지 확인."""
    result = await session.execute(
        select(ReportAnalysis.id).where(ReportAnalysis.report_id == report_id)
    )
    return bool(result.scalar())


async def _list_pending_reports() -> list[dict]:
    """pipeline_status='analysis_pending'인 리포트 목록 반환."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReportModel).where(
                ReportModel.pipeline_status == "analysis_pending"
            ).order_by(ReportModel.report_date.desc())
        )
        rows = result.all()

    out = []
    for (row,) in rows:
        out.append({
            "id": row.id,
            "title": row.title,
            "report_date": str(row.report_date) if row.report_date else None,
            "channel": row.source_channel,
        })
    return out


def _print_summary(summary: dict, dry_run: bool) -> None:
    """배치 결과 요약 출력."""
    batch_id = summary["batch_id"]
    status = summary["status"]
    succeeded = summary["succeeded"]
    errored = summary["errored"]
    expired = summary["expired"]
    total = summary["total"]
    saved = summary["saved"]
    error = summary.get("error")
    ids_succeeded = summary.get("report_ids_succeeded", [])
    ids_failed = summary.get("report_ids_failed", [])
    ids_unknown = summary.get("report_ids_unknown_custom_id", [])

    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"\n[{mode}] Batch: {batch_id}")
    print(f"  Status:    {status}")
    print(f"  Succeeded: {succeeded}")
    print(f"  Errored:   {errored}")
    print(f"  Expired:   {expired}")
    print(f"  Total:     {total}")
    if not dry_run:
        print(f"  Saved to DB: {saved}")
    if ids_succeeded:
        print(f"  Report IDs (ok):  {sorted(ids_succeeded)}")
    if ids_failed:
        print(f"  Report IDs (fail): {sorted(ids_failed)}")
    if ids_unknown:
        print(f"  Unknown custom IDs: {ids_unknown}")
    if error:
        print(f"  Error: {error}")


async def _check_and_recover_batch(client, batch_id: str, apply: bool) -> dict:
    """단일 batch ID를 확인하고 필요시 결과를 DB에 저장."""
    summary = {
        "batch_id": batch_id,
        "status": "unknown",
        "succeeded": 0,
        "errored": 0,
        "expired": 0,
        "total": 0,
        "saved": 0,
        "error": None,
        "report_ids_succeeded": [],
        "report_ids_failed": [],
        "report_ids_unknown_custom_id": [],
    }

    # 1) Batch 조회
    try:
        batch = await client.messages.batches.retrieve(batch_id)
    except Exception as e:
        summary["status"] = "retrieve_error"
        summary["error"] = str(e)
        return summary

    counts = batch.request_counts
    summary["succeeded"] = counts.succeeded
    summary["errored"] = counts.errored
    summary["expired"] = counts.expired
    summary["total"] = counts.succeeded + counts.errored + counts.expired

    if batch.processing_status != "ended":
        summary["status"] = batch.processing_status
        return summary

    summary["status"] = "ended"

    # 2) 결과 스트리밍
    try:
        results_iter = await client.messages.batches.results(batch_id)
    except Exception as e:
        summary["status"] = "results_error"
        summary["error"] = str(e)
        return summary

    async for entry in results_iter:
        cid = entry.custom_id

        # custom_id 형식: "report-{report_id}"
        if not cid.startswith("report-") or not cid[len("report-"):].isdigit():
            summary["report_ids_unknown_custom_id"].append(cid)
            continue

        report_id = int(cid[len("report-"):])

        if entry.result.type != "succeeded":
            summary["report_ids_failed"].append(report_id)
            if apply:
                async with AsyncSessionLocal() as session:
                    await update_pipeline_status(session, report_id, "analysis_failed")
                    await session.commit()
            continue

        # Succeeded entry
        summary["report_ids_succeeded"].append(report_id)

        msg = entry.result.message
        usage = msg.usage
        in_tok = usage.input_tokens
        out_tok = usage.output_tokens
        cc_tok = usage.cache_creation_input_tokens
        cr_tok = usage.cache_read_input_tokens

        # tool_input 추출
        tool_input = None
        for block in msg.content:
            if block.type == "tool_use" and block.name == "extract_layer2":
                tool_input = block.input
                break

        layer2 = make_layer2_result(
            tool_input, in_tok, out_tok, cc_tok, cr_tok,
            False, 0,
            is_batch=True,
        )
        if not layer2:
            continue

        await record_llm_usage(
            model=settings.llm_pdf_model,
            purpose="layer2_batch",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_tokens=cc_tok,
            cache_read_tokens=cr_tok,
            is_batch=True,
            source_channel=None,
            report_id=report_id,
        )

        if not apply:
            continue

        async with AsyncSessionLocal() as session:
            report = await session.get(ReportModel, report_id)
            if not report:
                log.warning("recover_report_not_found", report_id=report_id)
                continue

            meta_updates = _apply_layer2_meta(report, layer2.meta)
            if meta_updates:
                try:
                    async with session.begin_nested():
                        await session.execute(
                            sa_update(ReportModel)
                            .where(ReportModel.id == report_id)
                            .values(**meta_updates)
                        )
                except IntegrityError:
                    log.debug("meta_update_skipped", report_id=report_id)

            try:
                await save_analysis(session, report_id, layer2)
                await session.commit()
                summary["saved"] += 1
            except Exception as e:
                log.warning("recover_save_failed", report_id=report_id, error=str(e))
                await session.rollback()

    return summary


async def _recover_all_batches(client, apply: bool) -> None:
    """Anthropic API에서 모든 batch 목록을 조회하여 succeeded > 0인 ended batch를 복구."""
    # 1) 배치 목록 조회
    eligible = []
    async for batch in await client.messages.batches.list():
        if batch.processing_status != "ended":
            continue
        if batch.request_counts.succeeded <= 0:
            continue
        eligible.append(batch)

    print(f"\n{len(eligible)} eligible batch(es) found.")

    total_saved = 0
    total_already_exists = 0
    total_would_save = 0
    total_wrong_status = 0
    total_errors = 0

    for batch in eligible:
        batch_id = batch.batch_id if hasattr(batch, "batch_id") else batch.id

        # 2) 결과 스트리밍
        try:
            results_iter = await client.messages.batches.results(batch_id)
        except Exception as e:
            print(f"  ERROR streaming results for {batch_id}: {e}")
            total_errors += 1
            continue

        batch_saved = 0
        batch_would_save = 0
        batch_already_exists = 0
        batch_wrong_status = 0

        async for entry in results_iter:
            if entry.result.type != "succeeded":
                continue

            cid = entry.custom_id
            if not cid.startswith("report-") or not cid[len("report-"):].isdigit():
                continue

            report_id = int(cid[len("report-"):])

            # tool_input 추출
            msg = entry.result.message
            usage = msg.usage
            in_tok = usage.input_tokens
            out_tok = usage.output_tokens
            cc_tok = usage.cache_creation_input_tokens
            cr_tok = usage.cache_read_input_tokens

            tool_input = None
            for block in msg.content:
                if block.type == "tool_use" and block.name == "extract_layer2":
                    tool_input = block.input
                    break

            layer2 = make_layer2_result(
                tool_input, in_tok, out_tok, cc_tok, cr_tok,
                False, 0,
                is_batch=True,
            )
            if not layer2:
                continue

            async with AsyncSessionLocal() as session:
                exists = await _analysis_exists(session, report_id)
                if exists:
                    batch_already_exists += 1
                    continue

                report = await session.get(ReportModel, report_id)
                if not report:
                    continue

                if report.pipeline_status != "analysis_pending":
                    batch_wrong_status += 1
                    continue

                if not apply:
                    batch_would_save += 1
                    continue

                # Apply mode: save
                await record_llm_usage(
                    model=settings.llm_pdf_model,
                    purpose="layer2_batch",
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cache_creation_tokens=cc_tok,
                    cache_read_tokens=cr_tok,
                    is_batch=True,
                    source_channel=None,
                    report_id=report_id,
                )

                meta_updates = _apply_layer2_meta(report, layer2.meta)
                if meta_updates:
                    try:
                        async with session.begin_nested():
                            await session.execute(
                                sa_update(ReportModel)
                                .where(ReportModel.id == report_id)
                                .values(**meta_updates)
                            )
                    except IntegrityError:
                        log.debug("meta_update_skipped", report_id=report_id)

                try:
                    await save_analysis(session, report_id, layer2)
                    await session.commit()
                    batch_saved += 1
                except Exception as e:
                    log.warning("recover_all_save_failed", report_id=report_id, error=str(e))
                    await session.rollback()

        total_saved += batch_saved
        total_already_exists += batch_already_exists
        total_would_save += batch_would_save
        total_wrong_status += batch_wrong_status

        if apply:
            print(f"  {batch_id}: saved={batch_saved}, already_exists={batch_already_exists}, "
                  f"wrong_status={batch_wrong_status}")
        else:
            print(f"  {batch_id}: would save={batch_would_save}, already_exists={batch_already_exists}, "
                  f"wrong_status={batch_wrong_status}")

    print()
    if apply:
        print(f"Total saved to DB:    {total_saved}")
    else:
        print(f"[DRY RUN] Would save: {total_would_save}")
    print(f"Already in DB:        {total_already_exists}")
    print(f"Wrong pipeline status:{total_wrong_status}")
    if total_errors:
        print(f"Errors:               {total_errors}")


async def _recover_from_pending(client, apply: bool) -> None:
    """logs/pending_batches.jsonl을 읽어 각 batch_id를 확인/복구.

    성공적으로 복구된 batch는 jsonl에서 제거.
    """
    if not _PENDING_BATCHES_PATH.exists():
        print(f"No pending batches file found at: {_PENDING_BATCHES_PATH}")
        return

    lines = _PENDING_BATCHES_PATH.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            log.warning("pending_batch_invalid_line", line=line)
            continue
        batch_id = entry.get("batch_id")
        if not batch_id:
            log.warning("pending_batch_missing_batch_id", entry=entry)
            continue
        entries.append(entry)

    print(f"\nFound {len(entries)} pending batch(es) in {_PENDING_BATCHES_PATH}")

    total_saved = 0
    total_recovered = 0
    total_still_processing = 0
    total_errors = 0

    for entry in entries:
        batch_id = entry["batch_id"]
        summary = await _check_and_recover_batch(client, batch_id, apply=apply)
        _print_summary(summary, dry_run=not apply)

        if summary["status"] == "ended":
            total_recovered += 1
            if apply:
                total_saved += summary["saved"]
                # Remove from pending file after successful recovery
                _remove_pending_batch(batch_id)
        elif summary["status"] in ("in_progress", "canceling"):
            total_still_processing += 1
        else:
            total_errors += 1

    print(f"\n=== From-Pending Summary ===")
    print(f"  Total pending batches: {len(entries)}")
    print(f"  Ended (recovered):     {total_recovered}")
    print(f"  Still processing:      {total_still_processing}")
    print(f"  Errors:                {total_errors}")
    if apply:
        print(f"  Saved to DB:           {total_saved}")
    else:
        print(f"  (dry-run — use --apply to save to DB and clean up)")


async def main(args: argparse.Namespace) -> None:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    if args.list_pending:
        rows = await _list_pending_reports()
        print(f"\n{len(rows)} analysis_pending report(s):")
        for r in rows:
            print(f"  [{r['id']}] {r['report_date']} | {r['channel']} | {r['title']}")
        return

    if getattr(args, "from_pending", False):
        await _recover_from_pending(client, apply=args.apply)
        return

    if args.recover_all:
        await _recover_all_batches(client, apply=args.apply)
        return

    batch_ids = list(args.batch_ids or [])

    if args.batch_ids_file:
        try:
            with open(args.batch_ids_file) as f:
                batch_ids.extend(line.strip() for line in f if line.strip())
        except OSError as e:
            print(f"Cannot read batch IDs file: {e}", file=sys.stderr)
            sys.exit(1)

    if not batch_ids:
        return

    for batch_id in batch_ids:
        summary = await _check_and_recover_batch(client, batch_id, apply=args.apply)
        _print_summary(summary, dry_run=not args.apply)


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Anthropic Batch 복구 스크립트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--batch-ids", nargs="*", metavar="BATCH_ID",
        help="복구할 batch ID 목록",
    )
    parser.add_argument(
        "--batch-ids-file", metavar="FILE",
        help="batch ID 목록 파일 (줄당 하나)",
    )
    parser.add_argument(
        "--recover-all", action="store_true",
        help="Anthropic API의 모든 완료 batch를 스캔하여 복구",
    )
    parser.add_argument(
        "--list-pending", action="store_true",
        help="analysis_pending 상태인 리포트 목록 출력",
    )
    parser.add_argument(
        "--from-pending", action="store_true",
        help="logs/pending_batches.jsonl을 읽어 각 batch 결과 확인/복구",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="실제로 DB에 저장 (없으면 dry-run)",
    )
    args = parser.parse_args()

    if not any([args.batch_ids, args.batch_ids_file, args.recover_all, args.list_pending,
                args.from_pending]):
        parser.print_help()

    return args


if __name__ == "__main__":
    args = cli()
    asyncio.run(main(args))
