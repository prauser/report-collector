"""trades 테이블 CRUD — upsert / 조회 / 통계."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Trade, TradeIndicator
from trades.csv_parsers.common import TradeRow

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Filters dataclass
# ---------------------------------------------------------------------------

@dataclass
class TradeFilters:
    """Optional filters for trade queries."""
    symbol: str | None = None
    date_from: date | datetime | None = None
    date_to: date | datetime | None = None
    broker: str | None = None
    side: str | None = None
    account_type: str | None = None
    offset: int = 0
    limit: int = 100


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------

async def upsert_trades(session: AsyncSession, rows: list[TradeRow]) -> dict[str, int]:
    """TradeRow 리스트를 DB에 upsert (ON CONFLICT DO NOTHING).

    Returns
    -------
    dict with keys "inserted" and "skipped".
    """
    if not rows:
        return {"inserted": 0, "skipped": 0}

    values_list = [
        {
            "symbol": row.symbol,
            "name": row.name,
            "side": row.side,
            "traded_at": row.traded_at,
            "price": row.price,
            "quantity": row.quantity,
            "amount": row.amount,
            "broker": row.broker,
            "account_type": row.account_type,
            "market": row.market,
            "fees": row.fees,
        }
        for row in rows
    ]

    stmt = insert(Trade).values(values_list).on_conflict_do_nothing(
        constraint="uq_trade_dedup"
    )
    result = await session.execute(stmt)
    await session.commit()

    # NOTE: rowcount for ON CONFLICT DO NOTHING may be -1 on some drivers
    # (e.g. asyncpg returns -1 when the conflict clause fires). Treat negative
    # as 0 so that `skipped` is always non-negative.
    inserted = result.rowcount if result.rowcount is not None and result.rowcount >= 0 else 0
    skipped = len(rows) - inserted

    log.info("trades_upserted", inserted=inserted, skipped=skipped)
    return {"inserted": inserted, "skipped": skipped}


# ---------------------------------------------------------------------------
# get_trades
# ---------------------------------------------------------------------------

async def get_trades(session: AsyncSession, filters: TradeFilters | None = None) -> list[Trade]:
    """필터 조건으로 Trade 목록 조회. 정렬: traded_at DESC."""
    if filters is None:
        filters = TradeFilters()

    stmt = select(Trade)

    if filters.symbol:
        stmt = stmt.where(Trade.symbol == filters.symbol)
    if filters.date_from:
        stmt = stmt.where(Trade.traded_at >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(Trade.traded_at <= filters.date_to)
    if filters.broker:
        stmt = stmt.where(Trade.broker == filters.broker)
    if filters.side:
        stmt = stmt.where(Trade.side == filters.side)
    if filters.account_type:
        stmt = stmt.where(Trade.account_type == filters.account_type)

    stmt = stmt.order_by(Trade.traded_at.desc())
    stmt = stmt.offset(filters.offset).limit(filters.limit)

    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# count_trades
# ---------------------------------------------------------------------------

async def count_trades(session: AsyncSession, filters: TradeFilters | None = None) -> int:
    """필터 조건에 맞는 Trade 총 건수 (페이지네이션 제외)."""
    if filters is None:
        filters = TradeFilters()

    stmt = select(func.count(Trade.id))

    if filters.symbol:
        stmt = stmt.where(Trade.symbol == filters.symbol)
    if filters.date_from:
        stmt = stmt.where(Trade.traded_at >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(Trade.traded_at <= filters.date_to)
    if filters.broker:
        stmt = stmt.where(Trade.broker == filters.broker)
    if filters.side:
        stmt = stmt.where(Trade.side == filters.side)
    if filters.account_type:
        stmt = stmt.where(Trade.account_type == filters.account_type)

    result = await session.execute(stmt)
    return result.scalar() or 0


async def get_trade(session: AsyncSession, trade_id: int) -> Trade | None:
    """ID로 Trade 단건 조회."""
    stmt = select(Trade).where(Trade.id == trade_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# update_trade_reason / update_trade_review
# ---------------------------------------------------------------------------

async def update_trade_reason(session: AsyncSession, trade_id: int, reason: str) -> Trade:
    """매매 이유 수정."""
    trade = await session.get(Trade, trade_id)
    if trade is None:
        raise ValueError(f"Trade {trade_id} not found")

    await session.execute(
        update(Trade).where(Trade.id == trade_id).values(reason=reason)
    )
    await session.commit()
    await session.refresh(trade)
    return trade


async def update_trade_review(session: AsyncSession, trade_id: int, review: str) -> Trade:
    """복기 메모 수정."""
    trade = await session.get(Trade, trade_id)
    if trade is None:
        raise ValueError(f"Trade {trade_id} not found")

    await session.execute(
        update(Trade).where(Trade.id == trade_id).values(review=review)
    )
    await session.commit()
    await session.refresh(trade)
    return trade


# ---------------------------------------------------------------------------
# get_trade_stats
# ---------------------------------------------------------------------------

async def get_trade_stats(session: AsyncSession, filters: TradeFilters | None = None) -> dict[str, Any]:
    """통계: 총 거래수, 매수/매도 건수, 총 금액, 종목별 거래 빈도."""
    if filters is None:
        filters = TradeFilters()

    def _apply_filters(stmt, exclude_side: bool = False):
        if filters.symbol:
            stmt = stmt.where(Trade.symbol == filters.symbol)
        if filters.date_from:
            stmt = stmt.where(Trade.traded_at >= filters.date_from)
        if filters.date_to:
            stmt = stmt.where(Trade.traded_at <= filters.date_to)
        if filters.broker:
            stmt = stmt.where(Trade.broker == filters.broker)
        if not exclude_side and filters.side:
            stmt = stmt.where(Trade.side == filters.side)
        if filters.account_type:
            stmt = stmt.where(Trade.account_type == filters.account_type)
        return stmt

    # 총 거래수, 총 금액
    agg_stmt = _apply_filters(
        select(
            func.count(Trade.id).label("total_count"),
            func.sum(Trade.amount).label("total_amount"),
        )
    )

    # 매수 건수 — side는 이미 "buy"로 고정하므로 filters.side 중복 적용 방지
    buy_stmt = _apply_filters(
        select(func.count(Trade.id)).where(Trade.side == "buy"),
        exclude_side=True,
    )
    # 매도 건수 — 동일 이유
    sell_stmt = _apply_filters(
        select(func.count(Trade.id)).where(Trade.side == "sell"),
        exclude_side=True,
    )

    total_result = await session.execute(agg_stmt)
    buy_result = await session.execute(buy_stmt)
    sell_result = await session.execute(sell_stmt)

    total_row = total_result.one()
    buy_count = buy_result.scalar() or 0
    sell_count = sell_result.scalar() or 0

    # 종목별 거래 빈도
    freq_stmt = _apply_filters(
        select(Trade.symbol, Trade.name, func.count(Trade.id).label("trade_count"))
        .group_by(Trade.symbol, Trade.name)
        .order_by(func.count(Trade.id).desc())
        .limit(20)
    )
    freq_result = await session.execute(freq_stmt)
    symbol_freq = [
        {"symbol": row.symbol, "name": row.name, "count": row.trade_count}
        for row in freq_result.all()
    ]

    return {
        "total_count": total_row.total_count or 0,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_amount": total_row.total_amount or 0,
        "symbol_frequency": symbol_freq,
    }


# ---------------------------------------------------------------------------
# get_chart_data
# ---------------------------------------------------------------------------

async def get_chart_data(
    session: AsyncSession,
    symbol: str,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
) -> list[Trade]:
    """특정 종목의 매매 내역 (차트 마커용). traded_at ASC."""
    stmt = select(Trade).where(Trade.symbol == symbol)

    if date_from:
        stmt = stmt.where(Trade.traded_at >= date_from)
    if date_to:
        stmt = stmt.where(Trade.traded_at <= date_to)

    stmt = stmt.order_by(Trade.traded_at.asc())

    result = await session.execute(stmt)
    return list(result.scalars().all())
