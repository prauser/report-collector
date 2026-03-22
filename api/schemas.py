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
    source_message_id: int | None


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
    # Layer 2
    analysis_done: int
    analysis_pending: int
    analysis_failed: int
    analysis_truncated: int
    analysis_by_category: list[dict]
    top_brokers: list[dict]
    top_stocks: list[dict]


# ---------------------------------------------------------------------------
# Trade schemas
# ---------------------------------------------------------------------------


class TradeBase(BaseModel):
    """공통 매매 필드."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    side: str
    traded_at: datetime
    price: Decimal
    quantity: int
    amount: Decimal
    broker: str
    account_type: str
    market: str
    fees: Decimal | None


class TradeResponse(TradeBase):
    """매매 목록/상세 응답."""

    id: int
    reason: str | None
    review: str | None
    created_at: datetime


class TradeIndicatorResponse(BaseModel):
    """기술적 지표 스냅샷."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trade_id: int
    stoch_k_d: dict | None
    rsi_14: Decimal | None
    macd: dict | None
    ma_position: dict | None
    bb_position: Decimal | None
    volume_ratio: Decimal | None
    snapshot_text: str | None


class TradeDetailResponse(TradeResponse):
    """매매 상세 — 기술적 지표 포함."""

    indicator: TradeIndicatorResponse | None


class TradeUploadResponse(BaseModel):
    """CSV 업로드 결과."""

    inserted: int
    skipped: int
    preview: list[TradeBase] | None  # dry_run=True 시 파싱 결과


class TradeUpdateRequest(BaseModel):
    """reason / review 수정 요청."""

    reason: str | None = None
    review: str | None = None


class TradeListResponse(BaseModel):
    """매매 목록 페이지네이션 응답."""

    items: list[TradeResponse]
    total: int
    limit: int
    offset: int


class TradeStatsResponse(BaseModel):
    """매매 통계."""

    total_count: int
    buy_count: int
    sell_count: int
    total_amount: Decimal
    symbol_frequency: list[dict]


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Agent 챗봇 schemas
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """채팅 요청."""

    message: str
    session_id: int | None = None


class ChatSessionResponse(BaseModel):
    """대화 세션 응답."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    user_id: str | None
    message_count: int
    created_at: datetime
    updated_at: datetime | None


class ChatMessageResponse(BaseModel):
    """대화 메시지 응답."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    role: str
    content: str
    context_report_count: int | None
    created_at: datetime


# ---------------------------------------------------------------------------


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
