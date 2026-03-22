"""FIFO 매수-매도 매칭 및 평균단가 계산 모듈.

주요 기능:
1. FIFO 매칭 — 개별 거래 복기용 (match_trades_fifo)
2. 평균단가 계산 — 포지션 전체 손익 (calculate_avg_cost)
3. 전체 종목 일괄 매칭 (match_all_trades)
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Deque

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Trade, TradePair

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# AvgCostPosition — 평균단가 + 잔여 포지션
# ---------------------------------------------------------------------------

@dataclass
class AvgCostPosition:
    """종목별 평균단가 및 미실현 포지션."""
    symbol: str
    avg_cost: Decimal          # 평균 매수단가 (원/주)
    remaining_qty: int         # 잔여 수량
    open_lots: list[dict]      # [{"buy_trade_id": int, "qty": int, "price": Decimal}]


# ---------------------------------------------------------------------------
# 내부 헬퍼 — 매수 잔여 lot 관리
# ---------------------------------------------------------------------------

@dataclass
class _BuyLot:
    """FIFO 큐에서 관리되는 매수 lot."""
    trade_id: int
    price: Decimal
    total_qty: int
    remaining_qty: int
    fees: Decimal            # 매수 건 전체 수수료
    traded_at: datetime


def _fee_for_qty(lot: _BuyLot, qty_used: int) -> Decimal:
    """매수 수량 비례 수수료 배분."""
    if lot.total_qty == 0 or lot.fees == Decimal(0):
        return Decimal(0)
    allocated = lot.fees * Decimal(qty_used) / Decimal(lot.total_qty)
    return allocated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# match_trades_fifo
# ---------------------------------------------------------------------------

async def match_trades_fifo(symbol: str, session: AsyncSession) -> list[TradePair]:
    """종목의 모든 매매를 FIFO로 매칭하여 trade_pairs에 저장.

    - 재계산 시 기존 pairs 삭제 후 재생성 (idempotent).
    - 부분 매도 처리 지원.
    - 수익률 = (매도금액 - 매수금액 - 수수료배분) / 매수금액
    """
    # 1) 기존 pairs 삭제 (idempotent)
    buy_ids_stmt = select(Trade.id).where(
        Trade.symbol == symbol,
        Trade.side == "buy",
    )
    sell_ids_stmt = select(Trade.id).where(
        Trade.symbol == symbol,
        Trade.side == "sell",
    )
    buy_ids_result = await session.execute(buy_ids_stmt)
    sell_ids_result = await session.execute(sell_ids_stmt)
    buy_ids = [r for r, in buy_ids_result]
    sell_ids = [r for r, in sell_ids_result]

    if buy_ids or sell_ids:
        del_stmt = delete(TradePair).where(
            TradePair.buy_trade_id.in_(buy_ids) | TradePair.sell_trade_id.in_(sell_ids)
        )
        await session.execute(del_stmt)

    # 2) 종목 전체 거래 시간순 조회
    stmt = (
        select(Trade)
        .where(Trade.symbol == symbol)
        .order_by(Trade.traded_at.asc(), Trade.id.asc())
    )
    result = await session.execute(stmt)
    trades: list[Trade] = list(result.scalars().all())

    # 3) FIFO 큐 처리
    buy_queue: Deque[_BuyLot] = collections.deque()
    pairs: list[TradePair] = []

    for trade in trades:
        if trade.side == "buy":
            buy_queue.append(
                _BuyLot(
                    trade_id=trade.id,
                    price=trade.price,
                    total_qty=trade.quantity,
                    remaining_qty=trade.quantity,
                    fees=trade.fees or Decimal(0),
                    traded_at=trade.traded_at,
                )
            )
        elif trade.side == "sell":
            sell_qty_left = trade.quantity
            sell_fees = trade.fees or Decimal(0)
            sell_amount_total = trade.price * trade.quantity  # 매도 총금액

            while sell_qty_left > 0 and buy_queue:
                lot = buy_queue[0]
                matched_qty = min(lot.remaining_qty, sell_qty_left)

                # 매수금액 (매칭 수량 기준)
                buy_amount = lot.price * matched_qty
                # 매수 수수료 비례 배분
                buy_fee_alloc = _fee_for_qty(lot, matched_qty)
                # 매도 수수료: 매도 수량 비례
                if trade.quantity > 0:
                    sell_fee_alloc = (sell_fees * Decimal(matched_qty) / Decimal(trade.quantity)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                else:
                    sell_fee_alloc = Decimal(0)

                # 매도금액 (매칭 수량 기준)
                sell_amount = trade.price * matched_qty
                net_profit = sell_amount - buy_amount - buy_fee_alloc - sell_fee_alloc

                profit_rate = None
                if buy_amount > 0:
                    profit_rate = (net_profit / buy_amount).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    )

                holding_days = None
                if lot.traded_at and trade.traded_at:
                    delta = trade.traded_at - lot.traded_at
                    holding_days = delta.days

                pair = TradePair(
                    buy_trade_id=lot.trade_id,
                    sell_trade_id=trade.id,
                    profit_rate=profit_rate,
                    holding_days=holding_days,
                    matched_qty=matched_qty,
                    buy_amount=buy_amount,
                    sell_amount=sell_amount,
                    buy_fee=buy_fee_alloc,
                    sell_fee=sell_fee_alloc,
                )
                session.add(pair)
                pairs.append(pair)

                lot.remaining_qty -= matched_qty
                sell_qty_left -= matched_qty

                if lot.remaining_qty == 0:
                    buy_queue.popleft()

    await session.commit()
    log.info("fifo_match_done", symbol=symbol, pairs=len(pairs))
    return pairs


# ---------------------------------------------------------------------------
# calculate_avg_cost
# ---------------------------------------------------------------------------

async def calculate_avg_cost(symbol: str, session: AsyncSession) -> AvgCostPosition:
    """종목의 평균단가 + 미실현 포지션 계산. 저장 안 함.

    물타기(averaging down) 반영:
    avg = (기존수량×기존단가 + 추가수량×추가단가) / 총수량
    """
    stmt = (
        select(Trade)
        .where(Trade.symbol == symbol)
        .order_by(Trade.traded_at.asc(), Trade.id.asc())
    )
    result = await session.execute(stmt)
    trades: list[Trade] = list(result.scalars().all())

    buy_queue: Deque[_BuyLot] = collections.deque()

    for trade in trades:
        if trade.side == "buy":
            buy_queue.append(
                _BuyLot(
                    trade_id=trade.id,
                    price=trade.price,
                    total_qty=trade.quantity,
                    remaining_qty=trade.quantity,
                    fees=trade.fees or Decimal(0),
                    traded_at=trade.traded_at,
                )
            )
        elif trade.side == "sell":
            sell_qty_left = trade.quantity
            while sell_qty_left > 0 and buy_queue:
                lot = buy_queue[0]
                matched_qty = min(lot.remaining_qty, sell_qty_left)
                lot.remaining_qty -= matched_qty
                sell_qty_left -= matched_qty
                if lot.remaining_qty == 0:
                    buy_queue.popleft()

    # 잔여 lots로 평균단가 계산
    remaining_lots = [lot for lot in buy_queue if lot.remaining_qty > 0]
    total_qty = sum(lot.remaining_qty for lot in remaining_lots)

    if total_qty == 0:
        avg_cost = Decimal(0)
    else:
        weighted_sum = sum(lot.price * lot.remaining_qty for lot in remaining_lots)
        avg_cost = (weighted_sum / total_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    open_lots = [
        {"buy_trade_id": lot.trade_id, "qty": lot.remaining_qty, "price": lot.price}
        for lot in remaining_lots
    ]

    return AvgCostPosition(
        symbol=symbol,
        avg_cost=avg_cost,
        remaining_qty=total_qty,
        open_lots=open_lots,
    )


# ---------------------------------------------------------------------------
# match_all_trades
# ---------------------------------------------------------------------------

async def match_all_trades(session: AsyncSession) -> dict[str, int]:
    """전체 종목 FIFO 매칭.

    Returns
    -------
    dict[symbol, pair_count]
        성공한 종목은 pair 수, 실패한 종목은 -1로 기록됨.
    """
    # 종목 목록 조회
    stmt = select(Trade.symbol).distinct()
    result = await session.execute(stmt)
    symbols: list[str] = [row for row, in result]

    summary: dict[str, int] = {}
    failed: list[str] = []
    for symbol in symbols:
        try:
            pairs = await match_trades_fifo(symbol, session)
            summary[symbol] = len(pairs)
            log.info("match_all_progress", symbol=symbol, pairs=len(pairs))
        except Exception as exc:
            await session.rollback()
            log.error("match_all_error", symbol=symbol, error=str(exc))
            failed.append(symbol)
            summary[symbol] = -1
            continue

    if failed:
        log.warning("match_all_failed_symbols", failed=failed, count=len(failed))

    return summary
