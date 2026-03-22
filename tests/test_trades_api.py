"""Unit tests for api/routers/trades.py.

Uses FastAPI TestClient with mocked repository functions and CSV parsers.
No live DB or real CSV files required.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_db
from db.models import Trade, TradeIndicator
from trades.csv_parsers.common import TradeRow
from trades.trade_repo import TradeFilters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trade_row(
    symbol: str = "005930",
    name: str = "삼성전자",
    side: str = "buy",
    price: Decimal = Decimal("70000"),
    quantity: int = 10,
    amount: Decimal = Decimal("700000"),
    broker: str = "kiwoom",
    account_type: str = "개인",
    market: str = "KOSPI",
) -> TradeRow:
    return TradeRow(
        symbol=symbol,
        name=name,
        side=side,
        traded_at=datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc),
        price=price,
        quantity=quantity,
        amount=amount,
        broker=broker,
        account_type=account_type,
        market=market,
        fees=None,
    )


def _make_trade_model(
    trade_id: int = 1,
    symbol: str = "005930",
    name: str = "삼성전자",
    side: str = "buy",
    broker: str = "kiwoom",
) -> MagicMock:
    """Return a MagicMock that looks like a Trade ORM object."""
    t = MagicMock(spec=Trade)
    t.id = trade_id
    t.symbol = symbol
    t.name = name
    t.side = side
    t.traded_at = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
    t.price = Decimal("70000")
    t.quantity = 10
    t.amount = Decimal("700000")
    t.broker = broker
    t.account_type = "개인"
    t.market = "KOSPI"
    t.fees = None
    t.reason = None
    t.review = None
    t.created_at = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    return t


def _mock_db_session():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock()
    return session


def _override_db(session):
    """Return an async generator that yields the given session."""
    async def _dep():
        yield session
    return _dep


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """TestClient with overridden DB dependency."""
    session = _mock_db_session()
    # Default: execute returns empty result
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=[])
    result.scalars = MagicMock(return_value=scalars)
    result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute.return_value = result

    app.dependency_overrides[get_db] = _override_db(session)
    with TestClient(app) as c:
        yield c, session
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/trades/upload — dry_run
# ---------------------------------------------------------------------------

class TestUploadDryRun:
    def test_dry_run_returns_preview_without_db_save(self):
        """dry_run=true should return parsed rows, not call upsert_trades."""
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

            with TestClient(app) as client:
                resp = client.post(
                    "/api/trades/upload?dry_run=true",
                    files={"file": ("test.csv", b"dummy,csv,content", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["inserted"] == 0
        assert data["skipped"] == 0
        assert data["preview"] is not None
        assert len(data["preview"]) == 1
        assert data["preview"][0]["symbol"] == "005930"
        mock_upsert.assert_not_called()

    def test_dry_run_with_multiple_rows(self):
        """Multiple parsed rows should all appear in preview."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)

        rows = [
            _make_trade_row(symbol="005930", name="삼성전자"),
            _make_trade_row(symbol="000660", name="SK하이닉스", side="sell"),
        ]

        with (
            patch("api.routers.trades.detect_broker", return_value="mirae"),
            patch("api.routers.trades.get_parser") as mock_get_parser,
            patch("api.routers.trades.upsert_trades") as mock_upsert,
        ):
            parser = MagicMock()
            parser.parse = MagicMock(return_value=rows)
            mock_get_parser.return_value = parser

            with TestClient(app) as client:
                resp = client.post(
                    "/api/trades/upload?dry_run=true",
                    files={"file": ("test.csv", b"data", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["preview"]) == 2
        mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/trades/upload — actual save
# ---------------------------------------------------------------------------

class TestUploadSave:
    def test_upload_saves_rows_and_returns_counts(self):
        """Without dry_run, upsert_trades should be called and counts returned."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)

        rows = [_make_trade_row()]

        with (
            patch("api.routers.trades.detect_broker", return_value="kiwoom"),
            patch("api.routers.trades.get_parser") as mock_get_parser,
            patch("api.routers.trades.upsert_trades", new_callable=AsyncMock) as mock_upsert,
        ):
            parser = MagicMock()
            parser.parse = MagicMock(return_value=rows)
            mock_get_parser.return_value = parser
            mock_upsert.return_value = {"inserted": 1, "skipped": 0}

            with TestClient(app) as client:
                resp = client.post(
                    "/api/trades/upload",
                    files={"file": ("test.csv", b"data", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert data["inserted"] == 1
        assert data["skipped"] == 0
        assert data["preview"] is None
        mock_upsert.assert_called_once()

    def test_upload_with_explicit_broker_param(self):
        """Explicit broker= query param overrides auto-detection."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)

        rows = [_make_trade_row(broker="samsung")]

        with (
            patch("api.routers.trades.detect_broker") as mock_detect,
            patch("api.routers.trades.get_parser") as mock_get_parser,
            patch("api.routers.trades.upsert_trades", new_callable=AsyncMock) as mock_upsert,
        ):
            parser = MagicMock()
            parser.parse = MagicMock(return_value=rows)
            mock_get_parser.return_value = parser
            mock_upsert.return_value = {"inserted": 1, "skipped": 0}

            with TestClient(app) as client:
                resp = client.post(
                    "/api/trades/upload?broker=samsung",
                    files={"file": ("test.csv", b"data", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        # detect_broker should not have been called (broker was explicit)
        mock_detect.assert_not_called()
        mock_get_parser.assert_called_once_with("samsung")

    def test_upload_unknown_broker_returns_422(self):
        """When broker cannot be detected, API should return 422."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)

        with (
            patch("api.routers.trades.detect_broker", return_value="unknown"),
        ):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/trades/upload",
                    files={"file": ("test.csv", b"unknown,data", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 422

    def test_upload_parse_error_returns_422(self):
        """If parser raises, API should return 422."""
        session = _mock_db_session()
        app.dependency_overrides[get_db] = _override_db(session)

        with (
            patch("api.routers.trades.detect_broker", return_value="kiwoom"),
            patch("api.routers.trades.get_parser") as mock_get_parser,
        ):
            parser = MagicMock()
            parser.parse = MagicMock(side_effect=ValueError("bad csv"))
            mock_get_parser.return_value = parser

            with TestClient(app) as client:
                resp = client.post(
                    "/api/trades/upload",
                    files={"file": ("bad.csv", b"garbage", "text/csv")},
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/trades
# ---------------------------------------------------------------------------

class TestListTrades:
    def test_returns_paginated_response_shape(self, client):
        """Response must be { items, total, limit, offset }, not a plain array."""
        c, session = client
        with (
            patch("api.routers.trades.get_trades", new_callable=AsyncMock, return_value=[]),
            patch("api.routers.trades.count_trades", new_callable=AsyncMock, return_value=0),
        ):
            resp = c.get("/api/trades")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert data["items"] == []
        assert data["total"] == 0

    def test_returns_empty_items_when_no_trades(self, client):
        c, session = client
        with (
            patch("api.routers.trades.get_trades", new_callable=AsyncMock, return_value=[]) as mock_get,
            patch("api.routers.trades.count_trades", new_callable=AsyncMock, return_value=0),
        ):
            resp = c.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json()["items"] == []
        assert resp.json()["total"] == 0
        mock_get.assert_called_once()

    def test_returns_trade_list_in_items(self, client):
        c, session = client
        trade = _make_trade_model(trade_id=1)
        with (
            patch("api.routers.trades.get_trades", new_callable=AsyncMock, return_value=[trade]),
            patch("api.routers.trades.count_trades", new_callable=AsyncMock, return_value=1),
        ):
            resp = c.get("/api/trades")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == 1
        assert data["items"][0]["symbol"] == "005930"

    def test_total_reflects_filtered_count_not_page_size(self, client):
        """total should be the count of all matching records, not just the page."""
        c, session = client
        trade = _make_trade_model(trade_id=1)
        # 50 total records but only 1 returned on this page
        with (
            patch("api.routers.trades.get_trades", new_callable=AsyncMock, return_value=[trade]),
            patch("api.routers.trades.count_trades", new_callable=AsyncMock, return_value=50),
        ):
            resp = c.get("/api/trades?limit=1&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 50
        assert len(data["items"]) == 1

    def test_limit_and_offset_echoed_in_response(self, client):
        c, _ = client
        with (
            patch("api.routers.trades.get_trades", new_callable=AsyncMock, return_value=[]),
            patch("api.routers.trades.count_trades", new_callable=AsyncMock, return_value=0),
        ):
            resp = c.get("/api/trades?limit=25&offset=50")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 25
        assert data["offset"] == 50

    def test_query_params_passed_to_filters(self, client):
        """Filter query params should be forwarded to get_trades."""
        c, session = client
        captured = {}

        async def mock_get_trades(db, filters: TradeFilters | None = None):
            captured["filters"] = filters
            return []

        with (
            patch("api.routers.trades.get_trades", side_effect=mock_get_trades),
            patch("api.routers.trades.count_trades", new_callable=AsyncMock, return_value=0),
        ):
            resp = c.get("/api/trades?symbol=005930&side=buy&limit=10&offset=5")

        assert resp.status_code == 200
        f = captured["filters"]
        assert f.symbol == "005930"
        assert f.side == "buy"
        assert f.limit == 10
        assert f.offset == 5

    def test_invalid_limit_returns_422(self, client):
        c, _ = client
        resp = c.get("/api/trades?limit=0")
        assert resp.status_code == 422

    def test_limit_too_large_returns_422(self, client):
        c, _ = client
        resp = c.get("/api/trades?limit=501")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/trades/{id}
# ---------------------------------------------------------------------------

class TestGetTradeDetail:
    def test_returns_trade_detail(self, client):
        c, session = client
        trade = _make_trade_model(trade_id=42)

        # session.execute returns nothing for the TradeIndicator sub-query
        ind_result = MagicMock()
        ind_result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute.return_value = ind_result

        with patch("api.routers.trades.get_trade", new_callable=AsyncMock, return_value=trade):
            resp = c.get("/api/trades/42")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 42
        assert data["symbol"] == "005930"
        assert data["indicator"] is None

    def test_returns_404_when_not_found(self, client):
        c, _ = client
        with patch("api.routers.trades.get_trade", new_callable=AsyncMock, return_value=None):
            resp = c.get("/api/trades/999")
        assert resp.status_code == 404

    def test_indicator_included_when_present(self, client):
        c, session = client
        trade = _make_trade_model(trade_id=5)

        ind = MagicMock(spec=TradeIndicator)
        ind.id = 1
        ind.trade_id = 5
        ind.stoch_k_d = None
        ind.rsi_14 = Decimal("55.00")
        ind.macd = None
        ind.ma_position = None
        ind.bb_position = None
        ind.volume_ratio = None
        ind.snapshot_text = None

        ind_result = MagicMock()
        ind_result.scalar_one_or_none = MagicMock(return_value=ind)
        session.execute.return_value = ind_result

        with patch("api.routers.trades.get_trade", new_callable=AsyncMock, return_value=trade):
            resp = c.get("/api/trades/5")

        assert resp.status_code == 200
        data = resp.json()
        assert data["indicator"] is not None
        assert data["indicator"]["trade_id"] == 5
        assert data["indicator"]["rsi_14"] == "55.00"


# ---------------------------------------------------------------------------
# PATCH /api/trades/{id}/reason
# ---------------------------------------------------------------------------

class TestPatchReason:
    def test_updates_reason(self, client):
        c, _ = client
        trade = _make_trade_model(trade_id=1)
        trade.reason = "좋은 진입"

        with patch(
            "api.routers.trades.update_trade_reason",
            new_callable=AsyncMock,
            return_value=trade,
        ):
            resp = c.patch("/api/trades/1/reason", json={"reason": "좋은 진입"})

        assert resp.status_code == 200
        assert resp.json()["reason"] == "좋은 진입"

    def test_missing_reason_returns_400(self, client):
        c, _ = client
        resp = c.patch("/api/trades/1/reason", json={"review": "no reason here"})
        assert resp.status_code == 400

    def test_trade_not_found_returns_404(self, client):
        c, _ = client
        with patch(
            "api.routers.trades.update_trade_reason",
            new_callable=AsyncMock,
            side_effect=ValueError("Trade 999 not found"),
        ):
            resp = c.patch("/api/trades/999/reason", json={"reason": "test"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/trades/{id}/review
# ---------------------------------------------------------------------------

class TestPatchReview:
    def test_updates_review(self, client):
        c, _ = client
        trade = _make_trade_model(trade_id=2)
        trade.review = "손절 타이밍 늦었음"

        with patch(
            "api.routers.trades.update_trade_review",
            new_callable=AsyncMock,
            return_value=trade,
        ):
            resp = c.patch("/api/trades/2/review", json={"review": "손절 타이밍 늦었음"})

        assert resp.status_code == 200
        assert resp.json()["review"] == "손절 타이밍 늦었음"

    def test_missing_review_returns_400(self, client):
        c, _ = client
        resp = c.patch("/api/trades/2/review", json={"reason": "not a review"})
        assert resp.status_code == 400

    def test_trade_not_found_returns_404(self, client):
        c, _ = client
        with patch(
            "api.routers.trades.update_trade_review",
            new_callable=AsyncMock,
            side_effect=ValueError("Trade 999 not found"),
        ):
            resp = c.patch("/api/trades/999/review", json={"review": "test"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/trades/stats
# ---------------------------------------------------------------------------

class TestTradeStats:
    def test_returns_stats(self, client):
        c, _ = client
        expected = {
            "total_count": 10,
            "buy_count": 6,
            "sell_count": 4,
            "total_amount": Decimal("5000000"),
            "symbol_frequency": [{"symbol": "005930", "name": "삼성전자", "count": 5}],
        }
        with patch(
            "api.routers.trades.get_trade_stats",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            resp = c.get("/api/trades/stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 10
        assert data["buy_count"] == 6
        assert data["sell_count"] == 4
        assert Decimal(data["total_amount"]) == Decimal("5000000")
        assert len(data["symbol_frequency"]) == 1

    def test_stats_with_filters(self, client):
        """Query params should be forwarded as TradeFilters."""
        c, _ = client
        captured = {}

        async def mock_stats(db, filters=None):
            captured["filters"] = filters
            return {
                "total_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "total_amount": Decimal("0"),
                "symbol_frequency": [],
            }

        with patch("api.routers.trades.get_trade_stats", side_effect=mock_stats):
            resp = c.get("/api/trades/stats?symbol=005930&broker=kiwoom")

        assert resp.status_code == 200
        assert captured["filters"].symbol == "005930"
        assert captured["filters"].broker == "kiwoom"


# ---------------------------------------------------------------------------
# GET /api/trades/chart-data
# ---------------------------------------------------------------------------

class TestChartData:
    def test_requires_symbol(self, client):
        c, _ = client
        resp = c.get("/api/trades/chart-data")
        assert resp.status_code == 422

    def test_returns_trade_list_for_symbol(self, client):
        c, _ = client
        trade = _make_trade_model(trade_id=10)

        with patch(
            "api.routers.trades.get_chart_data",
            new_callable=AsyncMock,
            return_value=[trade],
        ):
            resp = c.get("/api/trades/chart-data?symbol=005930")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "005930"

    def test_chart_data_empty(self, client):
        c, _ = client
        with patch(
            "api.routers.trades.get_chart_data",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = c.get("/api/trades/chart-data?symbol=999999")

        assert resp.status_code == 200
        assert resp.json() == []
