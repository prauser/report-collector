"""Unit tests for trades/trade_repo.py.

All tests use mocked AsyncSession — no live DB required.
PostgreSQL dialect-specific behaviour (ON CONFLICT DO NOTHING) is tested
by verifying the compiled SQL string contains the expected clause.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Trade
from trades.csv_parsers.common import TradeRow
from trades.trade_repo import (
    TradeFilters,
    count_trades,
    get_chart_data,
    get_trade,
    get_trade_stats,
    get_trades,
    update_trade_reason,
    update_trade_review,
    upsert_trades,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_row(
    symbol: str = "005930",
    name: str = "삼성전자",
    side: str = "buy",
    traded_at: datetime | None = None,
    price: Decimal = Decimal("70000"),
    quantity: int = 10,
    amount: Decimal = Decimal("700000"),
    broker: str = "kiwoom",
    account_type: str = "개인",
    market: str = "KOSPI",
    fees: Decimal | None = None,
) -> TradeRow:
    if traded_at is None:
        traded_at = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
    return TradeRow(
        symbol=symbol,
        name=name,
        side=side,
        traded_at=traded_at,
        price=price,
        quantity=quantity,
        amount=amount,
        broker=broker,
        account_type=account_type,
        market=market,
        fees=fees,
    )


def _mock_session() -> MagicMock:
    """Return a MagicMock that behaves like AsyncSession."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _mock_execute_result(rowcount: int = 1, scalar_value=None, scalars_list=None, rows=None):
    """Build a fake SQLAlchemy execute result."""
    result = MagicMock()
    result.rowcount = rowcount
    result.scalar = MagicMock(return_value=scalar_value)
    result.scalar_one_or_none = MagicMock(return_value=scalar_value)
    if scalars_list is not None:
        scalars_mock = MagicMock()
        scalars_mock.all = MagicMock(return_value=scalars_list)
        result.scalars = MagicMock(return_value=scalars_mock)
    if rows is not None:
        result.one = MagicMock(return_value=rows)
        result.all = MagicMock(return_value=rows)
    return result


# ---------------------------------------------------------------------------
# upsert_trades
# ---------------------------------------------------------------------------

class TestUpsertTrades:
    @pytest.mark.asyncio
    async def test_empty_list_returns_zeros(self):
        session = _mock_session()
        result = await upsert_trades(session, [])
        assert result == {"inserted": 0, "skipped": 0}
        session.execute.assert_not_called()
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_row_inserted(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(rowcount=1)

        rows = [_make_row()]
        result = await upsert_trades(session, rows)

        assert result["inserted"] == 1
        assert result["skipped"] == 0
        session.execute.assert_called_once()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_duplicate_row_skipped(self):
        """rowcount=0 means ON CONFLICT DO NOTHING fired — row skipped."""
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(rowcount=0)

        rows = [_make_row()]
        result = await upsert_trades(session, rows)

        assert result["inserted"] == 0
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    async def test_mixed_insert_and_skip(self):
        """2 rows sent, 1 inserted, 1 conflict."""
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(rowcount=1)

        rows = [_make_row(), _make_row(symbol="000660", name="SK하이닉스")]
        result = await upsert_trades(session, rows)

        assert result["inserted"] == 1
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    async def test_upsert_stmt_uses_conflict_clause(self):
        """Verify the compiled SQL contains ON CONFLICT DO NOTHING."""
        captured_stmt = {}

        async def capture_execute(stmt, *args, **kwargs):
            captured_stmt["stmt"] = stmt
            return _mock_execute_result(rowcount=1)

        session = _mock_session()
        session.execute = capture_execute

        rows = [_make_row()]
        await upsert_trades(session, rows)

        stmt = captured_stmt["stmt"]
        # Compile with PostgreSQL dialect to get the ON CONFLICT clause
        from sqlalchemy.dialects import postgresql
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "ON CONFLICT" in sql.upper()
        assert "DO NOTHING" in sql.upper()

    @pytest.mark.asyncio
    async def test_upsert_conflict_target_is_uq_trade_dedup(self):
        """Verify the conflict target constraint name is correct."""
        captured_stmt = {}

        async def capture_execute(stmt, *args, **kwargs):
            captured_stmt["stmt"] = stmt
            return _mock_execute_result(rowcount=1)

        session = _mock_session()
        session.execute = capture_execute

        rows = [_make_row()]
        await upsert_trades(session, rows)

        stmt = captured_stmt["stmt"]
        from sqlalchemy.dialects import postgresql
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "uq_trade_dedup" in sql


# ---------------------------------------------------------------------------
# get_trades
# ---------------------------------------------------------------------------

class TestGetTrades:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        trade = MagicMock(spec=Trade)
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_list=[trade])

        result = await get_trades(session)
        assert result == [trade]

    @pytest.mark.asyncio
    async def test_empty_filters_no_where_clauses(self):
        """With empty filters, the query should not raise and returns results."""
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_list=[])

        result = await get_trades(session, TradeFilters())
        assert result == []
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_symbol_filter_applied(self):
        """The executed statement should filter on symbol."""
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            return _mock_execute_result(scalars_list=[])

        session = _mock_session()
        session.execute = capture

        await get_trades(session, TradeFilters(symbol="005930"))
        stmt = captured["stmt"]
        # Verify WHERE clause contains symbol comparison (check bind params)
        from sqlalchemy.dialects import postgresql
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        params = compiled.params
        assert "trades.symbol" in sql or "symbol" in sql
        assert any("005930" in str(v) for v in params.values())

    @pytest.mark.asyncio
    async def test_pagination_offset_limit(self):
        """Offset and limit should appear in compiled SQL bind params."""
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            return _mock_execute_result(scalars_list=[])

        session = _mock_session()
        session.execute = capture

        await get_trades(session, TradeFilters(offset=20, limit=10))
        stmt = captured["stmt"]
        from sqlalchemy.dialects import postgresql
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        params = compiled.params
        assert "LIMIT" in sql.upper()
        assert "OFFSET" in sql.upper()
        param_values = list(params.values())
        assert 10 in param_values
        assert 20 in param_values

    @pytest.mark.asyncio
    async def test_default_sort_is_traded_at_desc(self):
        """The compiled query should include ORDER BY traded_at DESC."""
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            return _mock_execute_result(scalars_list=[])

        session = _mock_session()
        session.execute = capture

        await get_trades(session)
        stmt = captured["stmt"]
        from sqlalchemy.dialects import postgresql
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "DESC" in sql.upper()
        assert "traded_at" in sql


# ---------------------------------------------------------------------------
# count_trades
# ---------------------------------------------------------------------------

class TestCountTrades:
    @pytest.mark.asyncio
    async def test_no_filters_returns_total_count(self):
        """No filters — returns the scalar value from COUNT(id)."""
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_value=42)

        result = await count_trades(session)
        assert result == 42
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_symbol_filter_in_sql(self):
        """Symbol filter should appear in the compiled SQL bind params."""
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            return _mock_execute_result(scalar_value=5)

        session = _mock_session()
        session.execute = capture

        result = await count_trades(session, TradeFilters(symbol="005930"))
        assert result == 5

        from sqlalchemy.dialects import postgresql
        compiled = captured["stmt"].compile(dialect=postgresql.dialect())
        params = compiled.params
        assert any("005930" in str(v) for v in params.values())

    @pytest.mark.asyncio
    async def test_date_range_filter_in_sql(self):
        """date_from and date_to should appear in compiled bind params."""
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            return _mock_execute_result(scalar_value=3)

        session = _mock_session()
        session.execute = capture

        date_from = datetime(2024, 1, 1, tzinfo=timezone.utc)
        date_to = datetime(2024, 6, 30, tzinfo=timezone.utc)
        result = await count_trades(session, TradeFilters(date_from=date_from, date_to=date_to))
        assert result == 3

        from sqlalchemy.dialects import postgresql
        compiled = captured["stmt"].compile(dialect=postgresql.dialect())
        params = compiled.params
        assert any(v == date_from for v in params.values())
        assert any(v == date_to for v in params.values())

    @pytest.mark.asyncio
    async def test_side_filter_in_sql(self):
        """Side filter should appear in the compiled SQL bind params."""
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            return _mock_execute_result(scalar_value=7)

        session = _mock_session()
        session.execute = capture

        result = await count_trades(session, TradeFilters(side="buy"))
        assert result == 7

        from sqlalchemy.dialects import postgresql
        compiled = captured["stmt"].compile(dialect=postgresql.dialect())
        params = compiled.params
        assert any(v == "buy" for v in params.values())

    @pytest.mark.asyncio
    async def test_zero_results_case(self):
        """When COUNT returns 0 (or None), count_trades should return 0."""
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_value=None)

        result = await count_trades(session)
        assert result == 0


# ---------------------------------------------------------------------------
# get_trade
# ---------------------------------------------------------------------------

class TestGetTrade:
    @pytest.mark.asyncio
    async def test_returns_trade_when_found(self):
        trade = MagicMock(spec=Trade)
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_value=trade)

        result = await get_trade(session, trade_id=1)
        assert result is trade

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_value=None)

        result = await get_trade(session, trade_id=999)
        assert result is None

    @pytest.mark.asyncio
    async def test_query_filters_by_id(self):
        """Compiled SQL bind params should include the trade_id value."""
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            return _mock_execute_result(scalar_value=None)

        session = _mock_session()
        session.execute = capture

        await get_trade(session, trade_id=42)
        stmt = captured["stmt"]
        from sqlalchemy.dialects import postgresql
        compiled = stmt.compile(dialect=postgresql.dialect())
        params = compiled.params
        assert 42 in params.values()


# ---------------------------------------------------------------------------
# update_trade_reason
# ---------------------------------------------------------------------------

class TestUpdateTradeReason:
    @pytest.mark.asyncio
    async def test_updates_and_returns_trade(self):
        trade = MagicMock(spec=Trade)
        trade.id = 1
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(rowcount=1)
        # get is called BEFORE execute (existence check)
        session.get.return_value = trade

        result = await update_trade_reason(session, trade_id=1, reason="좋은 진입 타이밍")
        assert result is trade
        session.execute.assert_called_once()
        session.commit.assert_called_once()
        session.get.assert_called_once_with(Trade, 1)

    @pytest.mark.asyncio
    async def test_raises_before_update_when_trade_not_found(self):
        """ValueError is raised before execute when the trade does not exist."""
        session = _mock_session()
        session.get.return_value = None  # trade missing

        with pytest.raises(ValueError, match="Trade 999 not found"):
            await update_trade_reason(session, trade_id=999, reason="test")

        # execute and commit must NOT have been called
        session.execute.assert_not_called()
        session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# update_trade_review
# ---------------------------------------------------------------------------

class TestUpdateTradeReview:
    @pytest.mark.asyncio
    async def test_updates_and_returns_trade(self):
        trade = MagicMock(spec=Trade)
        trade.id = 2
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(rowcount=1)
        # get is called BEFORE execute (existence check)
        session.get.return_value = trade

        result = await update_trade_review(session, trade_id=2, review="손절 타이밍 늦었음")
        assert result is trade
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_before_update_when_trade_not_found(self):
        """ValueError is raised before execute when the trade does not exist."""
        session = _mock_session()
        session.get.return_value = None  # trade missing

        with pytest.raises(ValueError, match="Trade 777 not found"):
            await update_trade_review(session, trade_id=777, review="test")

        # execute and commit must NOT have been called
        session.execute.assert_not_called()
        session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# get_trade_stats
# ---------------------------------------------------------------------------

class TestGetTradeStats:
    def _make_agg_row(self, total_count=5, total_amount=Decimal("1000000")):
        row = MagicMock()
        row.total_count = total_count
        row.total_amount = total_amount
        return row

    @pytest.mark.asyncio
    async def test_returns_expected_keys(self):
        agg_row = self._make_agg_row()
        freq_row = MagicMock()
        freq_row.symbol = "005930"
        freq_row.name = "삼성전자"
        freq_row.trade_count = 3

        call_count = 0

        async def multi_execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # agg query
                r = MagicMock()
                r.one = MagicMock(return_value=agg_row)
                return r
            elif call_count == 2:
                # buy count
                r = MagicMock()
                r.scalar = MagicMock(return_value=3)
                return r
            elif call_count == 3:
                # sell count
                r = MagicMock()
                r.scalar = MagicMock(return_value=2)
                return r
            else:
                # freq query
                r = MagicMock()
                r.all = MagicMock(return_value=[freq_row])
                return r

        session = _mock_session()
        session.execute = multi_execute

        stats = await get_trade_stats(session)

        assert "total_count" in stats
        assert "buy_count" in stats
        assert "sell_count" in stats
        assert "total_amount" in stats
        assert "symbol_frequency" in stats
        assert stats["total_count"] == 5
        assert stats["buy_count"] == 3
        assert stats["sell_count"] == 2
        assert stats["total_amount"] == 1_000_000.0
        assert len(stats["symbol_frequency"]) == 1
        assert stats["symbol_frequency"][0]["symbol"] == "005930"

    @pytest.mark.asyncio
    async def test_handles_none_aggregates(self):
        """When there are no trades, totals should be 0, not None."""
        agg_row = self._make_agg_row(total_count=0, total_amount=None)
        call_count = 0

        async def multi_execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                r = MagicMock()
                r.one = MagicMock(return_value=agg_row)
                return r
            elif call_count in (2, 3):
                r = MagicMock()
                r.scalar = MagicMock(return_value=None)
                return r
            else:
                r = MagicMock()
                r.all = MagicMock(return_value=[])
                return r

        session = _mock_session()
        session.execute = multi_execute

        stats = await get_trade_stats(session)
        assert stats["total_count"] == 0
        assert stats["total_amount"] == 0.0
        assert stats["buy_count"] == 0
        assert stats["sell_count"] == 0
        assert stats["symbol_frequency"] == []

    @pytest.mark.asyncio
    async def test_side_filter_does_not_conflict_with_buy_sell_subqueries(self):
        """When filters.side='buy', the buy/sell sub-queries must not get an
        impossible double side condition (e.g. side='buy' AND side='sell').

        We verify this by inspecting the compiled SQL for the sell sub-query:
        it must contain side='sell' but must NOT also contain side='buy'
        (which would be added by _apply_filters without exclude_side=True).
        """
        captured_stmts: list = []

        async def capture_execute(stmt, *a, **kw):
            captured_stmts.append(stmt)
            r = MagicMock()
            if len(captured_stmts) == 1:
                # agg
                agg_row = MagicMock()
                agg_row.total_count = 2
                agg_row.total_amount = Decimal("200000")
                r.one = MagicMock(return_value=agg_row)
            elif len(captured_stmts) == 2:
                # buy count
                r.scalar = MagicMock(return_value=2)
            elif len(captured_stmts) == 3:
                # sell count
                r.scalar = MagicMock(return_value=0)
            else:
                # freq
                r.all = MagicMock(return_value=[])
            return r

        session = _mock_session()
        session.execute = capture_execute

        stats = await get_trade_stats(session, TradeFilters(side="buy"))

        # Should complete without error and return sane values
        assert stats["buy_count"] == 2
        assert stats["sell_count"] == 0

        # Inspect the sell sub-query (3rd statement, index 2)
        from sqlalchemy.dialects import postgresql
        sell_stmt = captured_stmts[2]
        compiled = sell_stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        params = compiled.params

        # The sell sub-query must target side='sell'
        assert any(v == "sell" for v in params.values()), (
            "sell sub-query should have side='sell' bind param"
        )
        # The sell sub-query must NOT also have side='buy' (the filter value)
        assert not any(v == "buy" for v in params.values()), (
            "sell sub-query must not contain side='buy' — that would be an impossible condition"
        )


# ---------------------------------------------------------------------------
# get_chart_data
# ---------------------------------------------------------------------------

class TestGetChartData:
    @pytest.mark.asyncio
    async def test_returns_trades_for_symbol(self):
        trade = MagicMock(spec=Trade)
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_list=[trade])

        result = await get_chart_data(session, symbol="005930")
        assert result == [trade]

    @pytest.mark.asyncio
    async def test_date_range_in_sql(self):
        """date_from and date_to should appear in compiled bind params."""
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            return _mock_execute_result(scalars_list=[])

        session = _mock_session()
        session.execute = capture

        date_from = datetime(2024, 1, 1, tzinfo=timezone.utc)
        date_to = datetime(2024, 3, 31, tzinfo=timezone.utc)
        await get_chart_data(session, symbol="005930", date_from=date_from, date_to=date_to)

        from sqlalchemy.dialects import postgresql
        compiled = captured["stmt"].compile(dialect=postgresql.dialect())
        sql = str(compiled)
        params = compiled.params
        # symbol and both dates should be in bind params
        assert any("005930" in str(v) for v in params.values())
        assert any(v == date_from for v in params.values())
        assert any(v == date_to for v in params.values())

    @pytest.mark.asyncio
    async def test_sorted_asc(self):
        """Chart data should be sorted ASC by traded_at."""
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            return _mock_execute_result(scalars_list=[])

        session = _mock_session()
        session.execute = capture

        await get_chart_data(session, symbol="005930")
        from sqlalchemy.dialects import postgresql
        sql = str(captured["stmt"].compile(dialect=postgresql.dialect()))
        assert "ASC" in sql.upper() or "DESC" not in sql.upper()

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_trades(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_list=[])

        result = await get_chart_data(session, symbol="999999")
        assert result == []


# ---------------------------------------------------------------------------
# TradeFilters dataclass
# ---------------------------------------------------------------------------

class TestTradeFilters:
    def test_defaults(self):
        f = TradeFilters()
        assert f.symbol is None
        assert f.date_from is None
        assert f.date_to is None
        assert f.broker is None
        assert f.side is None
        assert f.account_type is None
        assert f.offset == 0
        assert f.limit == 100

    def test_partial_init(self):
        f = TradeFilters(symbol="005930", side="buy", limit=20)
        assert f.symbol == "005930"
        assert f.side == "buy"
        assert f.limit == 20
        assert f.offset == 0
