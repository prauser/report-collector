from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
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

    # 소스 추적
    source_channel: Mapped[str] = mapped_column(String(100), nullable=False)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AI (2차)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_sentiment: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    ai_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    ai_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
