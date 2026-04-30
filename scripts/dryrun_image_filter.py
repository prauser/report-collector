"""image_extractor 필터 dry-run.

실제 chart_digitize 호출 없이 페이지별 시그널/점수만 출력.
임계값 튜닝용.

Usage:
    python scripts/dryrun_image_filter.py --sample 20
    python scripts/dryrun_image_filter.py --pdf-paths F:/report-collector/pdfs/abc.pdf F:/.../def.pdf
    python scripts/dryrun_image_filter.py --report-ids 131271 131272
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf
from sqlalchemy import select

from config.settings import settings
from db.models import Report
from db.session import AsyncSessionLocal
from parser.image_extractor import (
    _HARD_SKIP_FIRST_N,
    _HARD_SKIP_LAST_N,
    _LARGE_IMAGE_RATIO,
    _SCORE_THRESHOLD,
    _TEXT_COVERAGE_THRESHOLD,
    _VECTOR_DENSITY_THRESHOLD,
    _collect_signals,
    _score_page,
)


def analyze_pdf(pdf_path: Path) -> dict:
    """단일 PDF에 대한 페이지별 시그널/점수/통과 여부."""
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        return {"path": str(pdf_path), "error": str(e), "pages": []}

    try:
        signals = _collect_signals(doc)
        total = len(signals)
        keyword_pages = {
            s.page_idx for s in signals
            if s.has_keyword or s.section_header_keyword
        }

        rows = []
        for s in signals:
            neighbor_kw = (
                (s.page_idx - 1) in keyword_pages
                or (s.page_idx + 1) in keyword_pages
            )
            score = _score_page(s, total, neighbor_kw)
            decision = "RENDER" if (
                s.text_coverage <= _TEXT_COVERAGE_THRESHOLD
                or s.largest_image_ratio >= _LARGE_IMAGE_RATIO
            ) else "EMBED"
            passed = score >= _SCORE_THRESHOLD
            rows.append({
                "page": s.page_idx,
                "tc": round(s.text_coverage, 2),
                "vec": s.vector_count,
                "img": round(s.largest_image_ratio, 2),
                "kw": s.has_keyword,
                "hdr": s.section_header_keyword,
                "nbr": neighbor_kw,
                "score": score,
                "pass": passed,
                "mode": decision if passed else "-",
            })
        return {
            "path": str(pdf_path),
            "total_pages": total,
            "passed": sum(1 for r in rows if r["pass"]),
            "rows": rows,
        }
    finally:
        doc.close()


async def fetch_pdf_paths_from_db(
    sample: int | None,
    report_ids: list[int] | None,
    diverse: bool,
) -> list[tuple[int, str | None, str | None, Path]]:
    """Returns list of (report_id, broker, report_type, pdf_path)."""
    from sqlalchemy import func

    async with AsyncSessionLocal() as sess:
        if report_ids:
            stmt = select(
                Report.id, Report.broker, Report.report_type, Report.pdf_path
            ).where(Report.id.in_(report_ids))
            rows = (await sess.execute(stmt)).all()
        elif diverse:
            # broker × report_type 조합당 최대 2건씩
            from sqlalchemy import text
            sql = text("""
                SELECT id, broker, report_type, pdf_path
                FROM (
                    SELECT id, broker, report_type, pdf_path,
                           ROW_NUMBER() OVER (
                               PARTITION BY broker, report_type
                               ORDER BY id DESC
                           ) AS rn
                    FROM reports
                    WHERE pdf_path IS NOT NULL
                      AND pipeline_status = 'done'
                ) t
                WHERE rn <= 2
                ORDER BY broker, report_type
                LIMIT :lim
            """)
            rows = (await sess.execute(sql, {"lim": sample or 30})).all()
        else:
            stmt = (
                select(Report.id, Report.broker, Report.report_type, Report.pdf_path)
                .where(Report.pdf_path.isnot(None))
                .where(Report.pipeline_status == "done")
                .order_by(Report.id.desc())
                .limit(sample or 20)
            )
            rows = (await sess.execute(stmt)).all()

        result = []
        for r in rows:
            if not r[3]:
                continue
            p = Path(r[3])
            if not p.is_absolute():
                p = settings.pdf_base_path / p
            result.append((r[0], r[1], r[2], p))
        return result


def print_report(result: dict, label: str = "") -> None:
    print("=" * 100)
    print(f"PDF: {result['path']}  {label}")
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return
    print(f"  pages={result['total_pages']}  passed={result['passed']}  "
          f"hard_skip=first{_HARD_SKIP_FIRST_N}/last{_HARD_SKIP_LAST_N}  "
          f"threshold={_SCORE_THRESHOLD}")
    print(f"  {'pg':>3} {'tc':>5} {'vec':>5} {'img':>5} {'kw':>5} {'hdr':>5} "
          f"{'nbr':>5} {'score':>5} {'pass':>5} mode")
    for r in result["rows"]:
        print(f"  {r['page']:>3} {r['tc']:>5} {r['vec']:>5} {r['img']:>5} "
              f"{str(r['kw']):>5} {str(r['hdr']):>5} {str(r['nbr']):>5} "
              f"{r['score']:>5} {str(r['pass']):>5} {r['mode']}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None,
                    help="DB에서 최근 done 리포트 N건 샘플")
    ap.add_argument("--report-ids", type=int, nargs="+", default=None)
    ap.add_argument("--pdf-paths", type=str, nargs="+", default=None)
    ap.add_argument("--diverse", action="store_true",
                    help="broker × report_type 조합당 최대 2건씩 다양성 샘플")
    args = ap.parse_args()

    if args.pdf_paths:
        items = [(0, None, None, Path(p)) for p in args.pdf_paths]
    else:
        items = await fetch_pdf_paths_from_db(args.sample, args.report_ids, args.diverse)

    if not items:
        print("No PDFs to analyze.")
        return

    total_pages = 0
    total_passed = 0
    by_type: dict[str, list[tuple[int, int]]] = {}
    for rid, broker, rtype, p in items:
        if not p.exists():
            print(f"SKIP (missing): rid={rid} {p}")
            continue
        label = f"[rid={rid} {broker or '?'} / {rtype or '?'}]"
        result = analyze_pdf(p)
        print_report(result, label)
        tp = result.get("total_pages", 0)
        ps = result.get("passed", 0)
        total_pages += tp
        total_passed += ps
        by_type.setdefault(rtype or "?", []).append((tp, ps))

    print("=" * 100)
    print(f"SUMMARY: {len(items)} PDFs, {total_pages} pages total, "
          f"{total_passed} pages passed ({total_passed / max(total_pages, 1):.1%})")
    print("\nBy report_type:")
    for rt, vals in sorted(by_type.items()):
        tp = sum(v[0] for v in vals)
        ps = sum(v[1] for v in vals)
        print(f"  {rt:>20}  {len(vals):>3} PDFs  {tp:>4} pages  "
              f"{ps:>4} passed ({ps / max(tp, 1):.1%})")


if __name__ == "__main__":
    asyncio.run(main())
