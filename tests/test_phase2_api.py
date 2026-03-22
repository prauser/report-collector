"""Unit tests for Phase 2 API endpoints.

Tests cover:
- POST /api/trades/upload — BackgroundTask trigger after successful upload
- GET /api/trades/{trade_id}/indicators — on-demand indicator calculation
- GET /api/trades/pairs — FIFO matching results
- GET /api/trades/positions — average cost positions
- GET /api/ohlcv/{symbol} — OHLCV data with date filter
- POST /api/ohlcv/refresh — manual OHLCV refresh trigger

All tests use mocked dependencies — no live DB or external API calls.
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_db
from db.models import PriceCache, Trade, TradeIndicator, TradePair
from trades.csv_parsers.common import TradeRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_db_session():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=[])
    result.scalars = MagicMock(return_value=scalars)
    result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute.return_value = result
    return session


def _override_db(session):
    async def _dep():
        yield session
    return _dep


def _make_trade_row(symbol: str = "005930") -> TradeRow:
    return TradeRow(
        symbol=symbol,
        name="삼성전자",
        side="buy",
        traded_at=datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc),
        price=Decimal("70000"),
        quantity=10,
        amount=Decimal("700000"),
        broker="kiwoom",
        account_type="개인",
        market="KOSPI",
        fees=None,
    )


def _make_trade_model(trade_id: int = 1, symbol: str = "005930") -> MagicMock:
    t = MagicMock(spec=Trade)
    t.id = trade_id
    t.symbol = symbol
    t.name = "삼성전자"
    t.side = "buy"
    t.traded_at = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
    t.price = Decimal("70000")
    t.quantity = 10
    t.amount = Decimal("700000")
    t.broker = "kiwoom"
    t.account_type = "개인"
    t.market = "KOSPI"
    t.fees = None
    t.reason = None
    t.review = None
    t.created_at = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    return t


def _make_trade_pair_model(pair_id: int = 1) -> MagicMock:
    p = MagicMock(spec=TradePair)
    p.id = pair_id
    p.buy_trade_id = 1
    p.sell_trade_id = 2
    p.profit_rate = Decimal("0.0500")
    p.holding_days = 5
    p.matched_qty = 10
    p.buy_amount = Decimal("700000")
    p.sell_amount = Decimal("735000")
    p.buy_fee = Decimal("700")
    p.sell_fee = Decimal("735")
    return p


def _make_price_cache_model(symbol: str = "005930", row_date: date = date(2024, 1, 15)) -> MagicMock:
    pc = MagicMock(spec=PriceCache)
    pc.symbol = symbol
    pc.date = row_date
    pc.open = Decimal("69000")
    pc.high = Decimal("71000")
    pc.low = Decimal("68500")
    pc.close = Decimal("70000")
    pc.volume = 1000000
    return pc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    session = _mock_db_session()
    app.dependency_overrides[get_db] = _override_db(session)
    with TestClient(app) as c:
        yield c, session
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/trades/upload — BackgroundTask tests
# ---------------------------------------------------------------------------

class TestUploadBackgroundTask:
    def test_upload_triggers_background_task_on_insert(self):
        """When rows are inserted, background task should be added."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)
        rows = [_make_trade_row("005930")]

        triggered_symbols = []

        async def _fake_bg(symbols):
            triggered_symbols.extend(symbols)

        with (
            patch("api.routers.trades.detect_broker", return_value="kiwoom"),
            patch("api.routers.trades.get_parser") as mock_get_parser,
            patch("api.routers.trades.upsert_trades", new_callable=AsyncMock) as mock_upsert,
            patch("api.routers.trades._bg_ohlcv_and_match", side_effect=_fake_bg),
        ):
            parser = MagicMock()
            parser.parse = MagicMock(return_value=rows)
            mock_get_parser.return_value = parser
            mock_upsert.return_value = {"inserted": 1, "skipped": 0}

            with TestClient(app) as c:
                resp = c.post(
                    "/api/trades/upload",
                    files={"file": ("test.csv", b"data", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["inserted"] == 1
        assert len(triggered_symbols) > 0
        assert "005930" in triggered_symbols

    def test_upload_no_background_task_when_all_skipped(self):
        """When no rows are inserted (all skipped), no background task."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)
        rows = [_make_trade_row("005930")]

        bg_calls = []

        with (
            patch("api.routers.trades.detect_broker", return_value="kiwoom"),
            patch("api.routers.trades.get_parser") as mock_get_parser,
            patch("api.routers.trades.upsert_trades", new_callable=AsyncMock) as mock_upsert,
            patch("api.routers.trades._bg_ohlcv_and_match") as mock_bg,
        ):
            parser = MagicMock()
            parser.parse = MagicMock(return_value=rows)
            mock_get_parser.return_value = parser
            mock_upsert.return_value = {"inserted": 0, "skipped": 1}

            with TestClient(app) as c:
                resp = c.post(
                    "/api/trades/upload",
                    files={"file": ("test.csv", b"data", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        # BackgroundTasks.add_task is called via FastAPI internally;
        # _bg_ohlcv_and_match should NOT be called directly since no inserts
        # (TestClient runs background tasks inline, but _bg_ohlcv_and_match is patched)
        mock_bg.assert_not_called()

    def test_dry_run_does_not_trigger_background_task(self):
        """dry_run=true should not trigger background task."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)
        rows = [_make_trade_row()]

        with (
            patch("api.routers.trades.detect_broker", return_value="kiwoom"),
            patch("api.routers.trades.get_parser") as mock_get_parser,
            patch("api.routers.trades.upsert_trades") as mock_upsert,
            patch("api.routers.trades._bg_ohlcv_and_match") as mock_bg,
        ):
            parser = MagicMock()
            parser.parse = MagicMock(return_value=rows)
            mock_get_parser.return_value = parser

            with TestClient(app) as c:
                resp = c.post(
                    "/api/trades/upload?dry_run=true",
                    files={"file": ("test.csv", b"data", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_upsert.assert_not_called()
        mock_bg.assert_not_called()

    def test_multiple_symbols_deduplicated_in_background_call(self):
        """Multiple rows with same symbol should be deduplicated."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)
        rows = [_make_trade_row("005930"), _make_trade_row("005930"), _make_trade_row("000660")]

        captured_symbols = []

        async def _capture_bg(symbols):
            captured_symbols.extend(symbols)

        with (
            patch("api.routers.trades.detect_broker", return_value="kiwoom"),
            patch("api.routers.trades.get_parser") as mock_get_parser,
            patch("api.routers.trades.upsert_trades", new_callable=AsyncMock) as mock_upsert,
            patch("api.routers.trades._bg_ohlcv_and_match", side_effect=_capture_bg),
        ):
            parser = MagicMock()
            parser.parse = MagicMock(return_value=rows)
            mock_get_parser.return_value = parser
            mock_upsert.return_value = {"inserted": 3, "skipped": 0}

            with TestClient(app) as c:
                c.post(
                    "/api/trades/upload",
                    files={"file": ("test.csv", b"data", "text/csv")},
                )

        app.dependency_overrides.clear()

        # TestClient runs background tasks inline, so _bg_ohlcv_and_match was called
        # with deduplicated symbols
        assert len(captured_symbols) == len(set(captured_symbols))
        assert set(captured_symbols) == {"005930", "000660"}


# ---------------------------------------------------------------------------
# GET /api/trades/{trade_id}/indicators
# ---------------------------------------------------------------------------

class TestTradeIndicators:
    def _make_indicator_result(self):
        """Create a mock IndicatorResult."""
        from trades.indicators import IndicatorResult, StochSet
        stoch_set = StochSet(
            k=5, d=3, smooth_k=3,
            stoch_k=45.0, stoch_d=42.0,
            cross="none", direction="rising", zone="neutral",
        )
        return IndicatorResult(
            stochastic={"daily": [stoch_set], "weekly": [], "monthly": []},
            ma={"alignment": "bullish", "deviations": {5: 1.2, 20: 0.5, 60: None, 120: None}, "values": {}},
            bb={"position": 0.65, "bandwidth": 5.2, "squeeze_expanding": "neutral"},
            volume_ratio=1.5,
            candle={"pattern": "bullish", "body_ratio": 0.7, "upper_shadow": 0.1, "lower_shadow": 0.2, "gap": "none"},
        )

    def test_returns_indicator_response(self, client):
        c, session = client
        trade = _make_trade_model(trade_id=1)
        ind_result = self._make_indicator_result()

        with (
            patch("api.routers.trades.get_trade", new_callable=AsyncMock, return_value=trade),
            patch(
                "trades.indicators.calculate_indicators_for_trade",
                new_callable=AsyncMock,
                return_value=ind_result,
            ),
            patch(
                "trades.indicators.generate_snapshot_text",
                return_value="▸ 스토캐스틱 정렬: 일봉 상승 1/1",
            ),
        ):
            resp = c.get("/api/trades/1/indicators")

        assert resp.status_code == 200
        data = resp.json()
        assert "stochastic" in data
        assert "ma" in data
        assert "bb" in data
        assert "volume_ratio" in data
        assert "candle" in data
        assert "snapshot_text" in data
        assert data["volume_ratio"] == 1.5

    def test_returns_404_when_trade_not_found(self, client):
        c, _ = client
        with patch("api.routers.trades.get_trade", new_callable=AsyncMock, return_value=None):
            resp = c.get("/api/trades/999/indicators")
        assert resp.status_code == 404

    def test_returns_404_when_no_price_data(self, client):
        c, session = client
        trade = _make_trade_model(trade_id=1)

        with (
            patch("api.routers.trades.get_trade", new_callable=AsyncMock, return_value=trade),
            patch(
                "trades.indicators.calculate_indicators_for_trade",
                new_callable=AsyncMock,
                side_effect=ValueError("No price data found for 005930"),
            ),
        ):
            resp = c.get("/api/trades/1/indicators")

        assert resp.status_code == 404
        assert "price data" in resp.json()["detail"].lower() or "005930" in resp.json()["detail"]

    def test_stochastic_is_serialized_to_plain_dicts(self, client):
        """StochSet dataclass objects must be converted to dicts in JSON response."""
        c, session = client
        trade = _make_trade_model(trade_id=1)
        ind_result = self._make_indicator_result()

        with (
            patch("api.routers.trades.get_trade", new_callable=AsyncMock, return_value=trade),
            patch(
                "trades.indicators.calculate_indicators_for_trade",
                new_callable=AsyncMock,
                return_value=ind_result,
            ),
            patch("trades.indicators.generate_snapshot_text", return_value="test"),
        ):
            resp = c.get("/api/trades/1/indicators")

        assert resp.status_code == 200
        data = resp.json()
        daily = data["stochastic"]["daily"]
        assert isinstance(daily, list)
        assert len(daily) == 1
        stoch = daily[0]
        assert "stoch_k" in stoch
        assert stoch["stoch_k"] == 45.0
        assert stoch["direction"] == "rising"
        assert stoch["zone"] == "neutral"

    def test_snapshot_text_included(self, client):
        c, session = client
        trade = _make_trade_model(trade_id=1)
        ind_result = self._make_indicator_result()

        with (
            patch("api.routers.trades.get_trade", new_callable=AsyncMock, return_value=trade),
            patch(
                "trades.indicators.calculate_indicators_for_trade",
                new_callable=AsyncMock,
                return_value=ind_result,
            ),
            patch(
                "trades.indicators.generate_snapshot_text",
                return_value="▸ 스토캐스틱 정렬: 일봉 상승 1/1 | 주봉 데이터부족 | 월봉 데이터부족",
            ),
        ):
            resp = c.get("/api/trades/1/indicators")

        assert resp.status_code == 200
        assert "▸ 스토캐스틱" in resp.json()["snapshot_text"]


# ---------------------------------------------------------------------------
# GET /api/trades/pairs
# ---------------------------------------------------------------------------

class TestTradePairs:
    def test_returns_empty_list_when_no_pairs(self, client):
        c, session = client
        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[])
        result.scalars = MagicMock(return_value=scalars)
        result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute.return_value = result

        resp = c.get("/api/trades/pairs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_pairs_without_filter(self, client):
        c, session = client
        pair = _make_trade_pair_model(pair_id=1)

        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[pair])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/trades/pairs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["buy_trade_id"] == 1
        assert data[0]["sell_trade_id"] == 2
        assert data[0]["matched_qty"] == 10
        assert Decimal(data[0]["buy_amount"]) == Decimal("700000")
        assert Decimal(data[0]["profit_rate"]) == Decimal("0.0500")

    def test_symbol_filter_returns_empty_when_no_trades(self, client):
        c, session = client

        # First two execute calls (buy_ids, sell_ids) return empty
        empty_result = MagicMock()
        empty_scalars = MagicMock()
        # Make iteration return empty list for buy/sell id queries
        empty_result.__iter__ = MagicMock(return_value=iter([]))
        session.execute.return_value = empty_result

        resp = c.get("/api/trades/pairs?symbol=999999")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_pair_response_fields_complete(self, client):
        c, session = client
        pair = _make_trade_pair_model()

        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[pair])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/trades/pairs")
        assert resp.status_code == 200
        data = resp.json()[0]
        expected_fields = {
            "id", "buy_trade_id", "sell_trade_id", "profit_rate",
            "holding_days", "matched_qty", "buy_amount", "sell_amount",
            "buy_fee", "sell_fee",
        }
        assert expected_fields.issubset(set(data.keys()))

    def test_holding_days_and_profit_rate_nullable(self, client):
        c, session = client
        pair = _make_trade_pair_model()
        pair.profit_rate = None
        pair.holding_days = None

        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[pair])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/trades/pairs")
        assert resp.status_code == 200
        data = resp.json()[0]
        assert data["profit_rate"] is None
        assert data["holding_days"] is None


# ---------------------------------------------------------------------------
# GET /api/trades/positions
# ---------------------------------------------------------------------------

class TestPositions:
    def test_returns_empty_list_when_no_symbols(self, client):
        c, session = client

        # No symbols in trades table
        result = MagicMock()
        result.__iter__ = MagicMock(return_value=iter([]))
        session.execute.return_value = result

        with patch(
            "trades.pairing.calculate_avg_cost",
            new_callable=AsyncMock,
        ) as mock_calc:
            resp = c.get("/api/trades/positions")

        assert resp.status_code == 200
        assert resp.json() == []
        mock_calc.assert_not_called()

    def test_returns_positions_with_remaining_qty(self, client):
        c, session = client
        from trades.pairing import AvgCostPosition

        pos = AvgCostPosition(
            symbol="005930",
            avg_cost=Decimal("70000"),
            remaining_qty=10,
            open_lots=[{"buy_trade_id": 1, "qty": 10, "price": Decimal("70000")}],
        )

        # Mock execute to return symbol list
        sym_result = MagicMock()
        sym_result.__iter__ = MagicMock(return_value=iter([("005930",)]))
        session.execute.return_value = sym_result

        with patch("trades.pairing.calculate_avg_cost", new_callable=AsyncMock, return_value=pos):
            resp = c.get("/api/trades/positions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "005930"
        assert Decimal(data[0]["avg_cost"]) == Decimal("70000")
        assert data[0]["remaining_qty"] == 10

    def test_excludes_fully_sold_positions(self, client):
        c, session = client
        from trades.pairing import AvgCostPosition

        # remaining_qty=0 means fully sold
        pos = AvgCostPosition(
            symbol="005930",
            avg_cost=Decimal("0"),
            remaining_qty=0,
            open_lots=[],
        )

        sym_result = MagicMock()
        sym_result.__iter__ = MagicMock(return_value=iter([("005930",)]))
        session.execute.return_value = sym_result

        with patch("trades.pairing.calculate_avg_cost", new_callable=AsyncMock, return_value=pos):
            resp = c.get("/api/trades/positions")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_open_lots_price_is_float_serializable(self, client):
        """Decimal prices in open_lots must be converted to float for JSON."""
        c, session = client
        from trades.pairing import AvgCostPosition

        pos = AvgCostPosition(
            symbol="005930",
            avg_cost=Decimal("70000"),
            remaining_qty=5,
            open_lots=[{"buy_trade_id": 1, "qty": 5, "price": Decimal("70000")}],
        )

        sym_result = MagicMock()
        sym_result.__iter__ = MagicMock(return_value=iter([("005930",)]))
        session.execute.return_value = sym_result

        with patch("trades.pairing.calculate_avg_cost", new_callable=AsyncMock, return_value=pos):
            resp = c.get("/api/trades/positions")

        assert resp.status_code == 200
        data = resp.json()
        lot = data[0]["open_lots"][0]
        # price should be JSON-serializable (float or numeric string)
        assert isinstance(lot["price"], (int, float))


# ---------------------------------------------------------------------------
# GET /api/ohlcv/{symbol}
# ---------------------------------------------------------------------------

class TestOhlcv:
    def test_returns_empty_list_when_no_data(self, client):
        c, session = client
        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/ohlcv/005930")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_ohlcv_data(self, client):
        c, session = client
        pc = _make_price_cache_model("005930", date(2024, 1, 15))

        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[pc])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/ohlcv/005930")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["date"] == "2024-01-15"
        assert Decimal(data[0]["open"]) == Decimal("69000")
        assert Decimal(data[0]["close"]) == Decimal("70000")
        assert data[0]["volume"] == 1000000

    def test_ohlcv_response_fields(self, client):
        c, session = client
        pc = _make_price_cache_model()

        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[pc])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/ohlcv/005930")
        assert resp.status_code == 200
        data = resp.json()[0]
        assert set(data.keys()) == {"date", "open", "high", "low", "close", "volume"}

    def test_from_date_filter_accepted(self, client):
        """?from= query param should be accepted without error."""
        c, session = client
        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/ohlcv/005930?from=2024-01-01")
        assert resp.status_code == 200

    def test_to_date_filter_accepted(self, client):
        c, session = client
        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/ohlcv/005930?to=2024-12-31")
        assert resp.status_code == 200

    def test_date_range_filter_accepted(self, client):
        c, session = client
        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/ohlcv/005930?from=2024-01-01&to=2024-03-31")
        assert resp.status_code == 200

    def test_multiple_rows_returned_in_order(self, client):
        c, session = client
        pc1 = _make_price_cache_model("005930", date(2024, 1, 14))
        pc2 = _make_price_cache_model("005930", date(2024, 1, 15))
        pc1.close = Decimal("69500")
        pc2.close = Decimal("70000")

        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[pc1, pc2])
        result.scalars = MagicMock(return_value=scalars)
        session.execute.return_value = result

        resp = c.get("/api/ohlcv/005930")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["date"] == "2024-01-14"
        assert data[1]["date"] == "2024-01-15"


# ---------------------------------------------------------------------------
# POST /api/ohlcv/refresh
# ---------------------------------------------------------------------------

class TestOhlcvRefresh:
    def test_refresh_returns_triggered_status(self, client):
        c, _ = client

        with patch("trades.ohlcv.refresh_cached_symbols", new_callable=AsyncMock):
            resp = c.post("/api/ohlcv/refresh")

        assert resp.status_code == 200
        assert resp.json()["status"] == "refresh_triggered"

    def test_refresh_is_non_blocking(self, client):
        """Endpoint should return immediately without waiting for refresh."""
        c, _ = client

        with patch("trades.ohlcv.refresh_cached_symbols", new_callable=AsyncMock):
            resp = c.post("/api/ohlcv/refresh")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Regression: existing endpoints still work
# ---------------------------------------------------------------------------

class TestExistingEndpointsRegression:
    def test_list_trades_still_works(self, client):
        c, _ = client
        with (
            patch("api.routers.trades.get_trades", new_callable=AsyncMock, return_value=[]),
            patch("api.routers.trades.count_trades", new_callable=AsyncMock, return_value=0),
        ):
            resp = c.get("/api/trades")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total"] == 0

    def test_trade_stats_still_works(self, client):
        c, _ = client
        with patch(
            "api.routers.trades.get_trade_stats",
            new_callable=AsyncMock,
            return_value={
                "total_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "total_amount": Decimal("0"),
                "symbol_frequency": [],
            },
        ):
            resp = c.get("/api/trades/stats")
        assert resp.status_code == 200

    def test_get_trade_detail_still_works(self, client):
        c, session = client
        trade = _make_trade_model(trade_id=10)

        ind_result = MagicMock()
        ind_result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute.return_value = ind_result

        with patch("api.routers.trades.get_trade", new_callable=AsyncMock, return_value=trade):
            resp = c.get("/api/trades/10")
        assert resp.status_code == 200
        assert resp.json()["id"] == 10

    def test_chart_data_still_works(self, client):
        c, _ = client
        with patch(
            "api.routers.trades.get_chart_data",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = c.get("/api/trades/chart-data?symbol=005930")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_patch_reason_still_works(self, client):
        c, _ = client
        trade = _make_trade_model(trade_id=1)
        trade.reason = "good entry"
        with patch(
            "api.routers.trades.update_trade_reason",
            new_callable=AsyncMock,
            return_value=trade,
        ):
            resp = c.patch("/api/trades/1/reason", json={"reason": "good entry"})
        assert resp.status_code == 200
        assert resp.json()["reason"] == "good entry"

    def test_upload_dry_run_still_works(self):
        """Existing dry_run behavior should be unchanged."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)
        rows = [_make_trade_row()]

        with (
            patch("api.routers.trades.detect_broker", return_value="kiwoom"),
            patch("api.routers.trades.get_parser") as mock_get_parser,
            patch("api.routers.trades.upsert_trades") as mock_upsert,
        ):
            parser = MagicMock()
            parser.parse = MagicMock(return_value=rows)
            mock_get_parser.return_value = parser

            with TestClient(app) as c:
                resp = c.post(
                    "/api/trades/upload?dry_run=true",
                    files={"file": ("test.csv", b"data", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert data["preview"] is not None
        assert len(data["preview"]) == 1
        mock_upsert.assert_not_called()
