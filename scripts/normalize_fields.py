"""기존 reports 테이블의 broker/opinion 필드를 정규화하는 스크립트.

기본 동작은 dry-run (변경 대상만 출력).
실제 업데이트는 --apply 플래그로 실행.

사용법:
  python scripts/normalize_fields.py              # dry-run (기본)
  python scripts/normalize_fields.py --dry-run    # 명시적 dry-run
  python scripts/normalize_fields.py --apply      # 실제 업데이트
  python scripts/normalize_fields.py --apply --batch-size 500
"""
import os
import sys
import argparse
import asyncio

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# repo root를 path에 추가 (scripts/ 에서 실행 시)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sqlalchemy import select, update as sa_update

from db.session import AsyncSessionLocal
from db.models import Report as ReportModel
from parser.normalizer import normalize_broker, normalize_opinion


async def _collect_changes(batch_size: int) -> list[dict]:
    """DB에서 broker/opinion 읽어 정규화 후 변경이 필요한 행 수집."""
    changes = []
    offset = 0

    while True:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ReportModel.id, ReportModel.broker, ReportModel.opinion)
                .offset(offset)
                .limit(batch_size)
            )
            rows = result.all()

        if not rows:
            break

        for report_id, broker, opinion in rows:
            updates = {}

            if broker:
                normalized = normalize_broker(broker)
                if normalized != broker:
                    updates["broker"] = normalized

            if opinion:
                normalized = normalize_opinion(opinion)
                if normalized != opinion:
                    updates["opinion"] = normalized

            if updates:
                changes.append({
                    "id": report_id,
                    "updates": updates,
                    "before": {
                        "broker": broker,
                        "opinion": opinion,
                    },
                })

        offset += batch_size

    return changes


async def _apply_changes(changes: list[dict]) -> int:
    """변경 사항을 DB에 적용. 업데이트된 행 수 반환."""
    n_updated = 0
    for change in changes:
        report_id = change["id"]
        updates = change["updates"]
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_update(ReportModel)
                .where(ReportModel.id == report_id)
                .values(**updates)
            )
            await session.commit()
        n_updated += 1
    return n_updated


async def main(args: argparse.Namespace) -> None:
    apply = args.apply
    batch_size = args.batch_size

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== normalize_fields ({mode}) ===")
    print(f"batch_size: {batch_size}")
    print()

    print("Scanning reports table for broker/opinion normalization changes...")
    changes = await _collect_changes(batch_size)

    if not changes:
        print("No changes needed. All broker/opinion values are already normalized.")
        return

    # 변경 요약 출력
    broker_changes = [c for c in changes if "broker" in c["updates"]]
    opinion_changes = [c for c in changes if "opinion" in c["updates"]]

    print(f"Found {len(changes)} rows needing normalization:")
    print(f"  broker changes:  {len(broker_changes)}")
    print(f"  opinion changes: {len(opinion_changes)}")
    print()

    # 샘플 출력 (최대 20건)
    sample = changes[:20]
    print(f"Sample changes (up to 20):")
    for c in sample:
        before = c["before"]
        updates = c["updates"]
        parts = []
        if "broker" in updates:
            parts.append(f"broker: {before['broker']!r} → {updates['broker']!r}")
        if "opinion" in updates:
            parts.append(f"opinion: {before['opinion']!r} → {updates['opinion']!r}")
        print(f"  [{c['id']}] " + " | ".join(parts))

    if len(changes) > 20:
        print(f"  ... and {len(changes) - 20} more")

    print()

    if not apply:
        print(f"총 {len(changes)}건 변경 대상 (--apply 로 실제 업데이트)")
        return

    print(f"Applying {len(changes)} updates...")
    n_updated = await _apply_changes(changes)
    print(f"Done. {n_updated}건 업데이트 완료.")


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="reports 테이블의 broker/opinion 필드 정규화 (기본: dry-run)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="실제 DB 업데이트 수행 (기본: dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="dry-run 명시 (기본 동작, --apply 없으면 항상 dry-run)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="DB 조회 배치 크기 (기본값: 1000)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    asyncio.run(main(args))
