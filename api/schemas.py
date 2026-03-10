"""API 응답 Pydantic 스키마."""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ReportSummary(BaseModel):
    """목록 조회용 요약 스키마."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    broker: str
    report_date: date
    analyst: str | None
    stock_name: str | None
    stock_code: str | None
    title: str
    sector: str | None
    report_type: str | None
    opinion: str | None
    target_price: int | None
    prev_opinion: str | None
    prev_target_price: int | None
    has_pdf: bool
    has_ai: bool
    ai_sentiment: Decimal | None
    collected_at: datetime
    source_channel: str


class ReportDetail(ReportSummary):
    """상세 조회용 스키마 (AI 분석 포함)."""

    ai_summary: str | None
    ai_keywords: list[str] | None
    ai_processed_at: datetime | None
    pdf_url: str | None
    pdf_path: str | None
    pdf_size_kb: int | None
    page_count: int | None
    earnings_quarter: str | None
    est_revenue: int | None
    est_op_profit: int | None
    est_eps: int | None
    raw_text: str | None


class PaginatedReports(BaseModel):
    total: int
    page: int
    limit: int
    items: list[ReportSummary]


class FilterOptions(BaseModel):
    brokers: list[str]
    opinions: list[str]
    report_types: list[str]
    channels: list[str]


class OverviewStats(BaseModel):
    total_reports: int
    reports_today: int
    reports_with_pdf: int
    reports_with_ai: int
    top_brokers: list[dict]
    top_stocks: list[dict]


class LlmUsageStat(BaseModel):
    model: str
    purpose: str
    message_type: str | None
    call_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: Decimal


class LlmStats(BaseModel):
    period_days: int
    total_cost_usd: Decimal
    by_purpose: list[LlmUsageStat]
    by_message_type: list[dict]
    daily_cost: list[dict]
