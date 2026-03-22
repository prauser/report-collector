"""
Layer2 display helper functions — no FastAPI dependency.

Extracted so they can be unit-tested without a FastAPI installation.
"""
from __future__ import annotations

from api.schemas import ReportSummary, Layer2Data


def _layer2_summary_from_analysis(ra) -> tuple[str | None, float | None, str | None]:
    """ReportAnalysis 객체에서 (summary, sentiment, category) 추출."""
    if ra is None:
        return None, None, None
    thesis = ra.analysis_data.get("thesis") or {}
    return (
        thesis.get("summary"),
        thesis.get("sentiment"),
        ra.report_category,
    )


def _display_title(report, ra) -> str:
    """Layer2 meta.title이 있으면 사용, 없으면 기존 title."""
    if ra is not None:
        meta_title = (ra.analysis_data.get("meta") or {}).get("title", "")
        if meta_title and meta_title.strip():
            return meta_title.strip()
    return report.title


def _to_summary(r, ra=None) -> ReportSummary:
    l2_summary, l2_sentiment, l2_category = _layer2_summary_from_analysis(ra)
    return ReportSummary(
        id=r.id,
        broker=r.broker,
        report_date=r.report_date,
        analyst=r.analyst,
        stock_name=r.stock_name,
        stock_code=r.stock_code,
        title=r.title,
        sector=r.sector,
        report_type=r.report_type,
        opinion=r.opinion,
        target_price=r.target_price,
        prev_opinion=r.prev_opinion,
        prev_target_price=r.prev_target_price,
        has_pdf=r.pdf_path is not None,
        has_ai=r.ai_processed_at is not None,
        ai_sentiment=r.ai_sentiment,
        collected_at=r.collected_at,
        source_channel=r.source_channel,
        display_title=_display_title(r, ra),
        layer2_summary=l2_summary,
        layer2_sentiment=l2_sentiment,
        layer2_category=l2_category,
    )
