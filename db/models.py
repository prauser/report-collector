from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 중복 키 구성 필드
    broker: Mapped[str] = mapped_column(String(50), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    analyst: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stock_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    title_normalized: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 종목 정보
    stock_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    report_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # 투자의견
    opinion: Mapped[str | None] = mapped_column(String(30), nullable=True)
    target_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prev_opinion: Mapped[str | None] = mapped_column(String(30), nullable=True)
    prev_target_price: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 실적 추정
    earnings_quarter: Mapped[str | None] = mapped_column(String(10), nullable=True)
    est_revenue: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    est_op_profit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    est_eps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    earnings_surprise: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # PDF
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_size_kb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    pdf_download_failed: Mapped[bool] = mapped_column(Boolean, default=False)

    # 파싱 품질 (good / partial / poor)
    parse_quality: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # 소스 추적
    source_channel: Mapped[str] = mapped_column(String(100), nullable=False)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AI (2차)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_sentiment: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    ai_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    ai_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Layer 2 분석 상태
    analysis_status: Mapped[str | None] = mapped_column(String(20), default="pending")
    analysis_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    markdown_converted: Mapped[bool] = mapped_column(Boolean, default=False)

    # 타임스탬프
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_reports_stock", "stock_name", "report_date"),
        Index("ix_reports_stock_code", "stock_code", "report_date"),
        Index("ix_reports_sector", "sector", "report_date"),
        Index("ix_reports_broker", "broker", "report_date"),
        Index("ix_reports_analyst", "analyst", "report_date"),
        Index("ix_reports_date", "report_date"),
        Index("ix_reports_type", "report_type", "report_date"),
        Index("ix_reports_source", "source_channel", "collected_at"),
        Index("ix_reports_pdf_failed", "pdf_download_failed"),
    )


class StockCode(Base):
    __tablename__ = "stock_codes"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_normalized: Mapped[str | None] = mapped_column(String(100), nullable=True)
    market: Mapped[str | None] = mapped_column(String(10), nullable=True)  # KOSPI, KOSDAQ
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_stock_codes_name", "name"),)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Haiku 가격 (per 1M tokens, USD)
_PRICE: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-haiku-3-5-20241022": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
}


def calc_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """모델별 토큰 사용량을 USD 비용으로 계산."""
    price = _PRICE.get(model, {"input": 1.00, "output": 5.00})
    cost = (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
    return Decimal(str(round(cost, 8)))


class PendingMessage(Base):
    """S2a에서 ambiguous로 분류된 메시지 — 사람 검토 대기."""
    __tablename__ = "pending_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    source_channel: Mapped[str] = mapped_column(String(100), nullable=False)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    s2a_label: Mapped[str | None] = mapped_column(String(20), nullable=True)   # ambiguous
    s2a_reason: Mapped[str | None] = mapped_column(Text, nullable=True)         # LLM 이유

    # pending / broker_report / discarded
    review_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_pending_messages_channel", "source_channel"),
        Index("ix_pending_messages_status", "review_status"),
        Index("ix_pending_messages_created", "created_at"),
    )


class BackfillRun(Base):
    """채널별 백필 실행 기록."""
    __tablename__ = "backfill_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_username: Mapped[str] = mapped_column(String(100), nullable=False)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    from_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    n_scanned: Mapped[int] = mapped_column(Integer, default=0)
    n_saved: Mapped[int] = mapped_column(Integer, default=0)
    n_pending: Mapped[int] = mapped_column(Integer, default=0)
    n_skipped: Mapped[int] = mapped_column(Integer, default=0)

    # running / done / error
    status: Mapped[str] = mapped_column(String(20), default="running")
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_backfill_runs_channel", "channel_username", "run_date"),
    )


class ReportMarkdown(Base):
    """PDF → Markdown 변환 결과 저장."""
    __tablename__ = "report_markdown"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    markdown_text: Mapped[str] = mapped_column(Text, nullable=False)
    converter: Mapped[str | None] = mapped_column(String(50), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportAnalysis(Base):
    """Layer 2 구조화 분석 결과."""
    __tablename__ = "report_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    report_category: Mapped[str] = mapped_column(String(20), nullable=False)
    analysis_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")
    extraction_quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_report_analysis_category", "report_category"),
        Index("idx_report_analysis_schema_ver", "schema_version"),
    )


class ReportStockMention(Base):
    """종목-리포트 연결 테이블."""
    __tablename__ = "report_stock_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mention_type: Mapped[str] = mapped_column(String(20), nullable=False)
    impact: Mapped[str | None] = mapped_column(String(20), nullable=True)
    relevance_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_rsm_stock_code", "stock_code"),
        Index("idx_rsm_report_id", "report_id"),
        Index("idx_rsm_mention_type", "mention_type"),
        Index("uq_report_stock", "report_id", "stock_code", unique=True),
    )


class ReportSectorMention(Base):
    """섹터-리포트 연결 테이블."""
    __tablename__ = "report_sector_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    sector: Mapped[str] = mapped_column(String(100), nullable=False)
    mention_type: Mapped[str] = mapped_column(String(20), nullable=False)
    impact: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_rscm_sector", "sector"),
        Index("idx_rscm_report_id", "report_id"),
        Index("uq_report_sector", "report_id", "sector", unique=True),
    )


class ReportKeyword(Base):
    """키워드 태그 테이블."""
    __tablename__ = "report_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    keyword: Mapped[str] = mapped_column(String(100), nullable=False)
    keyword_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_rk_keyword", "keyword"),
        Index("idx_rk_keyword_type", "keyword_type"),
        Index("idx_rk_report_id", "report_id"),
        Index("uq_report_keyword", "report_id", "keyword", unique=True),
    )


class AnalysisJob(Base):
    """분석 처리 로그."""
    __tablename__ = "analysis_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    target_schema_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_aj_report_id", "report_id"),
        Index("idx_aj_status", "status"),
        Index("idx_aj_job_type", "job_type"),
    )


class Trade(Base):
    """매매 체결 내역."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # 'buy' / 'sell'
    traded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    broker: Mapped[str] = mapped_column(String(20), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str] = mapped_column(String(10), nullable=False)
    fees: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    review: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("symbol", "traded_at", "side", "price", "quantity", "broker", name="uq_trade_dedup"),
        Index("ix_trades_symbol", "symbol"),
        Index("ix_trades_traded_at", "traded_at"),
        Index("ix_trades_side", "side"),
    )


class TradeIndicator(Base):
    """매매 시점 기술적 지표 스냅샷 (trade와 1:1)."""
    __tablename__ = "trade_indicators"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    stoch_k_d: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rsi_14: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    macd: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ma_position: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    bb_position: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    snapshot_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class TradePair(Base):
    """매수-매도 체결을 연결하는 손익 계산 단위."""
    __tablename__ = "trade_pairs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    buy_trade_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False
    )
    sell_trade_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False
    )
    profit_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    holding_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_trade_pairs_buy_trade_id", "buy_trade_id"),
        Index("ix_trade_pairs_sell_trade_id", "sell_trade_id"),
    )


class LlmUsage(Base):
    """LLM API 호출 비용 집계 테이블."""
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)  # parse / summarize / pdf_analysis

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)

    # LLM 분류 결과 (parse 목적일 때: broker_report / news / general)
    message_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # 연관 리포트 (선택적)
    report_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("reports.id", ondelete="SET NULL"), nullable=True
    )
    source_channel: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("ix_llm_usage_purpose_date", "purpose", "called_at"),
        Index("ix_llm_usage_model_date", "model", "called_at"),
        Index("ix_llm_usage_message_type", "message_type"),
    )
