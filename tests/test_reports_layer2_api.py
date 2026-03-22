"""
Unit tests for Layer2 display — Part A (API schemas and router helpers).

These tests do NOT require a live database. They test the schema logic and
the helper functions (_display_title, _layer2_summary_from_analysis,
_to_summary) in isolation using mock objects.
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

# ─── helpers to build mock ORM objects ─────────────────────────────────────

def _make_report(**kwargs) -> MagicMock:
    """Create a mock Report ORM object with sensible defaults."""
    defaults = dict(
        id=1,
        broker="테스트증권",
        report_date=date(2026, 3, 1),
        analyst="홍길동",
        stock_name="삼성전자",
        stock_code="005930",
        title="원본제목_rawfile_v2.pdf",
        sector="반도체",
        report_type="기업분석",
        opinion="매수",
        target_price=90000,
        prev_opinion="매수",
        prev_target_price=80000,
        pdf_path="/data/foo.pdf",
        ai_processed_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
        ai_sentiment=Decimal("0.75"),
        collected_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        source_channel="@test_channel",
        pdf_url="https://example.com/report.pdf",
        pdf_size_kb=512,
        page_count=10,
        earnings_quarter="1Q26",
        est_revenue=123_0000,
        est_op_profit=12_0000,
        est_eps=1234,
        ai_summary="AI 요약 텍스트",
        ai_keywords=["반도체", "HBM"],
        raw_text="원문",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _make_report_analysis(analysis_data: dict, report_category: str = "stock",
                           extraction_quality: str = "high") -> MagicMock:
    """Create a mock ReportAnalysis ORM object."""
    ra = MagicMock()
    ra.report_category = report_category
    ra.analysis_data = analysis_data
    ra.extraction_quality = extraction_quality
    return ra


# ─── Import helpers (no FastAPI dependency) ────────────────────────────────

from api.layer2_helpers import _display_title, _layer2_summary_from_analysis, _to_summary


# ─── _display_title ─────────────────────────────────────────────────────────

class TestDisplayTitle:

    def test_returns_meta_title_when_present(self):
        """Layer2 meta.title이 있으면 그걸 반환."""
        report = _make_report(title="raw_filename.pdf")
        ra = _make_report_analysis({"meta": {"title": "정제된 리포트 제목"}})
        assert _display_title(report, ra) == "정제된 리포트 제목"

    def test_falls_back_to_report_title_when_no_ra(self):
        """ra가 None이면 report.title 반환."""
        report = _make_report(title="원본제목")
        assert _display_title(report, None) == "원본제목"

    def test_falls_back_when_meta_title_empty_string(self):
        """meta.title이 빈 문자열이면 report.title 반환."""
        report = _make_report(title="원본제목")
        ra = _make_report_analysis({"meta": {"title": ""}})
        assert _display_title(report, ra) == "원본제목"

    def test_falls_back_when_meta_missing(self):
        """analysis_data에 meta 키가 없으면 report.title 반환."""
        report = _make_report(title="원본제목")
        ra = _make_report_analysis({"thesis": {"summary": "test"}})
        assert _display_title(report, ra) == "원본제목"

    def test_falls_back_when_meta_title_none(self):
        """meta.title이 None이면 report.title 반환."""
        report = _make_report(title="원본제목")
        ra = _make_report_analysis({"meta": {"title": None}})
        assert _display_title(report, ra) == "원본제목"


# ─── _layer2_summary_from_analysis ──────────────────────────────────────────

class TestLayer2SummaryFromAnalysis:

    def test_returns_none_tuple_when_ra_is_none(self):
        summary, sentiment, category = _layer2_summary_from_analysis(None)
        assert summary is None
        assert sentiment is None
        assert category is None

    def test_extracts_thesis_fields(self):
        ra = _make_report_analysis(
            {"thesis": {"summary": "핵심 투자 논리", "sentiment": 0.7}},
            report_category="stock",
        )
        summary, sentiment, category = _layer2_summary_from_analysis(ra)
        assert summary == "핵심 투자 논리"
        assert sentiment == 0.7
        assert category == "stock"

    def test_handles_missing_thesis(self):
        ra = _make_report_analysis({}, report_category="macro")
        summary, sentiment, category = _layer2_summary_from_analysis(ra)
        assert summary is None
        assert sentiment is None
        assert category == "macro"

    def test_handles_partial_thesis(self):
        ra = _make_report_analysis({"thesis": {"summary": "요약만 있음"}})
        summary, sentiment, category = _layer2_summary_from_analysis(ra)
        assert summary == "요약만 있음"
        assert sentiment is None

    def test_industry_category(self):
        ra = _make_report_analysis({}, report_category="industry")
        _, _, category = _layer2_summary_from_analysis(ra)
        assert category == "industry"


# ─── _to_summary ──────────────────────────────────────────────────────────

class TestToSummary:

    def test_without_layer2(self):
        """ra=None일 때 Layer2 필드는 None/fallback."""
        report = _make_report(title="원본제목")
        summary = _to_summary(report, None)
        assert summary.display_title == "원본제목"
        assert summary.layer2_summary is None
        assert summary.layer2_sentiment is None
        assert summary.layer2_category is None

    def test_with_layer2(self):
        """ra가 있을 때 Layer2 필드 채워짐."""
        report = _make_report(title="raw.pdf")
        ra = _make_report_analysis({
            "meta": {"title": "정제된 제목"},
            "thesis": {"summary": "투자 논리", "sentiment": 0.8},
        }, report_category="stock")
        summary = _to_summary(report, ra)
        assert summary.display_title == "정제된 제목"
        assert summary.layer2_summary == "투자 논리"
        assert summary.layer2_sentiment == 0.8
        assert summary.layer2_category == "stock"

    def test_standard_fields_preserved(self):
        """기존 report 필드들이 정상적으로 매핑됨."""
        report = _make_report()
        summary = _to_summary(report, None)
        assert summary.id == report.id
        assert summary.broker == report.broker
        assert summary.has_pdf is True  # pdf_path is not None
        assert summary.has_ai is True   # ai_processed_at is not None

    def test_has_pdf_false_when_no_path(self):
        report = _make_report(pdf_path=None)
        summary = _to_summary(report, None)
        assert summary.has_pdf is False

    def test_has_ai_false_when_no_ai_processed_at(self):
        report = _make_report(ai_processed_at=None)
        summary = _to_summary(report, None)
        assert summary.has_ai is False


# ─── Schema validation ──────────────────────────────────────────────────────

class TestSchemas:

    def test_report_summary_serialises_layer2_fields(self):
        """ReportSummary 스키마가 Layer2 필드를 직렬화."""
        from api.schemas import ReportSummary

        data = dict(
            id=1, broker="증권사", report_date=date(2026, 1, 1),
            analyst=None, stock_name=None, stock_code=None,
            title="원본", sector=None, report_type=None,
            opinion=None, target_price=None, prev_opinion=None, prev_target_price=None,
            has_pdf=False, has_ai=False, ai_sentiment=None,
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source_channel="@ch",
            display_title="정제된 제목",
            layer2_summary="요약",
            layer2_sentiment=0.5,
            layer2_category="stock",
        )
        schema = ReportSummary(**data)
        # Support both pydantic v1 (.dict) and v2 (.model_dump)
        dumped = schema.model_dump() if hasattr(schema, "model_dump") else schema.dict()
        assert dumped["display_title"] == "정제된 제목"
        assert dumped["layer2_summary"] == "요약"
        assert dumped["layer2_sentiment"] == 0.5
        assert dumped["layer2_category"] == "stock"

    def test_report_summary_layer2_fields_nullable(self):
        """Layer2 필드가 모두 None이어도 유효."""
        from api.schemas import ReportSummary

        data = dict(
            id=2, broker="증권사", report_date=date(2026, 1, 1),
            analyst=None, stock_name=None, stock_code=None,
            title="제목", sector=None, report_type=None,
            opinion=None, target_price=None, prev_opinion=None, prev_target_price=None,
            has_pdf=False, has_ai=False, ai_sentiment=None,
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source_channel="@ch",
            display_title="제목",
            layer2_summary=None,
            layer2_sentiment=None,
            layer2_category=None,
        )
        schema = ReportSummary(**data)
        assert schema.layer2_summary is None
        assert schema.layer2_sentiment is None
        assert schema.layer2_category is None

    def test_layer2_data_schema(self):
        """Layer2Data 스키마 검증."""
        from api.schemas import Layer2Data, Layer2StockMention, Layer2SectorMention, Layer2Keyword

        layer2 = Layer2Data(
            report_category="stock",
            analysis_data={
                "meta": {"title": "테스트"},
                "thesis": {"summary": "요약", "sentiment": 0.7},
                "chain": [
                    {"step": "trigger", "text": "HBM 수요", "direction": "positive", "confidence": "high"}
                ],
            },
            extraction_quality="high",
            stock_mentions=[
                Layer2StockMention(
                    stock_code="005930",
                    company_name="삼성전자",
                    mention_type="primary",
                    impact="positive",
                    relevance_score=0.95,
                )
            ],
            sector_mentions=[
                Layer2SectorMention(sector="반도체", mention_type="primary", impact="positive")
            ],
            keywords=[
                Layer2Keyword(keyword="HBM", keyword_type="product")
            ],
        )
        assert layer2.report_category == "stock"
        assert layer2.extraction_quality == "high"
        assert len(layer2.stock_mentions) == 1
        assert layer2.stock_mentions[0].stock_code == "005930"
        assert len(layer2.sector_mentions) == 1
        assert len(layer2.keywords) == 1

    def test_report_detail_layer2_nullable(self):
        """ReportDetail의 layer2 필드는 nullable."""
        from api.schemas import ReportDetail

        data = dict(
            id=1, broker="증권사", report_date=date(2026, 1, 1),
            analyst=None, stock_name=None, stock_code=None,
            title="제목", sector=None, report_type=None,
            opinion=None, target_price=None, prev_opinion=None, prev_target_price=None,
            has_pdf=False, has_ai=False, ai_sentiment=None,
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source_channel="@ch",
            display_title="제목",
            layer2_summary=None, layer2_sentiment=None, layer2_category=None,
            ai_summary=None, ai_keywords=None, ai_processed_at=None,
            pdf_url=None, pdf_path=None, pdf_size_kb=None, page_count=None,
            earnings_quarter=None, est_revenue=None, est_op_profit=None, est_eps=None,
            raw_text=None,
            layer2=None,
        )
        detail = ReportDetail(**data)
        assert detail.layer2 is None

    def test_report_detail_with_layer2(self):
        """ReportDetail에 layer2 포함."""
        from api.schemas import ReportDetail, Layer2Data

        layer2 = Layer2Data(
            report_category="macro",
            analysis_data={"thesis": {"summary": "매크로 논리", "sentiment": 0.2}},
            extraction_quality="medium",
            stock_mentions=[],
            sector_mentions=[],
            keywords=[],
        )
        data = dict(
            id=3, broker="증권사", report_date=date(2026, 1, 1),
            analyst=None, stock_name=None, stock_code=None,
            title="제목", sector=None, report_type=None,
            opinion=None, target_price=None, prev_opinion=None, prev_target_price=None,
            has_pdf=False, has_ai=False, ai_sentiment=None,
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source_channel="@ch",
            display_title="정제된 제목",
            layer2_summary="매크로 논리", layer2_sentiment=0.2, layer2_category="macro",
            ai_summary=None, ai_keywords=None, ai_processed_at=None,
            pdf_url=None, pdf_path=None, pdf_size_kb=None, page_count=None,
            earnings_quarter=None, est_revenue=None, est_op_profit=None, est_eps=None,
            raw_text=None,
            layer2=layer2,
        )
        detail = ReportDetail(**data)
        assert detail.layer2 is not None
        assert detail.layer2.report_category == "macro"
        assert detail.layer2.extraction_quality == "medium"


# ─── Edge cases ─────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_display_title_with_whitespace_only_meta_title(self):
        """meta.title이 공백만 있으면 strip() 후 falsy → report.title 사용."""
        report = _make_report(title="원본제목")
        ra = _make_report_analysis({"meta": {"title": "   "}})
        result = _display_title(report, ra)
        assert result == "원본제목"  # whitespace-only meta title falls back to report.title

    def test_negative_sentiment(self):
        ra = _make_report_analysis({"thesis": {"sentiment": -0.9}})
        _, sentiment, _ = _layer2_summary_from_analysis(ra)
        assert sentiment == -0.9

    def test_zero_sentiment(self):
        ra = _make_report_analysis({"thesis": {"sentiment": 0.0}})
        _, sentiment, _ = _layer2_summary_from_analysis(ra)
        assert sentiment == 0.0

    def test_analysis_data_with_null_thesis_value(self):
        ra = _make_report_analysis({"thesis": None})
        summary, sentiment, _ = _layer2_summary_from_analysis(ra)
        assert summary is None
        assert sentiment is None
