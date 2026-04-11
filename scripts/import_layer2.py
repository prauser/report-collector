"""Layer2 출력 JSONL을 읽어서 DB에 저장하는 import 스크립트.

scripts/claude_layer2.py 등의 출력 JSONL을 처리하여 분석 결과를 DB에 저장한다.
기본 동작은 dry-run (파싱+검증만, DB 변경 없음).
실제 저장은 --apply 플래그로 실행.

사용법:
  python scripts/import_layer2.py --input data/layer2_outputs.jsonl     # dry-run
  python scripts/import_layer2.py --input data/layer2_outputs.jsonl --apply
  python scripts/import_layer2.py --apply --batch-size 100
"""
import os
import sys
import argparse
import asyncio
import json

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# repo root를 path에 추가 (scripts/ 에서 실행 시)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import structlog
from sqlalchemy import select, update as sa_update
from sqlalchemy.exc import IntegrityError

from db.session import AsyncSessionLocal
from db.models import Report as ReportModel
from storage.analysis_repo import save_analysis
from parser.layer2_extractor import make_layer2_result
from parser.meta_updater import apply_layer2_meta

log = structlog.get_logger(__name__)


def _parse_jsonl(input_path: str) -> list[dict]:
    """JSONL 파일을 읽어 파싱된 레코드 목록 반환.

    각 줄은 JSON 객체여야 한다.
    파싱 실패한 줄은 건너뛴다.
    """
    records = []
    with open(input_path, "r", encoding="utf-8") as fp:
        for lineno, line in enumerate(fp, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  [WARN] Line {lineno}: JSON parse error — {e}")
    return records


async def _is_already_done(session, report_id: int) -> bool:
    """pipeline_status='done'인지 확인."""
    result = await session.execute(
        select(ReportModel.pipeline_status).where(ReportModel.id == report_id)
    )
    row = result.scalar()
    return row == "done"


async def _process_record(
    session,
    report_id: int,
    result_dict: dict,
    apply: bool,
) -> str:
    """단일 레코드를 처리. 결과: 'imported' / 'skipped' / 'failed'."""
    # 이미 done인 건 skip
    if await _is_already_done(session, report_id):
        return "skipped"

    # Layer2Result 생성 (validation 포함)
    layer2 = make_layer2_result(
        tool_input=result_dict,
        input_tokens=0,
        output_tokens=0,
        is_batch=False,
        report_id=report_id,
    )
    if layer2 is None:
        log.warning("import_layer2_validation_failed", report_id=report_id)
        return "failed"

    if not apply:
        # dry-run: 파싱+검증만
        return "imported"

    # 실제 DB 저장
    report = await session.get(ReportModel, report_id)
    if not report:
        log.warning("import_layer2_report_not_found", report_id=report_id)
        return "failed"

    # meta 업데이트 (reports 테이블)
    meta_updates = apply_layer2_meta(report, layer2.meta)
    if meta_updates:
        try:
            async with session.begin_nested():
                await session.execute(
                    sa_update(ReportModel)
                    .where(ReportModel.id == report_id)
                    .values(**meta_updates)
                )
        except IntegrityError:
            log.debug("import_layer2_meta_update_skipped", report_id=report_id)

    # 분석 결과 저장
    await save_analysis(session, report_id, layer2)
    return "imported"


async def main(args: argparse.Namespace) -> None:
    apply = args.apply
    batch_size = args.batch_size
    input_path = args.input

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== import_layer2 ({mode}) ===")
    print(f"Input: {input_path}")
    print(f"Batch size: {batch_size}")
    print()

    # JSONL 파일 파싱
    print(f"Reading {input_path}...")
    try:
        records = _parse_jsonl(input_path)
    except FileNotFoundError:
        print(f"ERROR: File not found: {input_path}")
        return
    except OSError as e:
        print(f"ERROR: Cannot read file: {e}")
        return

    print(f"Loaded {len(records)} lines.")
    print()

    # status="success"이고 result가 null이 아닌 건만 처리
    candidates = [
        r for r in records
        if r.get("status") == "success" and r.get("result") is not None
    ]
    total = len(candidates)
    skipped_status = len(records) - total

    print(f"Eligible records (status=success, result not null): {total}")
    if skipped_status:
        print(f"Skipped (status!=success or result=null): {skipped_status}")
    print()

    if total == 0:
        print("No eligible records to process.")
        return

    # 처리
    n_imported = 0
    n_skipped = 0
    n_failed = 0
    batch_count = 0

    async with AsyncSessionLocal() as session:
        for i, record in enumerate(candidates, 1):
            report_id = record.get("report_id")
            result_dict = record.get("result")

            if report_id is None:
                print(f"  [WARN] Record {i}: missing report_id, skipping")
                n_failed += 1
                continue

            try:
                outcome = await _process_record(session, report_id, result_dict, apply)
            except Exception as e:
                log.warning("import_layer2_record_error", report_id=report_id, error=str(e))
                n_failed += 1
                continue

            if outcome == "imported":
                n_imported += 1
                batch_count += 1
            elif outcome == "skipped":
                n_skipped += 1
            else:
                n_failed += 1

            # batch_size마다 commit + 진행 표시
            if apply and batch_count > 0 and batch_count % batch_size == 0:
                await session.commit()
                print(f"[{i}/{total}] Imported {n_imported} reports ({n_skipped} skipped)")

            elif not apply and i % batch_size == 0:
                print(f"[{i}/{total}] Validated {n_imported} reports ({n_skipped} skipped)")

        # 마지막 배치 commit
        if apply and batch_count % batch_size != 0 and batch_count > 0:
            await session.commit()

    # 최종 summary
    print()
    if apply:
        print(f"=== Done ===")
        print(f"  Imported: {n_imported}")
        print(f"  Skipped (already done): {n_skipped}")
        print(f"  Failed: {n_failed}")
    else:
        print(f"=== Dry-run complete ===")
        print(f"  Would import: {n_imported}")
        print(f"  Would skip (already done): {n_skipped}")
        print(f"  Would fail: {n_failed}")
        print()
        print("Run with --apply to write to DB.")


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Layer2 출력 JSONL을 DB에 저장 (기본: dry-run)"
    )
    parser.add_argument(
        "--input",
        default="data/layer2_outputs.jsonl",
        help="입력 JSONL 경로 (기본: data/layer2_outputs.jsonl)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="실제 DB 저장 수행 (기본: dry-run)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="커밋 단위 (기본: 50)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    asyncio.run(main(args))
