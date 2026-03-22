"""Unit tests for trades/pairing.py.

모든 테스트는 AsyncMock 세션을 사용 — 실제 DB 불필요.

커버리지:
- FIFO 기본 매칭 (1매수 → 1매도)
- 부분 매도 (1매수 → 2매도)
- 물타기 (2매수 → 1매도)
- 평균단가 계산 정확성
- 수수료 배분 검증
- 매도 없는 종목 (미실현 포지션만)
- 재계산 시 기존 pairs 삭제 후 재생성 (idempotent)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from db.models import Trade, TradePair
from trades.pairing import (
    AvgCostPosition,
    _BuyLot,
    _fee_for_qty,
    calculate_avg_cost,
    match_all_trades,
    match_trades_fifo,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_BASE_DT = datetime(2024, 1, 10, 9, 30, 0, tzinfo=timezone.utc)


def _make_trade(
    id: int,
    symbol: str = "005930",
    name: str = "삼성전자",
    side: str = "buy",
    price: float = 70_000,
    quantity: int = 100,
    traded_at: datetime | None = None,
    fees: float | None = None,
    broker: str = "kiwoom",
    account_type: str = "개인",
    market: str = "KOSPI",
) -> Trade:
    t = MagicMock(spec=Trade)
    t.id = id
    t.symbol = symbol
    t.name = name
    t.side = side
    t.price = Decimal(str(price))
    t.quantity = quantity
    t.amount = Decimal(str(price)) * quantity
    t.traded_at = traded_at or _BASE_DT
    t.fees = Decimal(str(fees)) if fees is not None else None
    t.broker = broker
    t.account_type = account_type
    t.market = market
    return t


def _mock_session(trades: list[Trade]) -> MagicMock:
    """AsyncSession mock. execute는 항상 trades를 반환."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.add = MagicMock()

    # scalars() 결과를 trades로 설정
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=trades)

    # buy_ids / sell_ids 쿼리는 빈 결과로
    empty_result = MagicMock()
    empty_result.__iter__ = MagicMock(return_value=iter([]))

    # execute는 여러 번 호출될 수 있음 (buy_ids, sell_ids, delete, main query)
    # 순서: 1=buy_ids, 2=sell_ids, 3=delete, 4=main select
    call_count = [0]

    async def fake_execute(stmt, *args, **kwargs):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] <= 2:
            # buy_ids or sell_ids — row iterator yielding (id,) tuples
            ids = [t.id for t in trades if t.side == ("buy" if call_count[0] == 1 else "sell")]
            result.__iter__ = MagicMock(return_value=iter([(i,) for i in ids]))
        elif call_count[0] == 3:
            # delete statement — nothing to return
            pass
        else:
            # main select
            result.scalars = MagicMock(return_value=scalars_mock)
        return result

    session.execute = fake_execute
    return session


def _mock_session_for_avg(trades: list[Trade]) -> MagicMock:
    """calculate_avg_cost용 세션 mock (execute 1번만)."""
    session = MagicMock()
    session.commit = AsyncMock()

    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=trades)

    async def fake_execute(stmt, *args, **kwargs):
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars_mock)
        return result

    session.execute = fake_execute
    return session


# ---------------------------------------------------------------------------
# _fee_for_qty 단위 테스트
# ---------------------------------------------------------------------------

class TestFeeForQty:
    def _lot(self, total_qty: int, fees: float) -> _BuyLot:
        return _BuyLot(
            trade_id=1,
            price=Decimal("70000"),
            total_qty=total_qty,
            remaining_qty=total_qty,
            fees=Decimal(str(fees)),
            traded_at=_BASE_DT,
        )

    def test_full_qty_returns_full_fee(self):
        lot = self._lot(100, 100.00)
        assert _fee_for_qty(lot, 100) == Decimal("100.00")

    def test_half_qty_returns_half_fee(self):
        lot = self._lot(100, 100.00)
        assert _fee_for_qty(lot, 50) == Decimal("50.00")

    def test_zero_fees_returns_zero(self):
        lot = self._lot(100, 0.0)
        assert _fee_for_qty(lot, 100) == Decimal("0")

    def test_zero_total_qty_returns_zero(self):
        lot = _BuyLot(
            trade_id=1, price=Decimal("70000"),
            total_qty=0, remaining_qty=0,
            fees=Decimal("100"), traded_at=_BASE_DT,
        )
        assert _fee_for_qty(lot, 0) == Decimal(0)

    def test_proportional_rounding(self):
        """1/3 수량 사용 시 반올림 처리."""
        lot = self._lot(3, 10.00)
        result = _fee_for_qty(lot, 1)
        # 10/3 = 3.333... → 3.33
        assert result == Decimal("3.33")


# ---------------------------------------------------------------------------
# match_trades_fifo
# ---------------------------------------------------------------------------

class TestMatchTradesFifo:

    @pytest.mark.asyncio
    async def test_basic_single_buy_single_sell(self):
        """1매수 → 1매도 기본 케이스."""
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100)
        sell = _make_trade(
            id=2, side="sell", price=73_000, quantity=100,
            traded_at=_BASE_DT + timedelta(days=5),
        )
        session = _mock_session([buy, sell])
        pairs = await match_trades_fifo("005930", session)

        assert len(pairs) == 1
        p = pairs[0]
        assert p.buy_trade_id == 1
        assert p.sell_trade_id == 2
        # profit_rate = (73000 - 70000) / 70000 = 0.0429 (반올림)
        expected_rate = (Decimal("73000") - Decimal("70000")) / Decimal("70000")
        expected_rate = expected_rate.quantize(Decimal("0.0001"))
        assert p.profit_rate == expected_rate
        assert p.holding_days == 5
        # audit columns
        assert p.matched_qty == 100
        assert p.buy_amount == Decimal("70000") * 100
        assert p.sell_amount == Decimal("73000") * 100
        assert p.buy_fee == Decimal("0")
        assert p.sell_fee == Decimal("0")

    @pytest.mark.asyncio
    async def test_partial_sell_creates_two_pairs(self):
        """1매수 → 2매도 부분 매도."""
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100)
        sell1 = _make_trade(
            id=2, side="sell", price=73_000, quantity=50,
            traded_at=_BASE_DT + timedelta(days=3),
        )
        sell2 = _make_trade(
            id=3, side="sell", price=68_000, quantity=50,
            traded_at=_BASE_DT + timedelta(days=7),
        )
        session = _mock_session([buy, sell1, sell2])
        pairs = await match_trades_fifo("005930", session)

        assert len(pairs) == 2

        p1 = pairs[0]
        assert p1.buy_trade_id == 1
        assert p1.sell_trade_id == 2
        # profit_rate = (73000 - 70000) / 70000 = +0.0429
        rate1 = ((Decimal("73000") - Decimal("70000")) / Decimal("70000")).quantize(Decimal("0.0001"))
        assert p1.profit_rate == rate1
        assert p1.matched_qty == 50
        assert p1.buy_amount == Decimal("70000") * 50
        assert p1.sell_amount == Decimal("73000") * 50

        p2 = pairs[1]
        assert p2.buy_trade_id == 1
        assert p2.sell_trade_id == 3
        # profit_rate = (68000 - 70000) / 70000 = -0.0286
        rate2 = ((Decimal("68000") - Decimal("70000")) / Decimal("70000")).quantize(Decimal("0.0001"))
        assert p2.profit_rate == rate2
        assert p2.matched_qty == 50
        assert p2.buy_amount == Decimal("70000") * 50
        assert p2.sell_amount == Decimal("68000") * 50

    @pytest.mark.asyncio
    async def test_multiple_buys_fifo_order(self):
        """2매수 → 1매도: FIFO이므로 첫 번째 매수부터 소진."""
        buy1 = _make_trade(id=1, side="buy", price=70_000, quantity=50,
                           traded_at=_BASE_DT)
        buy2 = _make_trade(id=2, side="buy", price=65_000, quantity=50,
                           traded_at=_BASE_DT + timedelta(days=2))
        sell = _make_trade(id=3, side="sell", price=72_000, quantity=50,
                           traded_at=_BASE_DT + timedelta(days=5))
        session = _mock_session([buy1, buy2, sell])
        pairs = await match_trades_fifo("005930", session)

        assert len(pairs) == 1
        p = pairs[0]
        # 첫 번째 매수(id=1)가 소진되어야 함
        assert p.buy_trade_id == 1
        assert p.sell_trade_id == 3

    @pytest.mark.asyncio
    async def test_averaging_down_two_buys_one_sell(self):
        """물타기: 2매수 → 1매도 (전량 매도, FIFO이므로 첫 번째 lot만 매칭)."""
        buy1 = _make_trade(id=1, side="buy", price=80_000, quantity=50,
                           traded_at=_BASE_DT)
        buy2 = _make_trade(id=2, side="buy", price=70_000, quantity=50,
                           traded_at=_BASE_DT + timedelta(days=1))
        sell = _make_trade(id=3, side="sell", price=75_000, quantity=100,
                           traded_at=_BASE_DT + timedelta(days=10))
        session = _mock_session([buy1, buy2, sell])
        pairs = await match_trades_fifo("005930", session)

        # FIFO: buy1(50주) + buy2(50주) 각각 pair
        assert len(pairs) == 2
        assert pairs[0].buy_trade_id == 1
        assert pairs[1].buy_trade_id == 2
        assert pairs[0].sell_trade_id == 3
        assert pairs[1].sell_trade_id == 3

    @pytest.mark.asyncio
    async def test_no_sell_returns_empty_pairs(self):
        """매도 없으면 pairs 없음."""
        buy1 = _make_trade(id=1, side="buy", price=70_000, quantity=100)
        buy2 = _make_trade(id=2, side="buy", price=68_000, quantity=50,
                           traded_at=_BASE_DT + timedelta(days=3))
        session = _mock_session([buy1, buy2])
        pairs = await match_trades_fifo("005930", session)

        assert pairs == []

    @pytest.mark.asyncio
    async def test_fee_allocation_buy_proportional(self):
        """매수 수수료는 수량 비례 배분된다."""
        # 매수 100주 @ 70,000, 수수료 100원
        # 매도 50주 @ 73,000, 수수료 없음
        # buy_fee_alloc = 100 * 50/100 = 50
        # profit = (73000*50 - 70000*50 - 50) / (70000*50) = (150000-50)/3500000
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100, fees=100.0)
        sell = _make_trade(id=2, side="sell", price=73_000, quantity=50,
                           traded_at=_BASE_DT + timedelta(days=1), fees=None)
        session = _mock_session([buy, sell])
        pairs = await match_trades_fifo("005930", session)

        assert len(pairs) == 1
        p = pairs[0]

        buy_amount = Decimal("70000") * 50
        sell_amount = Decimal("73000") * 50
        buy_fee_alloc = Decimal("50.00")  # 100 * 50/100
        net = sell_amount - buy_amount - buy_fee_alloc
        expected_rate = (net / buy_amount).quantize(Decimal("0.0001"))
        assert p.profit_rate == expected_rate
        # audit columns
        assert p.matched_qty == 50
        assert p.buy_amount == buy_amount
        assert p.sell_amount == sell_amount
        assert p.buy_fee == buy_fee_alloc
        assert p.sell_fee == Decimal("0")

    @pytest.mark.asyncio
    async def test_fee_allocation_sell_proportional(self):
        """매도 수수료는 해당 pair의 매칭 수량 비례로 배분된다."""
        # 매수 100주 @ 70,000, 수수료 없음
        # 매도 100주 @ 73,000, 수수료 200원
        # sell_fee_alloc = 200 * 100/100 = 200
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100, fees=None)
        sell = _make_trade(id=2, side="sell", price=73_000, quantity=100,
                           traded_at=_BASE_DT + timedelta(days=1), fees=200.0)
        session = _mock_session([buy, sell])
        pairs = await match_trades_fifo("005930", session)

        assert len(pairs) == 1
        p = pairs[0]

        buy_amount = Decimal("70000") * 100
        sell_amount = Decimal("73000") * 100
        sell_fee_alloc = Decimal("200.00")
        net = sell_amount - buy_amount - sell_fee_alloc
        expected_rate = (net / buy_amount).quantize(Decimal("0.0001"))
        assert p.profit_rate == expected_rate
        # audit columns
        assert p.matched_qty == 100
        assert p.buy_amount == buy_amount
        assert p.sell_amount == sell_amount
        assert p.buy_fee == Decimal("0")
        assert p.sell_fee == sell_fee_alloc

    @pytest.mark.asyncio
    async def test_sell_fee_partial_allocation(self):
        """매도 수수료 부분 배분: 50주 매도(수수료 100), 25주 매칭 → 50원 배분."""
        # 매수 25주
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=25, fees=None)
        # 매도 50주 @ 73,000, 수수료 100원 (25주만 매칭됨)
        sell = _make_trade(id=2, side="sell", price=73_000, quantity=50,
                           traded_at=_BASE_DT + timedelta(days=1), fees=100.0)
        session = _mock_session([buy, sell])
        pairs = await match_trades_fifo("005930", session)

        # 매수 25주만 있으므로 25주만 매칭
        assert len(pairs) == 1
        p = pairs[0]

        buy_amount = Decimal("70000") * 25
        sell_amount = Decimal("73000") * 25
        sell_fee_alloc = (Decimal("100") * Decimal("25") / Decimal("50")).quantize(Decimal("0.01"))
        net = sell_amount - buy_amount - sell_fee_alloc
        expected_rate = (net / buy_amount).quantize(Decimal("0.0001"))
        assert p.profit_rate == expected_rate

    @pytest.mark.asyncio
    async def test_holding_days_calculated(self):
        """holding_days = 매도일 - 매수일."""
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100,
                          traded_at=_BASE_DT)
        sell = _make_trade(id=2, side="sell", price=73_000, quantity=100,
                           traded_at=_BASE_DT + timedelta(days=15))
        session = _mock_session([buy, sell])
        pairs = await match_trades_fifo("005930", session)

        assert pairs[0].holding_days == 15

    @pytest.mark.asyncio
    async def test_idempotent_deletes_existing_pairs(self):
        """재계산 시 delete가 호출되는지 검증 (execute 호출 확인)."""
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100)
        sell = _make_trade(id=2, side="sell", price=73_000, quantity=100,
                           traded_at=_BASE_DT + timedelta(days=5))

        execute_calls = []

        scalars_mock = MagicMock()
        scalars_mock.all = MagicMock(return_value=[buy, sell])

        call_count = [0]

        async def tracking_execute(stmt, *args, **kwargs):
            call_count[0] += 1
            execute_calls.append(call_count[0])
            result = MagicMock()
            if call_count[0] == 1:
                # buy ids
                result.__iter__ = MagicMock(return_value=iter([(1,)]))
            elif call_count[0] == 2:
                # sell ids
                result.__iter__ = MagicMock(return_value=iter([(2,)]))
            elif call_count[0] == 3:
                # delete
                pass
            else:
                result.scalars = MagicMock(return_value=scalars_mock)
            return result

        session = MagicMock()
        session.execute = tracking_execute
        session.commit = AsyncMock()
        session.add = MagicMock()

        await match_trades_fifo("005930", session)

        # delete stmt가 호출되었어야 함 (execute 3번째 호출)
        assert call_count[0] >= 4, "delete + main query 포함 최소 4번 execute 호출 필요"

    @pytest.mark.asyncio
    async def test_commit_called(self):
        """match_trades_fifo는 반드시 commit을 호출해야 한다."""
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100)
        session = _mock_session([buy])
        await match_trades_fifo("005930", session)
        session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_profit_rate_none_when_buy_amount_zero(self):
        """매수금액이 0이면 profit_rate는 None."""
        buy = _make_trade(id=1, side="buy", price=0, quantity=100)
        sell = _make_trade(id=2, side="sell", price=73_000, quantity=100,
                           traded_at=_BASE_DT + timedelta(days=1))
        session = _mock_session([buy, sell])
        pairs = await match_trades_fifo("005930", session)

        assert pairs[0].profit_rate is None

    @pytest.mark.asyncio
    async def test_empty_trades_returns_empty(self):
        """거래 없으면 빈 리스트."""
        session = _mock_session([])
        pairs = await match_trades_fifo("005930", session)
        assert pairs == []

    @pytest.mark.asyncio
    async def test_oversell_creates_one_pair_ignores_excess(self):
        """매도 수량 > 매수 수량: 매수 수량만큼만 pair 생성, 초과 매도 무시."""
        # Buy 50 shares @ 70,000
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=50,
                          traded_at=_BASE_DT)
        # Sell 100 shares @ 80,000 — only 50 can be matched
        sell = _make_trade(id=2, side="sell", price=80_000, quantity=100,
                           traded_at=_BASE_DT + timedelta(days=3))
        session = _mock_session([buy, sell])
        pairs = await match_trades_fifo("005930", session)

        # Only 1 pair for the 50 shares that were bought; remaining 50 sell qty silently ignored
        assert len(pairs) == 1
        p = pairs[0]
        assert p.buy_trade_id == 1
        assert p.sell_trade_id == 2

        # profit_rate = (80000 - 70000) / 70000
        expected_rate = ((Decimal("80000") - Decimal("70000")) / Decimal("70000")).quantize(
            Decimal("0.0001")
        )
        assert p.profit_rate == expected_rate


# ---------------------------------------------------------------------------
# calculate_avg_cost
# ---------------------------------------------------------------------------

class TestCalculateAvgCost:

    @pytest.mark.asyncio
    async def test_single_buy_no_sell(self):
        """매도 없으면 avg_cost = 매수단가."""
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100)
        session = _mock_session_for_avg([buy])
        pos = await calculate_avg_cost("005930", session)

        assert pos.symbol == "005930"
        assert pos.remaining_qty == 100
        assert pos.avg_cost == Decimal("70000.00")
        assert len(pos.open_lots) == 1
        assert pos.open_lots[0]["buy_trade_id"] == 1
        assert pos.open_lots[0]["qty"] == 100

    @pytest.mark.asyncio
    async def test_averaging_down_two_buys(self):
        """물타기: 2매수 평균단가."""
        buy1 = _make_trade(id=1, side="buy", price=80_000, quantity=100,
                           traded_at=_BASE_DT)
        buy2 = _make_trade(id=2, side="buy", price=60_000, quantity=100,
                           traded_at=_BASE_DT + timedelta(days=5))
        session = _mock_session_for_avg([buy1, buy2])
        pos = await calculate_avg_cost("005930", session)

        # avg = (80000*100 + 60000*100) / 200 = 70000
        assert pos.remaining_qty == 200
        assert pos.avg_cost == Decimal("70000.00")
        assert len(pos.open_lots) == 2

    @pytest.mark.asyncio
    async def test_partial_sell_reduces_remaining(self):
        """부분 매도 후 잔량 평균단가."""
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100)
        sell = _make_trade(id=2, side="sell", price=73_000, quantity=60,
                           traded_at=_BASE_DT + timedelta(days=5))
        session = _mock_session_for_avg([buy, sell])
        pos = await calculate_avg_cost("005930", session)

        assert pos.remaining_qty == 40
        assert pos.avg_cost == Decimal("70000.00")
        assert pos.open_lots[0]["qty"] == 40

    @pytest.mark.asyncio
    async def test_full_sell_returns_zero_remaining(self):
        """전량 매도 후 잔량 = 0, avg_cost = 0."""
        buy = _make_trade(id=1, side="buy", price=70_000, quantity=100)
        sell = _make_trade(id=2, side="sell", price=73_000, quantity=100,
                           traded_at=_BASE_DT + timedelta(days=5))
        session = _mock_session_for_avg([buy, sell])
        pos = await calculate_avg_cost("005930", session)

        assert pos.remaining_qty == 0
        assert pos.avg_cost == Decimal(0)
        assert pos.open_lots == []

    @pytest.mark.asyncio
    async def test_averaging_down_partial_sell(self):
        """물타기 후 부분 매도 — FIFO로 첫 lot부터 소진."""
        buy1 = _make_trade(id=1, side="buy", price=80_000, quantity=100,
                           traded_at=_BASE_DT)
        buy2 = _make_trade(id=2, side="buy", price=70_000, quantity=100,
                           traded_at=_BASE_DT + timedelta(days=3))
        # 첫 lot 전체 + 두 번째 lot 일부 소진
        sell = _make_trade(id=3, side="sell", price=75_000, quantity=150,
                           traded_at=_BASE_DT + timedelta(days=10))
        session = _mock_session_for_avg([buy1, buy2, sell])
        pos = await calculate_avg_cost("005930", session)

        # buy1 100주 전량 소진, buy2에서 50주 소진 → 50주 잔량
        assert pos.remaining_qty == 50
        assert pos.avg_cost == Decimal("70000.00")  # buy2 잔량만 남음
        assert pos.open_lots[0]["buy_trade_id"] == 2
        assert pos.open_lots[0]["qty"] == 50

    @pytest.mark.asyncio
    async def test_unequal_buy_qty_avg_cost(self):
        """서로 다른 수량의 2매수 평균단가."""
        buy1 = _make_trade(id=1, side="buy", price=90_000, quantity=30,
                           traded_at=_BASE_DT)
        buy2 = _make_trade(id=2, side="buy", price=60_000, quantity=70,
                           traded_at=_BASE_DT + timedelta(days=1))
        session = _mock_session_for_avg([buy1, buy2])
        pos = await calculate_avg_cost("005930", session)

        # avg = (90000*30 + 60000*70) / 100 = (2700000 + 4200000) / 100 = 69000
        assert pos.remaining_qty == 100
        assert pos.avg_cost == Decimal("69000.00")

    @pytest.mark.asyncio
    async def test_no_trades_returns_zero_position(self):
        """거래 없으면 잔량 0, 평균단가 0."""
        session = _mock_session_for_avg([])
        pos = await calculate_avg_cost("005930", session)

        assert pos.remaining_qty == 0
        assert pos.avg_cost == Decimal(0)
        assert pos.open_lots == []

    @pytest.mark.asyncio
    async def test_open_lots_structure(self):
        """open_lots는 buy_trade_id, qty, price 키를 가져야 한다."""
        buy = _make_trade(id=5, side="buy", price=70_000, quantity=100)
        session = _mock_session_for_avg([buy])
        pos = await calculate_avg_cost("005930", session)

        lot = pos.open_lots[0]
        assert "buy_trade_id" in lot
        assert "qty" in lot
        assert "price" in lot
        assert lot["buy_trade_id"] == 5
        assert lot["qty"] == 100
        assert lot["price"] == Decimal("70000")


# ---------------------------------------------------------------------------
# match_all_trades
# ---------------------------------------------------------------------------

class TestMatchAllTrades:

    @pytest.mark.asyncio
    async def test_returns_symbol_pair_count_dict(self):
        """match_all_trades는 {symbol: pair_count} dict를 반환한다."""
        # 종목 목록 조회 + 각 종목 처리 시뮬레이션
        symbols_result = MagicMock()
        symbols_result.__iter__ = MagicMock(return_value=iter([("005930",), ("000660",)]))

        call_count = [0]
        buy_005 = _make_trade(id=1, symbol="005930", side="buy", price=70_000, quantity=100)
        sell_005 = _make_trade(id=2, symbol="005930", side="sell", price=73_000, quantity=100,
                               traded_at=_BASE_DT + timedelta(days=5))
        buy_660 = _make_trade(id=3, symbol="000660", name="SK하이닉스", side="buy",
                              price=130_000, quantity=50)

        async def fake_execute(stmt, *args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            # 첫 번째 호출: distinct symbols
            if call_count[0] == 1:
                result.__iter__ = MagicMock(return_value=iter([("005930",), ("000660",)]))
            elif call_count[0] == 2:
                # 005930 buy_ids
                result.__iter__ = MagicMock(return_value=iter([(1,)]))
            elif call_count[0] == 3:
                # 005930 sell_ids
                result.__iter__ = MagicMock(return_value=iter([(2,)]))
            elif call_count[0] == 4:
                # 005930 delete
                pass
            elif call_count[0] == 5:
                # 005930 main select
                scalars_mock = MagicMock()
                scalars_mock.all = MagicMock(return_value=[buy_005, sell_005])
                result.scalars = MagicMock(return_value=scalars_mock)
            elif call_count[0] == 6:
                # 000660 buy_ids
                result.__iter__ = MagicMock(return_value=iter([(3,)]))
            elif call_count[0] == 7:
                # 000660 sell_ids
                result.__iter__ = MagicMock(return_value=iter([]))
            elif call_count[0] == 8:
                # 000660 delete
                pass
            else:
                # 000660 main select
                scalars_mock = MagicMock()
                scalars_mock.all = MagicMock(return_value=[buy_660])
                result.scalars = MagicMock(return_value=scalars_mock)
            return result

        session = MagicMock()
        session.execute = fake_execute
        session.commit = AsyncMock()
        session.add = MagicMock()

        summary = await match_all_trades(session)

        assert "005930" in summary
        assert "000660" in summary
        assert summary["005930"] == 1  # 1 pair (1매수 1매도)
        assert summary["000660"] == 0  # 매도 없음

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_dict(self):
        """종목 없으면 빈 dict."""
        async def fake_execute(stmt, *args, **kwargs):
            result = MagicMock()
            result.__iter__ = MagicMock(return_value=iter([]))
            return result

        session = MagicMock()
        session.execute = fake_execute
        session.commit = AsyncMock()

        summary = await match_all_trades(session)
        assert summary == {}

    @pytest.mark.asyncio
    async def test_error_in_one_symbol_continues_to_next(self):
        """한 종목에서 예외 발생 시 rollback 후 나머지 종목 처리 계속."""
        call_count = [0]
        buy_good = _make_trade(id=3, symbol="000660", name="SK하이닉스", side="buy",
                               price=130_000, quantity=50)

        async def fake_execute(stmt, *args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # distinct symbols: 005930 (will fail) + 000660 (will succeed)
                result.__iter__ = MagicMock(return_value=iter([("005930",), ("000660",)]))
            elif call_count[0] == 2:
                # 005930 buy_ids — raise to simulate failure
                raise RuntimeError("DB error for 005930")
            elif call_count[0] == 3:
                # 000660 buy_ids
                result.__iter__ = MagicMock(return_value=iter([(3,)]))
            elif call_count[0] == 4:
                # 000660 sell_ids
                result.__iter__ = MagicMock(return_value=iter([]))
            elif call_count[0] == 5:
                # 000660 delete (no buy+sell ids actually triggers delete only if ids exist,
                # but mock still counts the call)
                pass
            else:
                # 000660 main select
                scalars_mock = MagicMock()
                scalars_mock.all = MagicMock(return_value=[buy_good])
                result.scalars = MagicMock(return_value=scalars_mock)
            return result

        session = MagicMock()
        session.execute = fake_execute
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.add = MagicMock()

        summary = await match_all_trades(session)

        # Failed symbol recorded as -1
        assert summary.get("005930") == -1
        # rollback was called once for the failed symbol
        session.rollback.assert_called_once()
        # Successful symbol still processed
        assert "000660" in summary
        assert summary["000660"] == 0  # no sells


# ---------------------------------------------------------------------------
# AvgCostPosition 데이터클래스
# ---------------------------------------------------------------------------

class TestAvgCostPosition:
    def test_fields(self):
        pos = AvgCostPosition(
            symbol="005930",
            avg_cost=Decimal("70000.00"),
            remaining_qty=100,
            open_lots=[{"buy_trade_id": 1, "qty": 100, "price": Decimal("70000")}],
        )
        assert pos.symbol == "005930"
        assert pos.avg_cost == Decimal("70000.00")
        assert pos.remaining_qty == 100
        assert len(pos.open_lots) == 1
