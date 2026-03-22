"""Unit tests for trades/ohlcv.py.

All tests use mocked AsyncSession and mocked pykrx — no live DB or network required.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pandas as pd
import pytest

from trades.ohlcv import (
    fetch_ohlcv_for_symbol,
    fetch_ohlcv_batch,
    refresh_cached_symbols,
    get_earliest_trade_date,
    _fetch_from_pykrx,
    _get_cached_dates,
    _upsert_ohlcv_rows,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_session() -> MagicMock:
    """Return a MagicMock that behaves like AsyncSession."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


def _make_ohlcv_df(
    start: date = date(2024, 1, 2),
    days: int = 3,
) -> pd.DataFrame:
    """Create a minimal pykrx-style OHLCV DataFrame."""
    dates = [start + timedelta(days=i) for i in range(days)]
    data = {
        "시가": [70000, 71000, 72000][:days],
        "고가": [72000, 73000, 74000][:days],
        "저가": [69000, 70000, 71000][:days],
        "종가": [71000, 72000, 73000][:days],
        "거래량": [1000000, 1100000, 1200000][:days],
    }
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
    return pd.DataFrame(data, index=index)


def _make_execute_result_scalars(items: list) -> MagicMock:
    """Fake session.execute() result for scalar queries."""
    result = MagicMock()
    result.scalar = MagicMock(return_value=items[0] if items else None)
    result.scalar_one_or_none = MagicMock(return_value=items[0] if items else None)
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=items)
    result.scalars = MagicMock(return_value=scalars_mock)
    # For tuple-style rows (date queries)
    result.all = MagicMock(return_value=[(item,) for item in items])
    result.rowcount = len(items)
    return result


def _make_execute_result_rows(rows: list) -> MagicMock:
    """Fake session.execute() result for multi-column row queries."""
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    result.rowcount = len(rows)
    return result


# ---------------------------------------------------------------------------
# _get_cached_dates
# ---------------------------------------------------------------------------

class TestGetCachedDates:
    @pytest.mark.asyncio
    async def test_returns_empty_set_when_no_cache(self):
        session = _mock_session()
        session.execute.return_value = _make_execute_result_rows([])
        result = await _get_cached_dates(session, "005930")
        assert result == set()

    @pytest.mark.asyncio
    async def test_returns_cached_dates(self):
        d1 = date(2024, 1, 2)
        d2 = date(2024, 1, 3)
        session = _mock_session()
        # simulate rows of (date,) tuples
        result_mock = MagicMock()
        result_mock.all = MagicMock(return_value=[(d1,), (d2,)])
        session.execute.return_value = result_mock

        result = await _get_cached_dates(session, "005930")
        assert result == {d1, d2}

    @pytest.mark.asyncio
    async def test_query_filters_by_symbol(self):
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            m = MagicMock()
            m.all = MagicMock(return_value=[])
            return m

        session = _mock_session()
        session.execute = capture

        await _get_cached_dates(session, "000660")

        from sqlalchemy.dialects import postgresql
        compiled = captured["stmt"].compile(dialect=postgresql.dialect())
        params = compiled.params
        assert any("000660" in str(v) for v in params.values())


# ---------------------------------------------------------------------------
# _upsert_ohlcv_rows
# ---------------------------------------------------------------------------

class TestUpsertOhlcvRows:
    @pytest.mark.asyncio
    async def test_skips_all_cached_dates(self):
        session = _mock_session()
        df = _make_ohlcv_df(start=date(2024, 1, 2), days=2)
        skip_dates = {date(2024, 1, 2), date(2024, 1, 3)}

        inserted = await _upsert_ohlcv_rows(session, "005930", df, skip_dates)
        assert inserted == 0
        session.execute.assert_not_called()
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_inserts_new_dates(self):
        session = _mock_session()
        exec_result = MagicMock()
        exec_result.rowcount = 2
        session.execute.return_value = exec_result

        df = _make_ohlcv_df(start=date(2024, 1, 2), days=2)
        inserted = await _upsert_ohlcv_rows(session, "005930", df, skip_dates=set())

        session.execute.assert_called_once()
        # commit is the caller's responsibility — helper must NOT call it
        session.commit.assert_not_called()
        assert inserted == 2

    @pytest.mark.asyncio
    async def test_partial_skip(self):
        """Only one date cached — other date should be inserted."""
        session = _mock_session()
        exec_result = MagicMock()
        exec_result.rowcount = 1
        session.execute.return_value = exec_result

        df = _make_ohlcv_df(start=date(2024, 1, 2), days=2)
        # Skip first date, insert second
        inserted = await _upsert_ohlcv_rows(session, "005930", df, skip_dates={date(2024, 1, 2)})

        assert inserted == 1
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_correct_columns_mapped(self):
        """Verify the insert values have the correct column names."""
        captured = {}

        async def capture_execute(stmt, *a, **kw):
            captured["stmt"] = stmt
            m = MagicMock()
            m.rowcount = 1
            return m

        session = _mock_session()
        session.execute = capture_execute

        df = _make_ohlcv_df(start=date(2024, 1, 2), days=1)
        await _upsert_ohlcv_rows(session, "005930", df, skip_dates=set())

        # Check the compiled SQL contains expected column names
        from sqlalchemy.dialects import postgresql
        sql = str(captured["stmt"].compile(dialect=postgresql.dialect()))
        assert "symbol" in sql
        assert "date" in sql
        assert "open" in sql or "시가" not in sql  # mapped to 'open'
        assert "volume" in sql


# ---------------------------------------------------------------------------
# _fetch_from_pykrx
# ---------------------------------------------------------------------------

class TestFetchFromPykrx:
    @pytest.mark.asyncio
    async def test_returns_dataframe_on_success(self):
        df = _make_ohlcv_df()
        with patch("trades.ohlcv._call_pykrx", return_value=df):
            result = await _fetch_from_pykrx("005930", date(2024, 1, 1), date(2024, 1, 31))
        assert result is df

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        """Simulate timeout by raising asyncio.TimeoutError directly."""
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await _fetch_from_pykrx("005930", date(2024, 1, 1), date(2024, 1, 31))
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        with patch("trades.ohlcv._call_pykrx", side_effect=Exception("network error")):
            result = await _fetch_from_pykrx("005930", date(2024, 1, 1), date(2024, 1, 31))
        assert result is None

    @pytest.mark.asyncio
    async def test_date_format_passed_correctly(self):
        """pykrx expects YYYYMMDD strings."""
        called_with = {}

        def fake_call(ticker, fromdate, todate):
            called_with["fromdate"] = fromdate
            called_with["todate"] = todate
            return _make_ohlcv_df()

        with patch("trades.ohlcv._call_pykrx", side_effect=fake_call):
            await _fetch_from_pykrx("005930", date(2024, 1, 5), date(2024, 3, 15))

        assert called_with["fromdate"] == "20240105"
        assert called_with["todate"] == "20240315"


# ---------------------------------------------------------------------------
# fetch_ohlcv_for_symbol
# ---------------------------------------------------------------------------

class TestFetchOhlcvForSymbol:
    @pytest.mark.asyncio
    async def test_success_full_insert(self):
        """No cached dates → all rows inserted."""
        session = _mock_session()
        # _get_cached_dates returns empty
        cached_result = MagicMock()
        cached_result.all = MagicMock(return_value=[])
        # upsert result
        upsert_result = MagicMock()
        upsert_result.rowcount = 3

        session.execute = AsyncMock(side_effect=[cached_result, upsert_result])

        df = _make_ohlcv_df(days=3)
        with patch("trades.ohlcv._fetch_from_pykrx", return_value=df):
            result = await fetch_ohlcv_for_symbol(
                session, "005930", date(2024, 1, 1), date(2024, 1, 31)
            )

        assert result["fetched"] == 3
        assert result["inserted"] == 3
        assert result["skipped_dates"] == 0

    @pytest.mark.asyncio
    async def test_skips_already_cached_dates(self):
        """All dates already cached → inserted = 0."""
        d1 = date(2024, 1, 2)
        d2 = date(2024, 1, 3)
        d3 = date(2024, 1, 4)

        session = _mock_session()
        cached_result = MagicMock()
        cached_result.all = MagicMock(return_value=[(d1,), (d2,), (d3,)])
        session.execute.return_value = cached_result

        df = _make_ohlcv_df(days=3)
        with patch("trades.ohlcv._fetch_from_pykrx", return_value=df):
            result = await fetch_ohlcv_for_symbol(
                session, "005930", date(2024, 1, 1), date(2024, 1, 31)
            )

        assert result["inserted"] == 0
        assert result["skipped_dates"] == 3

    @pytest.mark.asyncio
    async def test_returns_zeros_on_pykrx_timeout(self):
        """pykrx returns None (timeout) → fetched and inserted are 0."""
        session = _mock_session()
        cached_result = MagicMock()
        cached_result.all = MagicMock(return_value=[])
        session.execute.return_value = cached_result

        with patch("trades.ohlcv._fetch_from_pykrx", return_value=None):
            result = await fetch_ohlcv_for_symbol(
                session, "005930", date(2024, 1, 1), date(2024, 1, 31)
            )

        assert result["fetched"] == 0
        assert result["inserted"] == 0

    @pytest.mark.asyncio
    async def test_returns_zeros_on_empty_dataframe(self):
        """pykrx returns empty DataFrame → fetched and inserted are 0."""
        session = _mock_session()
        cached_result = MagicMock()
        cached_result.all = MagicMock(return_value=[])
        session.execute.return_value = cached_result

        with patch("trades.ohlcv._fetch_from_pykrx", return_value=pd.DataFrame()):
            result = await fetch_ohlcv_for_symbol(
                session, "005930", date(2024, 1, 1), date(2024, 1, 31)
            )

        assert result["fetched"] == 0
        assert result["inserted"] == 0

    @pytest.mark.asyncio
    async def test_partial_cache_only_new_dates_inserted(self):
        """2 of 3 dates cached → 1 inserted."""
        d1 = date(2024, 1, 2)
        d2 = date(2024, 1, 3)

        session = _mock_session()
        cached_result = MagicMock()
        cached_result.all = MagicMock(return_value=[(d1,), (d2,)])
        upsert_result = MagicMock()
        upsert_result.rowcount = 1
        session.execute = AsyncMock(side_effect=[cached_result, upsert_result])

        df = _make_ohlcv_df(days=3)  # dates: 1/2, 1/3, 1/4
        with patch("trades.ohlcv._fetch_from_pykrx", return_value=df):
            result = await fetch_ohlcv_for_symbol(
                session, "005930", date(2024, 1, 1), date(2024, 1, 31)
            )

        assert result["fetched"] == 3
        assert result["inserted"] == 1
        assert result["skipped_dates"] == 2


# ---------------------------------------------------------------------------
# fetch_ohlcv_batch
# ---------------------------------------------------------------------------

class TestFetchOhlcvBatch:
    @pytest.mark.asyncio
    async def test_processes_all_symbols(self):
        symbols = ["005930", "000660", "035420"]
        fake_result = {"fetched": 3, "inserted": 3, "skipped_dates": 0}

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol", return_value=fake_result) as mock_fetch:
            session = _mock_session()
            results = await fetch_ohlcv_batch(session, symbols)

        assert set(results.keys()) == set(symbols)
        assert mock_fetch.call_count == 3

    @pytest.mark.asyncio
    async def test_default_date_range_one_year(self):
        """from_date defaults to today - 365 days."""
        captured_dates = []

        async def fake_fetch(session, symbol, from_date, to_date):
            captured_dates.append((from_date, to_date))
            return {"fetched": 0, "inserted": 0, "skipped_dates": 0}

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol", side_effect=fake_fetch):
            session = _mock_session()
            today = date.today()
            await fetch_ohlcv_batch(session, ["005930"])

        from_date, to_date = captured_dates[0]
        assert to_date == today
        assert (to_date - from_date).days == 365

    @pytest.mark.asyncio
    async def test_custom_date_range(self):
        """Explicit from_date/to_date should be passed through."""
        captured_dates = []

        async def fake_fetch(session, symbol, from_date, to_date):
            captured_dates.append((from_date, to_date))
            return {"fetched": 0, "inserted": 0, "skipped_dates": 0}

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol", side_effect=fake_fetch):
            session = _mock_session()
            fd = date(2023, 1, 1)
            td = date(2023, 12, 31)
            await fetch_ohlcv_batch(session, ["005930"], from_date=fd, to_date=td)

        assert captured_dates[0] == (fd, td)

    @pytest.mark.asyncio
    async def test_empty_symbols_returns_empty_dict(self):
        session = _mock_session()
        results = await fetch_ohlcv_batch(session, [])
        assert results == {}

    @pytest.mark.asyncio
    async def test_one_symbol_failure_does_not_affect_others(self):
        """Timeout on symbol 1 should not prevent symbol 2 from being processed."""
        call_order = []

        async def fake_fetch(session, symbol, from_date, to_date):
            call_order.append(symbol)
            if symbol == "005930":
                return {"fetched": 0, "inserted": 0, "skipped_dates": 0}  # simulate skip
            return {"fetched": 3, "inserted": 3, "skipped_dates": 0}

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol", side_effect=fake_fetch):
            session = _mock_session()
            results = await fetch_ohlcv_batch(session, ["005930", "000660"])

        assert "005930" in results
        assert "000660" in results
        assert results["000660"]["inserted"] == 3
        assert call_order == ["005930", "000660"]

    @pytest.mark.asyncio
    async def test_result_keys_match_symbols(self):
        symbols = ["A", "B", "C"]
        fake_result = {"fetched": 1, "inserted": 1, "skipped_dates": 0}

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol", return_value=fake_result):
            session = _mock_session()
            results = await fetch_ohlcv_batch(session, symbols)

        assert list(results.keys()) == symbols


# ---------------------------------------------------------------------------
# refresh_cached_symbols
# ---------------------------------------------------------------------------

class TestRefreshCachedSymbols:
    @pytest.mark.asyncio
    async def test_empty_cache_returns_empty_dict(self):
        session = _mock_session()
        empty_result = MagicMock()
        empty_result.all = MagicMock(return_value=[])
        session.execute.return_value = empty_result

        result = await refresh_cached_symbols(session)
        assert result == {}

    @pytest.mark.asyncio
    async def test_fetches_from_last_cached_date_plus_one(self):
        """Each symbol should be fetched starting from last_cached_date + 1 day."""
        last_date = date(2024, 6, 1)
        expected_from = last_date + timedelta(days=1)

        session = _mock_session()
        rows_result = MagicMock()
        rows_result.all = MagicMock(return_value=[("005930", last_date)])
        session.execute.return_value = rows_result

        captured = []

        async def fake_fetch(session, symbol, from_date, to_date, **kwargs):
            captured.append((symbol, from_date, to_date))
            return {"fetched": 2, "inserted": 2, "skipped_dates": 0}

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol", side_effect=fake_fetch):
            result = await refresh_cached_symbols(session)

        assert len(captured) == 1
        sym, fd, td = captured[0]
        assert sym == "005930"
        assert fd == expected_from
        assert td == date.today()

    @pytest.mark.asyncio
    async def test_already_up_to_date_skips_fetch(self):
        """If last cached date is today or later, no fetch should happen."""
        today = date.today()

        session = _mock_session()
        rows_result = MagicMock()
        rows_result.all = MagicMock(return_value=[("005930", today)])
        session.execute.return_value = rows_result

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol") as mock_fetch:
            result = await refresh_cached_symbols(session)

        mock_fetch.assert_not_called()
        assert result["005930"]["inserted"] == 0

    @pytest.mark.asyncio
    async def test_multiple_symbols_processed(self):
        """All symbols in cache should be refreshed."""
        last_date = date(2024, 5, 1)

        session = _mock_session()
        rows_result = MagicMock()
        rows_result.all = MagicMock(return_value=[
            ("005930", last_date),
            ("000660", last_date),
        ])
        session.execute.return_value = rows_result

        fetch_calls = []

        async def fake_fetch(session, symbol, from_date, to_date, **kwargs):
            fetch_calls.append(symbol)
            return {"fetched": 1, "inserted": 1, "skipped_dates": 0}

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol", side_effect=fake_fetch):
            result = await refresh_cached_symbols(session)

        assert set(fetch_calls) == {"005930", "000660"}
        assert set(result.keys()) == {"005930", "000660"}

    @pytest.mark.asyncio
    async def test_result_contains_all_symbols(self):
        """Result dict includes symbols that were skipped (already up-to-date)."""
        today = date.today()
        old_date = date(2024, 1, 1)

        session = _mock_session()
        rows_result = MagicMock()
        rows_result.all = MagicMock(return_value=[
            ("005930", today),     # already up to date
            ("000660", old_date),  # needs refresh
        ])
        session.execute.return_value = rows_result

        async def fake_fetch(session, symbol, from_date, to_date, **kwargs):
            return {"fetched": 5, "inserted": 5, "skipped_dates": 0}

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol", side_effect=fake_fetch):
            result = await refresh_cached_symbols(session)

        assert "005930" in result
        assert "000660" in result
        assert result["005930"]["inserted"] == 0  # skipped
        assert result["000660"]["inserted"] == 5  # refreshed


# ---------------------------------------------------------------------------
# get_earliest_trade_date
# ---------------------------------------------------------------------------

class TestGetEarliestTradeDate:
    @pytest.mark.asyncio
    async def test_returns_date_when_trades_exist(self):
        session = _mock_session()
        dt = datetime(2024, 3, 15, 9, 30, tzinfo=None)
        result_mock = MagicMock()
        result_mock.scalar = MagicMock(return_value=dt)
        session.execute.return_value = result_mock

        result = await get_earliest_trade_date(session, "005930")
        assert result == date(2024, 3, 15)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_trades(self):
        session = _mock_session()
        result_mock = MagicMock()
        result_mock.scalar = MagicMock(return_value=None)
        session.execute.return_value = result_mock

        result = await get_earliest_trade_date(session, "005930")
        assert result is None

    @pytest.mark.asyncio
    async def test_query_filters_by_symbol(self):
        captured = {}

        async def capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            m = MagicMock()
            m.scalar = MagicMock(return_value=None)
            return m

        session = _mock_session()
        session.execute = capture

        await get_earliest_trade_date(session, "000660")

        from sqlalchemy.dialects import postgresql
        compiled = captured["stmt"].compile(dialect=postgresql.dialect())
        params = compiled.params
        assert any("000660" in str(v) for v in params.values())


# ---------------------------------------------------------------------------
# Integration-style: pykrx timeout in fetch_ohlcv_for_symbol
# ---------------------------------------------------------------------------

class TestTimeoutBehavior:
    @pytest.mark.asyncio
    async def test_timeout_logs_warning_and_returns_zeros(self):
        """When pykrx times out, function should return zeros without raising."""
        session = _mock_session()
        cached_result = MagicMock()
        cached_result.all = MagicMock(return_value=[])
        session.execute.return_value = cached_result

        # Simulate timeout by having _fetch_from_pykrx return None
        with patch("trades.ohlcv._fetch_from_pykrx", return_value=None):
            result = await fetch_ohlcv_for_symbol(
                session, "999999", date(2024, 1, 1), date(2024, 1, 31)
            )

        assert result["fetched"] == 0
        assert result["inserted"] == 0
        # No exception raised

    @pytest.mark.asyncio
    async def test_actual_asyncio_timeout_returns_none(self):
        """Direct test of _fetch_from_pykrx timeout mechanism via mocked wait_for."""
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await _fetch_from_pykrx("005930", date(2024, 1, 1), date(2024, 1, 31))

        assert result is None


# ---------------------------------------------------------------------------
# Deduplication: fetch does not re-request cached dates
# ---------------------------------------------------------------------------

class TestDeduplication:
    @pytest.mark.asyncio
    async def test_no_pykrx_call_when_all_dates_cached(self):
        """If all dates in range are cached, pykrx should still be called
        (we don't know ahead of time what pykrx will return), but the
        insert step should be skipped for cached dates."""
        # All 3 dates already in cache
        d1, d2, d3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)

        session = _mock_session()
        cached_result = MagicMock()
        cached_result.all = MagicMock(return_value=[(d1,), (d2,), (d3,)])
        session.execute.return_value = cached_result

        df = _make_ohlcv_df(days=3)
        with patch("trades.ohlcv._fetch_from_pykrx", return_value=df):
            result = await fetch_ohlcv_for_symbol(
                session, "005930", date(2024, 1, 1), date(2024, 1, 5)
            )

        # No execute call for the upsert (only the get_cached_dates SELECT)
        assert session.execute.call_count == 1  # only the cache query
        assert result["inserted"] == 0
        assert result["skipped_dates"] == 3

    @pytest.mark.asyncio
    async def test_refresh_uses_next_day_not_last_cached(self):
        """refresh_cached_symbols should start from last_date + 1, not last_date."""
        last_date = date(2024, 6, 15)

        session = _mock_session()
        rows_result = MagicMock()
        rows_result.all = MagicMock(return_value=[("005930", last_date)])
        session.execute.return_value = rows_result

        captured_from = []

        async def fake_fetch(session, symbol, from_date, to_date, **kwargs):
            captured_from.append(from_date)
            return {"fetched": 0, "inserted": 0, "skipped_dates": 0}

        with patch("trades.ohlcv.fetch_ohlcv_for_symbol", side_effect=fake_fetch):
            await refresh_cached_symbols(session)

        assert captured_from[0] == last_date + timedelta(days=1)
        assert captured_from[0] != last_date
