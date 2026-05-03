"""Layer2 분석 결과의 정량적 근거 깊이 확인.

valuation_impact만 있는 게 아니라 영업이익 추정/그 근거 수치 / 매출 / 마진 등이
체인에 함께 잡혀있는지 실제 샘플로 확인.

읽기 전용.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from db.session import AsyncSessionLocal


async def fetch_sample(limit: int, report_type: str | None = None) -> list[dict]:
    where = "AND r.report_type = :rt" if report_type else ""
    sql = text(
        f"""
        SELECT
            r.id AS report_id,
            r.report_type,
            r.source_channel,
            r.stock_name,
            a.analysis_data
        FROM reports r
        JOIN report_analysis a ON a.report_id = r.id
        WHERE r.pipeline_status = 'done'
          AND a.analysis_data ? 'chain'
          AND r.report_type IN ('기업분석', '실적리뷰', '산업분석')
          {where}
        ORDER BY RANDOM()
        LIMIT :limit
        """
    )
    params = {"limit": limit}
    if report_type:
        params["rt"] = report_type
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, params)
        return [dict(r) for r in result.mappings().all()]


def show_one(row: dict) -> None:
    a = row["analysis_data"]
    if isinstance(a, str):
        a = json.loads(a)

    print("=" * 80)
    print(f"id={row['report_id']} | type={row['report_type']} | stock={row['stock_name']} | ch={row['source_channel']}")
    print("=" * 80)

    # Opinion
    op = a.get("opinion") or {}
    if op:
        print(f"[opinion] rating={op.get('rating')} TP={op.get('target_price')} prev_TP={op.get('prev_target_price')}")
        if op.get("change_reason"):
            print(f"  change_reason: {op['change_reason']}")

    # Thesis
    th = a.get("thesis") or {}
    if th.get("summary"):
        print(f"[thesis] {th.get('summary')}")
    if th.get("sentiment") is not None:
        print(f"  sentiment={th.get('sentiment')}")

    # Financials
    fin = a.get("financials") or {}
    if fin:
        print(f"[financials]")
        for k, v in fin.items():
            if isinstance(v, (str, int, float)):
                print(f"  {k}: {v}")
            elif isinstance(v, dict):
                print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")

    # Chain (모든 step 보여주기, 순서 유지)
    chain = a.get("chain") or []
    if chain:
        print(f"[chain] ({len(chain)} steps)")
        for i, step in enumerate(chain):
            stype = step.get("step", "?")
            text_ = step.get("text", "")
            dir_ = step.get("direction", "?")
            conf = step.get("confidence", "?")
            print(f"  {i+1}. ({stype}|{dir_}|{conf}) {text_}")

    print()


def aggregate_financials_completeness(rows: list[dict]) -> dict:
    """financials 필드 완성도 측정."""
    fields = ["revenue", "operating_profit", "eps", "earnings_quarter", "key_metrics"]
    field_counts = Counter()
    field_with_value = Counter()
    sub_metric_counts = Counter()  # key_metrics 내부 분포
    chain_step_counts = Counter()

    for row in rows:
        a = row["analysis_data"]
        if isinstance(a, str):
            a = json.loads(a)
        fin = a.get("financials") or {}
        for f in fields:
            v = fin.get(f)
            if v is not None and v != "" and v != {}:
                field_with_value[f] += 1
            field_counts[f] += 1
        # key_metrics breakdown
        km = fin.get("key_metrics") or {}
        if isinstance(km, dict):
            for k in km.keys():
                sub_metric_counts[k] += 1
        # chain step types
        for step in a.get("chain") or []:
            chain_step_counts[step.get("step", "?")] += 1

    return {
        "n": len(rows),
        "field_fill_rate": {f: round(field_with_value[f] / field_counts[f] * 100, 1) for f in fields},
        "key_metrics_top": sub_metric_counts.most_common(15),
        "chain_step_counts": chain_step_counts.most_common(),
    }


def main_sync(args):
    rows = asyncio.run(fetch_sample(args.limit, args.report_type))
    if not rows:
        print("샘플 0건")
        return

    if args.summary_only:
        summary = aggregate_financials_completeness(rows)
        print(f"\n=== {summary['n']}건 ===\n")
        print("[financials 필드 채움률]")
        for f, pct in summary["field_fill_rate"].items():
            print(f"  {f:20s}: {pct}%")
        print("\n[key_metrics 내부 키 빈도 TOP 15]")
        for k, c in summary["key_metrics_top"]:
            print(f"  {k:30s}: {c}")
        print("\n[chain step 타입 분포]")
        for s, c in summary["chain_step_counts"]:
            print(f"  {s:25s}: {c}")
    else:
        for row in rows[: args.show]:
            show_one(row)
        if len(rows) > args.show:
            print(f"... ({len(rows) - args.show}건 생략)")
        # 마지막에 요약도
        summary = aggregate_financials_completeness(rows)
        print(f"\n=== {summary['n']}건 financials 필드 채움률 ===")
        for f, pct in summary["field_fill_rate"].items():
            print(f"  {f:20s}: {pct}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--show", type=int, default=5, help="실제 출력 건수")
    parser.add_argument("--report-type", type=str, default=None, help="필터: 기업분석/실적리뷰/산업분석")
    parser.add_argument("--summary-only", action="store_true", help="필드 채움률만")
    args = parser.parse_args()
    main_sync(args)
