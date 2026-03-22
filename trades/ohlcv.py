"""pykrx OHLCV 수집 → price_cache 저장 모듈.

Public API
----------
fetch_ohlcv_for_symbol(session, symbol, from_date, to_date)
    단일 종목의 OHLCV를 수집해 price_cache에 upsert.

fetch_ohlcv_batch(session, symbols, from_date, to_date)
    여러 종목을 순서대로 수집. 종목당 30초 타임아웃, 실패 시 skip.

refresh_cached_symbols(session)
    price_cache에 이미 저장된 모든 종목의 최신 데이터를 오늘까지 추가 수집.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Sequence

import pandas as pd
import structlog
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PriceCache, Trade

log = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 30
_YEAR_DAYS = 365


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _call_pykrx(ticker: str, fromdate: str, todate: str) -> pd.DataFrame:
    """pykrx 동기 호출 — asyncio.to_thread 안에서 실행."""
    from pykrx import stock  # lazy import so tests can mock easily
    return stock.get_market_ohlcv_by_date(fromdate, todate, ticker)


async def _fetch_from_pykrx(
    ticker: str,
    from_date: date,
    to_date: date,
) -> pd.DataFrame | None:
    """pykrx를 비동기로 호출 (to_thread 래핑). 타임아웃/에러 시 None 반환."""
    fromdate_str = from_date.strftime("%Y%m%d")
    todate_str = to_date.strftime("%Y%m%d")
    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(_call_pykrx, ticker, fromdate_str, todate_str),
            timeout=_TIMEOUT_SECONDS,
        )
        return df
    except asyncio.TimeoutError:
        log.warning("ohlcv_timeout", symbol=ticker, timeout=_TIMEOUT_SECONDS)
        return None
    except Exception as exc:
        log.error("ohlcv_fetch_error", symbol=ticker, error=str(exc))
        return None


async def _get_cached_dates(session: AsyncSession, symbol: str) -> set[date]:
    """price_cache에서 해당 종목의 이미 저장된 날짜 집합을 반환."""
    stmt = select(PriceCache.date).where(PriceCache.symbol == symbol)
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}


async def _upsert_ohlcv_rows(
    session: AsyncSession,
    symbol: str,
    df: pd.DataFrame,
    skip_dates: set[date],
) -> int:
    """DataFrame 행을 price_cache에 INSERT (ON CONFLICT DO NOTHING).

    Returns
    -------
    inserted: int — 실제로 삽입된 행 수.
    """
    rows = []
    for idx, row in df.iterrows():
        row_date: date = idx.date() if hasattr(idx, "date") else idx
        if row_date in skip_dates:
            continue
        rows.append(
            {
                "symbol": symbol,
                "date": row_date,
                "open": row.get("시가", row.get("open")),
                "high": row.get("고가", row.get("high")),
                "low": row.get("저가", row.get("low")),
                "close": row.get("종가", row.get("close")),
                "volume": int(row.get("거래량", row.get("volume", 0))),
            }
        )

    if not rows:
        return 0

    stmt = insert(PriceCache).values(rows).on_conflict_do_nothing()
    result = await session.execute(stmt)
    inserted = result.rowcount if result.rowcount is not None and result.rowcount >= 0 else len(rows)
    log.info("ohlcv_upserted", symbol=symbol, inserted=inserted, total_rows=len(rows))
    return inserted


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_ohlcv_for_symbol(
    session: AsyncSession,
    symbol: str,
    from_date: date,
    to_date: date,
    *,
    skip_cache_check: bool = False,
) -> dict[str, int]:
    """단일 종목의 OHLCV를 수집해 price_cache에 저장.

    이미 캐시된 날짜는 skip. 타임아웃·에러 시 skip + 로깅.

    Parameters
    ----------
    skip_cache_check:
        True이면 _get_cached_dates 호출을 생략한다.
        호출자가 이미 겹치는 날짜가 없음을 보장할 때 사용.

    Returns
    -------
    {"fetched": N, "inserted": N, "skipped_dates": N}
    """
    cached = set() if skip_cache_check else await _get_cached_dates(session, symbol)

    df = await _fetch_from_pykrx(symbol, from_date, to_date)
    if df is None or df.empty:
        log.warning("ohlcv_empty_result", symbol=symbol, from_date=from_date, to_date=to_date)
        return {"fetched": 0, "inserted": 0, "skipped_dates": 0}

    # Count rows in this fetch that overlap with already-cached dates
    skipped_count = sum(
        1 for idx in df.index
        if (idx.date() if hasattr(idx, "date") else idx) in cached
    )
    inserted = await _upsert_ohlcv_rows(session, symbol, df, skip_dates=cached)
    return {
        "fetched": len(df),
        "inserted": inserted,
        "skipped_dates": skipped_count,
    }


async def fetch_ohlcv_batch(
    session: AsyncSession,
    symbols: list[str],
    from_date: date | None = None,
    to_date: date | None = None,
) -> dict[str, dict[str, int]]:
    """여러 종목의 OHLCV를 순서대로 수집.

    from_date 기본값: to_date 기준 1년 전.
    to_date 기본값: 오늘.

    각 종목당 30초 타임아웃. 실패 시 skip + 로깅.

    Returns
    -------
    {symbol: {"fetched": N, "inserted": N, "skipped_dates": N}, ...}
    """
    if to_date is None:
        to_date = date.today()
    if from_date is None:
        from_date = to_date - timedelta(days=_YEAR_DAYS)

    results: dict[str, dict[str, int]] = {}
    for symbol in symbols:
        log.info("ohlcv_batch_symbol", symbol=symbol, from_date=from_date, to_date=to_date)
        results[symbol] = await fetch_ohlcv_for_symbol(session, symbol, from_date, to_date)

    return results


async def refresh_cached_symbols(session: AsyncSession) -> dict[str, dict[str, int]]:
    """price_cache에 이미 존재하는 모든 종목의 최신 데이터를 오늘까지 추가 수집.

    각 종목의 마지막 캐시 날짜 다음 날부터 오늘까지만 수집하므로
    이미 있는 날짜는 재요청하지 않는다.

    Returns
    -------
    {symbol: {"fetched": N, "inserted": N, "skipped_dates": N}, ...}
    """
    # 종목별 최신 캐시 날짜 조회
    stmt = select(PriceCache.symbol, func.max(PriceCache.date).label("max_date")).group_by(
        PriceCache.symbol
    )
    result = await session.execute(stmt)
    rows = result.all()

    if not rows:
        log.info("refresh_cached_symbols_no_symbols")
        return {}

    today = date.today()
    results: dict[str, dict[str, int]] = {}

    for row in rows:
        symbol: str = row[0]
        last_date: date = row[1]
        next_date = last_date + timedelta(days=1)

        if next_date > today:
            log.info("ohlcv_already_up_to_date", symbol=symbol, last_date=last_date)
            results[symbol] = {"fetched": 0, "inserted": 0, "skipped_dates": 0}
            continue

        log.info("ohlcv_refresh_symbol", symbol=symbol, from_date=next_date, to_date=today)
        results[symbol] = await fetch_ohlcv_for_symbol(
            session, symbol, next_date, today, skip_cache_check=True
        )

    return results


async def get_earliest_trade_date(session: AsyncSession, symbol: str) -> date | None:
    """trades 테이블에서 종목의 가장 빠른 매매 날짜 반환 (없으면 None)."""
    stmt = select(func.min(Trade.traded_at)).where(Trade.symbol == symbol)
    result = await session.execute(stmt)
    earliest = result.scalar()
    if earliest is None:
        return None
    return earliest.date() if hasattr(earliest, "date") else earliest
