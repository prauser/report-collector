"""Layer2 valuation/financial chain step의 수치 grounding 검증.

chart_digitize 제거 전 확인용:
- chain step 중 valuation_impact / financial_impact / pricing_impact만 추출
- 각 step의 text에 숫자가 있는지, 그 숫자가 markdown에 있는지
- 샘플 출력으로 실제 품질 눈으로 확인

읽기 전용. 비용 0.
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


VALUATION_RELEVANT_STEPS = {
    "valuation_impact",
    "financial_impact",
    "pricing_impact",
    "demand_transmission",
    "supply_dynamics",
}

NUM_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b\d+\.\d+|\b\d{2,}\b")


def extract_numbers(text_blob: str) -> set[str]:
    if not text_blob:
        return set()
    found = set()
    for m in NUM_RE.findall(text_blob):
        norm = m.replace(",", "")
        if len(norm.replace(".", "")) >= 2:
            found.add(norm)
    return found


async def fetch_sample(limit: int) -> list[dict]:
    sql = text(
        """
        SELECT
            r.id AS report_id,
            r.report_type,
            r.source_channel,
            r.stock_name,
            a.analysis_data,
            m.markdown_text
        FROM reports r
        JOIN report_analysis a ON a.report_id = r.id
        JOIN report_markdown m ON m.report_id = r.id
        WHERE r.pipeline_status = 'done'
          AND a.analysis_data ? 'chain'
        ORDER BY RANDOM()
        LIMIT :limit
        """
    )
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {"limit": limit})
        return [dict(r) for r in result.mappings().all()]


def analyze_one(row: dict) -> dict:
    analysis_data = row["analysis_data"]
    if isinstance(analysis_data, str):
        analysis_data = json.loads(analysis_data)

    md_text = row["markdown_text"] or ""
    md_nums = extract_numbers(md_text)

    relevant_steps = []
    for step in analysis_data.get("chain") or []:
        if step.get("step") in VALUATION_RELEVANT_STEPS and step.get("text"):
            t = step["text"]
            step_nums = extract_numbers(t)
            grounded = step_nums & md_nums
            ungrounded = step_nums - md_nums
            relevant_steps.append(
                {
                    "step": step["step"],
                    "text": t,
                    "direction": step.get("direction"),
                    "confidence": step.get("confidence"),
                    "num_count": len(step_nums),
                    "grounded_count": len(grounded),
                    "ungrounded_count": len(ungrounded),
                    "ungrounded_examples": sorted(ungrounded)[:3],
                }
            )

    return {
        "report_id": row["report_id"],
        "report_type": row["report_type"],
        "source_channel": row["source_channel"],
        "stock_name": row["stock_name"],
        "valuation_steps": relevant_steps,
    }


def aggregate(results: list[dict]) -> dict:
    step_counts = Counter()  # step type별 등장 빈도
    step_with_num = Counter()  # 숫자 포함 step 수
    total_nums = Counter()  # step type별 누적 숫자 카운트
    grounded = Counter()
    ungrounded = Counter()

    reports_with_valuation = 0

    for r in results:
        if r["valuation_steps"]:
            reports_with_valuation += 1
        for s in r["valuation_steps"]:
            step_counts[s["step"]] += 1
            if s["num_count"] > 0:
                step_with_num[s["step"]] += 1
            total_nums[s["step"]] += s["num_count"]
            grounded[s["step"]] += s["grounded_count"]
            ungrounded[s["step"]] += s["ungrounded_count"]

    return {
        "n_reports_total": len(results),
        "n_reports_with_valuation_step": reports_with_valuation,
        "by_step": {
            st: {
                "count": step_counts[st],
                "with_number_pct": round(step_with_num[st] / step_counts[st] * 100, 1) if step_counts[st] else 0,
                "total_nums": total_nums[st],
                "grounded_pct": round(grounded[st] / total_nums[st] * 100, 1) if total_nums[st] else 0,
                "ungrounded_pct": round(ungrounded[st] / total_nums[st] * 100, 1) if total_nums[st] else 0,
            }
            for st in step_counts
        },
    }


def main_sync(args):
    rows = asyncio.run(fetch_sample(args.limit))
    if not rows:
        print("샘플 0건.")
        return

    results = [analyze_one(r) for r in rows]
    summary = aggregate(results)

    print(f"\n=== 샘플 {summary['n_reports_total']}건 ===")
    print(f"valuation 관련 step 1개 이상 포함 리포트: {summary['n_reports_with_valuation_step']}건 ({summary['n_reports_with_valuation_step']/summary['n_reports_total']*100:.1f}%)")
    print(f"\n[step type별]")
    print(f"  {'step':22s} {'count':>5s}  {'with_num%':>9s}  {'total_nums':>11s}  {'grounded%':>9s}  {'ungrounded%':>11s}")
    for st, s in sorted(summary["by_step"].items(), key=lambda x: -x[1]["count"]):
        print(f"  {st:22s} {s['count']:5d}  {s['with_number_pct']:8.1f}%  {s['total_nums']:11d}  {s['grounded_pct']:8.1f}%  {s['ungrounded_pct']:10.1f}%")

    if args.show_samples:
        print(f"\n=== 샘플 valuation step 텍스트 (앞 {args.show_samples}건) ===\n")
        shown = 0
        for r in results:
            for s in r["valuation_steps"]:
                if s["num_count"] == 0:
                    continue
                if shown >= args.show_samples:
                    break
                ground_marker = "OK" if s["ungrounded_count"] == 0 else f"WARN ungrounded={s['ungrounded_examples']}"
                print(f"[{r['report_type']}|{s['step']}|{s['confidence']}] {r['stock_name'] or r['source_channel']} (id={r['report_id']})")
                print(f"  {s['text']}")
                print(f"  -> nums={s['num_count']}, grounded={s['grounded_count']}, ungrounded={s['ungrounded_count']} {ground_marker}\n")
                shown += 1
            if shown >= args.show_samples:
                break

    if args.json:
        print("\n=== JSON ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Layer2 valuation chain step grounding 검증")
    parser.add_argument("--limit", type=int, default=300, help="리포트 샘플 (default: 300)")
    parser.add_argument("--show-samples", type=int, default=15, help="실제 텍스트 샘플 출력 개수 (default: 15)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    main_sync(args)
