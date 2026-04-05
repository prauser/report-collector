"""기존 리포트의 report_date를 정규식 기반으로 보정하는 스크립트.

LLM 호출 없이 PDF 텍스트에서 정규식으로 날짜 추출 → DB 업데이트.
MISS(추출 실패), DIFF(큰 차이)는 CSV에 기록만 하고 수정하지 않음.

Usage:
    python scripts/fix_dates_regex.py                  # dry-run (전체 스캔)
    python scripts/fix_dates_regex.py --apply           # 실제 적용
    python scripts/fix_dates_regex.py --limit 1000      # 건수 제한
"""
import argparse
import asyncio
import csv
import os
import signal
import sys
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeout
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update as sa_update

from config.settings import settings
from db.models import Report
from db.session import AsyncSessionLocal
from parser.key_data_extractor import _get_first_pages_text_sync, _extract_date_regex

# PDF 1건당 타임아웃 (초)
PDF_TIMEOUT = 10
# 현재 DB report_date와 regex 추출 날짜 차이가 이 이상이면 DIFF로 분류
DIFF_THRESHOLD_DAYS = 90

CSV_DIR = Path("scripts/output")
CSV_UPDATED = CSV_DIR / "fix_dates_updated.csv"
CSV_MISS = CSV_DIR / "fix_dates_miss.csv"
CSV_DIFF = CSV_DIR / "fix_dates_diff.csv"


def _extract_one(pdf_path_str: str) -> str | None:
    """프로세스 풀에서 실행 — PDF에서 텍스트 추출 후 정규식 날짜 반환."""
    text = _get_first_pages_text_sync(pdf_path_str)
    if not text:
        return None
    d = _extract_date_regex(text)
    return d.isoformat() if d else ""


async def scan_and_fix(apply: bool = False, limit: int | None = None):
    CSV_DIR.mkdir(parents=True, exist_ok=True)

    # DB에서 PDF가 있는 리포트 전체 조회
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Report.id, Report.report_date, Report.pdf_path, Report.title, Report.broker)
            .where(Report.pdf_path.isnot(None))
            .order_by(Report.id)
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).all()

    total = len(rows)
    print(f"스캔 대상: {total}건\n", flush=True)

    updated_rows = []
    miss_rows = []
    diff_rows = []
    same = 0
    no_pdf = 0
    timeout_count = 0
    loop = asyncio.get_event_loop()

    for i, (report_id, current_date, pdf_path, title, broker) in enumerate(rows):
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{total} 스캔 중... (update={len(updated_rows)} miss={len(miss_rows)} diff={len(diff_rows)} same={same} timeout={timeout_count})", flush=True)

        abs_path = settings.pdf_base_path / pdf_path
        if not abs_path.exists():
            no_pdf += 1
            continue

        try:
            result_str = await asyncio.wait_for(
                loop.run_in_executor(None, _extract_one, str(abs_path)),
                timeout=PDF_TIMEOUT,
            )
        except (asyncio.TimeoutError, Exception):
            timeout_count += 1
            miss_rows.append((report_id, str(current_date), "", title or "", broker or "", "timeout"))
            continue

        if result_str is None:
            miss_rows.append((report_id, str(current_date), "", title or "", broker or "", "no_text"))
            continue

        if result_str == "":
            miss_rows.append((report_id, str(current_date), "", title or "", broker or "", "no_match"))
            continue

        regex_date = date.fromisoformat(result_str)

        if not regex_date:
            miss_rows.append((report_id, str(current_date), "", title or "", broker or "", "no_match"))
            continue

        if regex_date == current_date:
            same += 1
            continue

        diff_days = abs((regex_date - current_date).days)

        if diff_days > DIFF_THRESHOLD_DAYS:
            diff_rows.append((
                report_id, str(current_date), str(regex_date),
                diff_days, title or "", broker or "",
            ))
            continue

        # 정상 보정 대상
        updated_rows.append((report_id, current_date, regex_date, title or "", broker or ""))

    # === 결과 출력 ===
    print(f"\n{'='*60}")
    print(f"총 스캔: {total}")
    print(f"PDF 없음: {no_pdf}")
    print(f"날짜 동일: {same}")
    print(f"MISS (추출 실패): {len(miss_rows)}")
    print(f"DIFF (>{DIFF_THRESHOLD_DAYS}일 차이): {len(diff_rows)}")
    print(f"보정 대상: {len(updated_rows)}")

    # === CSV 저장 ===
    with open(CSV_UPDATED, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["report_id", "old_date", "new_date", "diff_days", "title", "broker"])
        for rid, old, new, title, broker in updated_rows:
            w.writerow([rid, old, new, abs((new - old).days), title[:80], broker])
    print(f"\n  보정 대상 → {CSV_UPDATED} ({len(updated_rows)}건)")

    with open(CSV_MISS, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["report_id", "current_date", "regex_date", "title", "broker", "reason"])
        for row in miss_rows:
            w.writerow(row)
    print(f"  MISS → {CSV_MISS} ({len(miss_rows)}건)")

    with open(CSV_DIFF, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["report_id", "current_date", "regex_date", "diff_days", "title", "broker"])
        for row in diff_rows:
            w.writerow(row)
    print(f"  DIFF → {CSV_DIFF} ({len(diff_rows)}건)")

    # === 샘플 출력 ===
    if updated_rows:
        print(f"\n=== 보정 대상 샘플 (최대 20건) ===")
        for rid, old, new, title, broker in updated_rows[:20]:
            print(f"  [{rid}] {old} → {new} ({abs((new-old).days)}d)  {broker} {title[:50]}")
        if len(updated_rows) > 20:
            print(f"  ... 외 {len(updated_rows) - 20}건")

    if diff_rows:
        print(f"\n=== DIFF 샘플 (최대 10건) ===")
        for rid, old, new, days, title, broker in diff_rows[:10]:
            print(f"  [{rid}] {old} → {new} ({days}d)  {broker} {title[:50]}")

    # === 적용 ===
    if not apply:
        print(f"\ndry-run 모드입니다. --apply 로 실행하면 {len(updated_rows)}건 업데이트됩니다.")
        return

    if not updated_rows:
        print("\n업데이트 대상이 없습니다.")
        return

    # 배치 업데이트 (1세션에서 여러 건 처리)
    BATCH = 500
    done = 0
    errors = 0
    for i in range(0, len(updated_rows), BATCH):
        batch = updated_rows[i:i + BATCH]
        try:
            async with AsyncSessionLocal() as session:
                for rid, _, new_date, _, _ in batch:
                    await session.execute(
                        sa_update(Report).where(Report.id == rid).values(report_date=new_date)
                    )
                await session.commit()
            done += len(batch)
        except Exception as e:
            errors += len(batch)
            print(f"  배치 에러 ({i}~): {e}")
        print(f"  {done + errors}/{len(updated_rows)} 처리 (성공 {done}, 에러 {errors})", flush=True)

    print(f"\n완료: {done}건 보정, {errors}건 에러")


def main():
    parser = argparse.ArgumentParser(description="report_date 정규식 기반 보정")
    parser.add_argument("--apply", action="store_true", help="실제 DB 적용 (없으면 dry-run)")
    parser.add_argument("--limit", type=int, default=None, help="스캔 건수 제한")
    args = parser.parse_args()
    asyncio.run(scan_and_fix(apply=args.apply, limit=args.limit))


if __name__ == "__main__":
    main()
