"""MISS/DIFF 리포트의 report_date를 텔레그램 메시지 날짜 기반으로 보정.

fix_dates_regex.py에서 MISS(정규식 추출 실패) / DIFF(>90일 차이) 건에 대해
텔레그램에서 실제 메시지 전송일을 조회하여 보정.

- MISS: report_date = 메시지 전송일
- DIFF: |regex - 메시지 전송일| ≤ 3일이면 regex 채택, 아니면 메시지 전송일

Usage:
    python scripts/fix_dates_telegram.py                # dry-run
    python scripts/fix_dates_telegram.py --apply        # 실제 적용
"""
import argparse
import asyncio
import csv
import os
import sys
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
from collector.telegram_client import get_client

CSV_DIR = Path("scripts/output")
CSV_MISS = CSV_DIR / "fix_dates_miss.csv"
CSV_DIFF = CSV_DIR / "fix_dates_diff.csv"
CSV_RESULT = CSV_DIR / "fix_dates_telegram_result.csv"

# regex 결과가 메시지 날짜와 이 이내면 regex 채택 (DIFF 건)
REGEX_ACCEPT_DAYS = 3
# 텔레그램 배치 조회 크기
BATCH_SIZE = 100


async def _fetch_message_dates(
    client, channel: str, message_ids: list[int],
) -> dict[int, date]:
    """텔레그램에서 메시지 ID 목록의 전송일 조회. {message_id: date} 반환."""
    result = {}
    for i in range(0, len(message_ids), BATCH_SIZE):
        batch = message_ids[i:i + BATCH_SIZE]
        try:
            messages = await client.get_messages(channel, ids=batch)
            for msg in messages:
                if msg and msg.date:
                    result[msg.id] = msg.date.date()
        except Exception as e:
            print(f"  경고: {channel} 메시지 조회 실패 (batch {i}~): {e}")
    return result


async def run(apply: bool = False):
    CSV_DIR.mkdir(parents=True, exist_ok=True)

    # 1) MISS + DIFF CSV에서 report_id 수집
    miss_ids = set()
    diff_map: dict[int, str] = {}  # report_id → regex_date (from CSV)

    if CSV_MISS.exists():
        with open(CSV_MISS, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                miss_ids.add(int(r["report_id"]))

    if CSV_DIFF.exists():
        with open(CSV_DIFF, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rid = int(r["report_id"])
                diff_map[rid] = r["regex_date"]

    all_ids = miss_ids | set(diff_map.keys())
    print(f"대상: MISS {len(miss_ids)}건 + DIFF {len(diff_map)}건 = {len(all_ids)}건\n")

    if not all_ids:
        print("처리 대상이 없습니다.")
        return

    # 2) DB에서 해당 리포트의 source_channel, source_message_id 조회
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Report.id, Report.report_date, Report.source_channel,
                   Report.source_message_id, Report.title)
            .where(Report.id.in_(all_ids))
            .where(Report.source_message_id.isnot(None))
        )).all()

    print(f"DB 조회: {len(rows)}건 (source_message_id 있는 건)\n")

    # 채널별 그룹핑
    channel_groups: dict[str, list[tuple]] = {}
    for rid, rdate, channel, msg_id, title in rows:
        channel_groups.setdefault(channel, []).append((rid, rdate, msg_id, title))

    print(f"채널 수: {len(channel_groups)}")
    for ch, items in sorted(channel_groups.items(), key=lambda x: -len(x[1])):
        print(f"  {ch:<30} {len(items):>5}건")
    print()

    # 3) 텔레그램 연결 & 메시지 날짜 조회
    client = get_client()
    await client.start()
    print("텔레그램 연결 완료\n")

    updates = []  # (report_id, old_date, new_date, source, title)
    no_change = 0
    not_found = 0

    try:
        for channel, items in channel_groups.items():
            msg_ids = [msg_id for _, _, msg_id, _ in items]
            print(f"  {channel} — {len(msg_ids)}건 조회 중...", end="", flush=True)

            msg_dates = await _fetch_message_dates(client, channel, msg_ids)
            print(f" 완료 ({len(msg_dates)}건 응답)")

            for rid, current_date, msg_id, title in items:
                msg_date = msg_dates.get(msg_id)
                if not msg_date:
                    not_found += 1
                    continue

                if rid in diff_map and diff_map[rid]:
                    # DIFF 건: regex vs 메시지 날짜 비교
                    try:
                        regex_date = date.fromisoformat(diff_map[rid])
                    except (ValueError, TypeError):
                        regex_date = None

                    if regex_date and abs((regex_date - msg_date).days) <= REGEX_ACCEPT_DAYS:
                        new_date = regex_date
                        source = "regex"
                    else:
                        new_date = msg_date
                        source = "msg"
                else:
                    # MISS 건: 메시지 날짜 사용
                    new_date = msg_date
                    source = "msg"

                if new_date == current_date:
                    no_change += 1
                    continue

                updates.append((rid, current_date, new_date, source, (title or "")[:60]))

    finally:
        await client.disconnect()

    # 4) 결과 출력
    print(f"\n{'='*60}")
    print(f"메시지 못 찾음: {not_found}")
    print(f"변경 없음: {no_change}")
    print(f"보정 대상: {len(updates)}")

    # CSV 저장
    with open(CSV_RESULT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["report_id", "old_date", "new_date", "diff_days", "source", "title"])
        for rid, old, new, src, title in updates:
            w.writerow([rid, old, new, abs((new - old).days), src, title])
    print(f"\n  결과 → {CSV_RESULT} ({len(updates)}건)")

    if updates:
        print(f"\n=== 보정 대상 샘플 (최대 20건) ===")
        for rid, old, new, src, title in updates[:20]:
            print(f"  [{rid}] {old} → {new} ({abs((new-old).days)}d) [{src}]  {title}")
        if len(updates) > 20:
            print(f"  ... 외 {len(updates) - 20}건")

    # 5) 적용
    if not apply:
        print(f"\ndry-run 모드입니다. --apply 로 실행하면 {len(updates)}건 업데이트됩니다.")
        return

    if not updates:
        print("\n업데이트 대상이 없습니다.")
        return

    # 배치 업데이트 (1세션에서 여러 건 처리)
    BATCH = 500
    done = 0
    errors = 0
    for i in range(0, len(updates), BATCH):
        batch = updates[i:i + BATCH]
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
        print(f"  {done + errors}/{len(updates)} 처리 (성공 {done}, 에러 {errors})", flush=True)

    print(f"\n완료: {done}건 보정, {errors}건 에러")


def main():
    parser = argparse.ArgumentParser(description="MISS/DIFF report_date 텔레그램 기반 보정")
    parser.add_argument("--apply", action="store_true", help="실제 DB 적용 (없으면 dry-run)")
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
