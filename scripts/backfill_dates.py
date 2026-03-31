"""기존 리포트의 report_date를 key_data 추출 날짜로 보정하는 one-off 스크립트.

분석 완료된 리포트 중 report_date가 수집일(2026-03)로 잘못 들어간 건을
PDF에서 key_data.date를 재추출하여 보정.

Usage:
    python scripts/backfill_dates.py            # dry-run
    python scripts/backfill_dates.py --apply     # 실제 적용
    python scripts/backfill_dates.py --all       # 2026-03뿐 아니라 전체 대상
"""
import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update as sa_update

from config.settings import settings
from db.models import Report, ReportAnalysis
from db.session import AsyncSessionLocal
from parser.key_data_extractor import extract_key_data

CHUNK = 500
_CACHE_FILE = "backfill_dates_cache.json"
# 수집일로 잘못 들어간 날짜 범위 (backfill 실행 시기)
SUSPECT_START = date(2026, 3, 1)
SUSPECT_END = date(2026, 3, 31)


def _load_cache() -> list[tuple] | None:
    """캐시 파일에서 스캔 결과 로드."""
    import json
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [(r["id"], r["old"], r["new"], r["title"]) for r in data]
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cache(updates: list[tuple]):
    """스캔 결과를 캐시 파일에 저장."""
    import json
    data = [{"id": rid, "old": str(old), "new": str(new), "title": t} for rid, old, new, t in updates]
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  캐시 저장: {_CACHE_FILE} ({len(data)}건)")


async def backfill_dates(apply: bool = False, scan_all: bool = False):
    # apply 모드에서 캐시가 있으면 스캔 skip
    if apply:
        cached = _load_cache()
        if cached:
            print(f"캐시 로드: {_CACHE_FILE} ({len(cached)}건) — Gemini 스캔 생략\n")
            updates = [(rid, old, date.fromisoformat(new) if isinstance(new, str) else new, t) for rid, old, new, t in cached]
            done = 0
            skipped_dedup = 0
            for report_id, _, new_date, title in updates:
                try:
                    async with AsyncSessionLocal() as session:
                        await session.execute(
                            sa_update(Report).where(Report.id == report_id).values(report_date=new_date)
                        )
                        await session.commit()
                    done += 1
                except Exception:
                    skipped_dedup += 1
                if (done + skipped_dedup) % 500 == 0:
                    print(f"  {done + skipped_dedup}/{len(updates)} 처리 (성공 {done}, dedup skip {skipped_dedup})")
            print(f"\n완료: {done}건 보정, {skipped_dedup}건 dedup skip")
            return

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Report.id, Report.report_date, Report.pdf_path, Report.title)
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(Report.pdf_path.isnot(None))
        )
        if not scan_all:
            stmt = stmt.where(
                Report.report_date >= SUSPECT_START,
                Report.report_date <= SUSPECT_END,
            )
        rows = (await session.execute(stmt)).all()

    total = len(rows)
    print(f"스캔 대상: {total}건 ({'전체' if scan_all else f'{SUSPECT_START}~{SUSPECT_END}'})\n")

    updates = []
    skipped = 0
    no_date = 0
    same_date = 0
    no_pdf = 0

    for i, (report_id, current_date, pdf_path, title) in enumerate(rows):
        if (i + 1) % 100 == 0:
            print(f"  스캔 중... {i+1}/{total} (보정 대상: {len(updates)})")

        abs_path = settings.pdf_base_path / pdf_path
        if not abs_path.exists():
            no_pdf += 1
            continue

        key_data = await extract_key_data(abs_path, report_id=report_id)
        if not key_data or not key_data.date:
            no_date += 1
            continue

        try:
            new_date = date.fromisoformat(key_data.date)
        except (ValueError, TypeError):
            no_date += 1
            continue

        if new_date == current_date:
            same_date += 1
            continue

        updates.append((report_id, current_date, new_date, (title or "")[:60]))

    print(f"\n=== 스캔 완료 ===")
    print(f"  총 스캔: {total}")
    print(f"  PDF 없음: {no_pdf}")
    print(f"  날짜 추출 실패: {no_date}")
    print(f"  날짜 동일: {same_date}")
    print(f"  보정 대상: {len(updates)}")

    if not updates:
        print("\n업데이트 대상이 없습니다.")
        return

    print(f"\n=== 보정 대상 샘플 ===")
    for report_id, old_date, new_date, title in updates[:30]:
        print(f"  [{report_id}] {old_date} -> {new_date}  {title}")
    if len(updates) > 30:
        print(f"  ... 외 {len(updates) - 30}건")

    # 스캔 결과 캐시 저장 (apply 시 재사용)
    _save_cache(updates)

    if not apply:
        print("\ndry-run 모드입니다. --apply 로 실행하세요.")
        return

    done = 0
    skipped_dedup = 0
    for report_id, _, new_date, title in updates:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    sa_update(Report).where(Report.id == report_id).values(report_date=new_date)
                )
                await session.commit()
            done += 1
        except Exception:
            skipped_dedup += 1
        if (done + skipped_dedup) % 500 == 0:
            print(f"  {done + skipped_dedup}/{len(updates)} 처리 (성공 {done}, dedup skip {skipped_dedup})")
    print(f"\n완료: {done}건 보정, {skipped_dedup}건 dedup skip")


def main():
    parser = argparse.ArgumentParser(description="report_date 보정 (key_data.date 기반)")
    parser.add_argument("--apply", action="store_true", help="실제 적용 (없으면 dry-run)")
    parser.add_argument("--all", action="store_true", dest="scan_all", help="전체 스캔 (기본: 2026-03만)")
    args = parser.parse_args()
    asyncio.run(backfill_dates(apply=args.apply, scan_all=args.scan_all))


if __name__ == "__main__":
    main()
