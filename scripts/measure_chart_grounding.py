"""chart_digitize 데이터의 Layer2 grounding 기여도 측정.

가설: chart_digitize가 Layer2의 valuation/financial 근거에 실질 기여한다면,
Layer2 출력 텍스트에 인용된 숫자 중 chart_text에만 있고 markdown에는 없는
숫자의 비율이 유의미하게 높아야 한다.

방식:
1. report_chart_text + report_analysis + report_markdown 모두 있는 리포트
   N건 무작위 샘플
2. 각 리포트의 Layer2 출력(thesis/chain/financials)에서 숫자 토큰 추출
3. 각 숫자가 markdown / chart / 둘 다 / 어디에도 없음 분류
4. 통계 집계 출력

읽기 전용. 비용 0.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from db.session import AsyncSessionLocal


# 숫자 토큰 패턴: 1,000 / 1.5 / 25 / 5,000 등 정수/소수
# 단위(%, 원, 조, 억, 만, 배 등)는 별도로 받지 않고 순수 숫자만 추적해 매칭률 ↑
NUM_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b\d+\.\d+|\b\d{2,}\b")


def extract_numbers(text_blob: str) -> set[str]:
    """텍스트에서 숫자 토큰 추출. 정규화: 콤마 제거."""
    if not text_blob:
        return set()
    found = set()
    for m in NUM_RE.findall(text_blob):
        # 정규화: '1,000' -> '1000'
        norm = m.replace(",", "")
        # 길이 1자리 숫자는 노이즈 많으므로 제외 (이미 패턴에서 \d{2,} 처리됨)
        if len(norm.replace(".", "")) >= 2:
            found.add(norm)
    return found


def collect_layer2_text(analysis_data: dict) -> str:
    """Layer2 출력에서 valuation/grounding 관련 텍스트 필드 수집."""
    parts = []

    thesis = analysis_data.get("thesis") or {}
    if thesis.get("summary"):
        parts.append(thesis["summary"])

    for step in analysis_data.get("chain") or []:
        if step.get("text"):
            parts.append(step["text"])
        # change_reason 등 기타 필드 포함
        if step.get("change_reason"):
            parts.append(step["change_reason"])

    opinion = analysis_data.get("opinion") or {}
    if opinion.get("change_reason"):
        parts.append(opinion["change_reason"])

    fin = analysis_data.get("financials") or {}
    for k in ("revenue", "operating_profit", "eps", "earnings_quarter"):
        v = fin.get(k)
        if isinstance(v, str):
            parts.append(v)
    km = fin.get("key_metrics") or {}
    if isinstance(km, dict):
        for v in km.values():
            if isinstance(v, (str, int, float)):
                parts.append(str(v))

    return "\n".join(parts)


def chart_texts_to_blob(chart_texts) -> str:
    """chart_texts (JSONB list[str]) -> single blob."""
    if not chart_texts:
        return ""
    if isinstance(chart_texts, list):
        return "\n".join(t for t in chart_texts if isinstance(t, str))
    if isinstance(chart_texts, str):
        return chart_texts
    return str(chart_texts)


async def fetch_sample(limit: int) -> list[dict]:
    """3 테이블 모두 있는 리포트 무작위 샘플."""
    sql = text(
        """
        SELECT
            r.id AS report_id,
            r.report_type,
            r.source_channel,
            a.analysis_data,
            m.markdown_text,
            c.chart_texts,
            c.image_count,
            c.success_count
        FROM reports r
        JOIN report_chart_text c ON c.report_id = r.id
        JOIN report_analysis a ON a.report_id = r.id
        JOIN report_markdown m ON m.report_id = r.id
        WHERE r.pipeline_status = 'done'
        ORDER BY RANDOM()
        LIMIT :limit
        """
    )
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {"limit": limit})
        rows = result.mappings().all()
    return [dict(r) for r in rows]


def analyze_one(row: dict) -> dict:
    analysis_data = row["analysis_data"]
    if isinstance(analysis_data, str):
        analysis_data = json.loads(analysis_data)

    layer2_text = collect_layer2_text(analysis_data)
    md_text = row["markdown_text"] or ""
    chart_blob = chart_texts_to_blob(row["chart_texts"])

    layer2_nums = extract_numbers(layer2_text)
    md_nums = extract_numbers(md_text)
    chart_nums = extract_numbers(chart_blob)

    chart_only = layer2_nums & (chart_nums - md_nums)
    md_only = layer2_nums & (md_nums - chart_nums)
    both = layer2_nums & md_nums & chart_nums
    neither = layer2_nums - md_nums - chart_nums

    return {
        "report_id": row["report_id"],
        "report_type": row["report_type"],
        "source_channel": row["source_channel"],
        "image_count": row["image_count"],
        "success_count": row["success_count"],
        "layer2_total": len(layer2_nums),
        "md_only": len(md_only),
        "chart_only": len(chart_only),
        "both": len(both),
        "neither": len(neither),
        "chart_only_examples": sorted(chart_only)[:5],
        "neither_examples": sorted(neither)[:5],
    }


def aggregate(results: list[dict]) -> dict:
    totals = Counter()
    for r in results:
        totals["layer2_total"] += r["layer2_total"]
        totals["md_only"] += r["md_only"]
        totals["chart_only"] += r["chart_only"]
        totals["both"] += r["both"]
        totals["neither"] += r["neither"]

    n = len(results)
    layer2_avg = totals["layer2_total"] / n if n else 0
    chart_only_pct = totals["chart_only"] / totals["layer2_total"] * 100 if totals["layer2_total"] else 0
    md_only_pct = totals["md_only"] / totals["layer2_total"] * 100 if totals["layer2_total"] else 0
    both_pct = totals["both"] / totals["layer2_total"] * 100 if totals["layer2_total"] else 0
    neither_pct = totals["neither"] / totals["layer2_total"] * 100 if totals["layer2_total"] else 0

    # report-type별 분해
    by_type: dict[str, Counter] = {}
    for r in results:
        rt = r["report_type"] or "unknown"
        c = by_type.setdefault(rt, Counter())
        c["count"] += 1
        c["layer2_total"] += r["layer2_total"]
        c["chart_only"] += r["chart_only"]
        c["md_only"] += r["md_only"]
        c["both"] += r["both"]
        c["neither"] += r["neither"]

    return {
        "n_reports": n,
        "totals": dict(totals),
        "layer2_avg_per_report": round(layer2_avg, 2),
        "pct": {
            "chart_only": round(chart_only_pct, 2),
            "md_only": round(md_only_pct, 2),
            "both": round(both_pct, 2),
            "neither": round(neither_pct, 2),
        },
        "by_report_type": {
            rt: {
                "count": c["count"],
                "layer2_total": c["layer2_total"],
                "chart_only_pct": round(c["chart_only"] / c["layer2_total"] * 100, 2) if c["layer2_total"] else 0,
                "md_only_pct": round(c["md_only"] / c["layer2_total"] * 100, 2) if c["layer2_total"] else 0,
                "both_pct": round(c["both"] / c["layer2_total"] * 100, 2) if c["layer2_total"] else 0,
                "neither_pct": round(c["neither"] / c["layer2_total"] * 100, 2) if c["layer2_total"] else 0,
            }
            for rt, c in by_type.items()
        },
    }


def main_sync(args):
    rows = asyncio.run(fetch_sample(args.limit))
    if not rows:
        print("샘플 0건. report_chart_text + report_analysis + report_markdown 모두 있는 done 리포트 없음.")
        return

    results = [analyze_one(r) for r in rows]
    summary = aggregate(results)

    print(f"\n=== 샘플: {summary['n_reports']}건 ===")
    print(f"리포트당 Layer2 인용 숫자 평균: {summary['layer2_avg_per_report']}개")
    print(f"\n분포 (전체 인용 숫자 중):")
    p = summary["pct"]
    print(f"  chart에만 있음 (chart_digitize 기여): {p['chart_only']}%")
    print(f"  markdown에만 있음                    : {p['md_only']}%")
    print(f"  둘 다 있음 (출처 구분 불가)          : {p['both']}%")
    print(f"  어디에도 없음 (LLM 환각/계산)        : {p['neither']}%")

    print(f"\n=== report_type별 ===")
    for rt, st in sorted(summary["by_report_type"].items(), key=lambda x: -x[1]["count"]):
        print(f"  [{rt}] n={st['count']:3d}  chart_only={st['chart_only_pct']:5.1f}%  md_only={st['md_only_pct']:5.1f}%  both={st['both_pct']:5.1f}%  neither={st['neither_pct']:5.1f}%")

    if args.show_examples:
        print("\n=== chart-only 인용 예시 (앞 10건) ===")
        for r in results[:10]:
            if r["chart_only_examples"]:
                print(f"  report_id={r['report_id']} ({r['report_type']}): {r['chart_only_examples']}")

    if args.json:
        print("\n=== JSON ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="chart_digitize Layer2 grounding 기여도 측정")
    parser.add_argument("--limit", type=int, default=200, help="샘플 크기 (default: 200)")
    parser.add_argument("--show-examples", action="store_true", help="chart-only 예시 출력")
    parser.add_argument("--json", action="store_true", help="JSON 결과도 출력")
    args = parser.parse_args()
    main_sync(args)
