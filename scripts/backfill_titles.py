"""기존 리포트의 title을 Layer2 meta.title로 backfill하는 one-off 스크립트.

Layer2 분석이 완료된 리포트 중 analysis_data.meta.title이 존재하면
reports.title에 복사한다. title_normalized(dedup 키)는 건드리지 않는다.

Usage:
    python scripts/backfill_titles.py            # dry-run (변경 없이 미리보기)
    python scripts/backfill_titles.py --apply     # 실제 적용
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update as sa_update

from db.models import Report, ReportAnalysis
from db.session import AsyncSessionLocal


CHUNK = 500


async def backfill_titles(apply: bool = False):
    updates = []
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Report.id, Report.title, ReportAnalysis.analysis_data)
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        )
        async for row in await session.stream(stmt):
            report_id, current_title, analysis_data = row._tuple()
            meta_title = (analysis_data or {}).get("meta", {}).get("title", "")
            if not meta_title:
                continue
            meta_title = meta_title.strip()
            if not meta_title or meta_title == current_title:
                continue
            updates.append((report_id, current_title, meta_title))

    if not updates:
        print("업데이트 대상이 없습니다.")
        return

    print(f"총 {len(updates)}건 타이틀 업데이트 대상:\n")
    for report_id, old, new in updates[:20]:
        old_short = (old or "")[:60]
        new_short = new[:60]
        print(f"  [{report_id}] {old_short!r}")
        print(f"        → {new_short!r}\n")
    if len(updates) > 20:
        print(f"  ... 외 {len(updates) - 20}건\n")

    if not apply:
        print("dry-run 모드입니다. --apply 로 실행하세요.")
        return

    done = 0
    for i in range(0, len(updates), CHUNK):
        chunk = updates[i : i + CHUNK]
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_update(Report),
                [{"id": rid, "title": new_t} for rid, _, new_t in chunk],
            )
            await session.commit()
        done += len(chunk)
        print(f"  {done}/{len(updates)} 완료")
    print(f"\n{len(updates)}건 타이틀 업데이트 완료.")


def main():
    parser = argparse.ArgumentParser(description="Layer2 meta.title → reports.title backfill")
    parser.add_argument("--apply", action="store_true", help="실제 적용 (없으면 dry-run)")
    args = parser.parse_args()
    asyncio.run(backfill_titles(apply=args.apply))


if __name__ == "__main__":
    main()
