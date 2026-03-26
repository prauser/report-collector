"""Unit tests for api/routers/stocks.py.

Uses FastAPI TestClient with mocked DB sessions.
No live DB required.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_db_session():
    session = MagicMock()
    session.execute = AsyncMock()
    session.scalar = AsyncMock()
    session.commit = AsyncMock()
    return session


def _override_db(session):
    async def _dep():
        yield session
    return _dep


def _make_execute_result(rows):
    """Create a mock execute() result that returns the given rows from .all()."""
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    return result


def _make_row(**kwargs):
    """Make a named-tuple-like object for query result rows."""
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client_and_session():
    session = _mock_db_session()
    app.dependency_overrides[get_db] = _override_db(session)
    with TestClient(app) as c:
        yield c, session
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /api/stocks
# ---------------------------------------------------------------------------

class TestListStocks:

    def test_returns_stock_list_response_shape(self, client_and_session):
        """Response must have total, limit, offset, items fields."""
        c, session = client_and_session

        # scalar() for total count
        session.scalar.return_value = 0
        # execute() for main query and name query
        empty_result = _make_execute_result([])
        session.execute.return_value = empty_result

        resp = c.get("/api/stocks")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert "items" in data
        assert data["items"] == []
        assert data["total"] == 0

    def test_returns_stock_items_when_data_present(self, client_and_session):
        """When rows exist, items should be populated."""
        c, session = client_and_session

        stock_row = _make_row(
            stock_code="005930",
            report_count=10,
            latest_report_date=date(2026, 3, 1),
            avg_sentiment=0.7,
        )
        name_row = _make_row(stock_code="005930", company_name="삼성전자")

        # Call sequence:
        # 1. scalar() → total count
        # 2. execute() → paged aggregation rows
        # 3. execute() → name lookup rows
        session.scalar.return_value = 1
        session.execute.side_effect = [
            _make_execute_result([stock_row]),
            _make_execute_result([name_row]),
        ]

        resp = c.get("/api/stocks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["stock_code"] == "005930"
        assert item["stock_name"] == "삼성전자"
        assert item["report_count"] == 10
        assert item["avg_sentiment"] == pytest.approx(0.7)

    def test_default_limit_and_offset_in_response(self, client_and_session):
        c, session = client_and_session
        session.scalar.return_value = 0
        session.execute.return_value = _make_execute_result([])

        resp = c.get("/api/stocks")
        data = resp.json()
        assert data["limit"] == 50
        assert data["offset"] == 0

    def test_custom_limit_offset_reflected(self, client_and_session):
        c, session = client_and_session
        session.scalar.return_value = 0
        session.execute.return_value = _make_execute_result([])

        resp = c.get("/api/stocks?limit=10&offset=20")
        data = resp.json()
        assert data["limit"] == 10
        assert data["offset"] == 20

    def test_limit_too_large_returns_422(self, client_and_session):
        c, _ = client_and_session
        resp = c.get("/api/stocks?limit=201")
        assert resp.status_code == 422

    def test_limit_zero_returns_422(self, client_and_session):
        c, _ = client_and_session
        resp = c.get("/api/stocks?limit=0")
        assert resp.status_code == 422

    def test_negative_offset_returns_422(self, client_and_session):
        c, _ = client_and_session
        resp = c.get("/api/stocks?offset=-1")
        assert resp.status_code == 422

    def test_no_stocks_returns_empty_items(self, client_and_session):
        c, session = client_and_session
        session.scalar.return_value = 0
        session.execute.return_value = _make_execute_result([])

        resp = c.get("/api/stocks")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_avg_sentiment_none_when_null(self, client_and_session):
        """avg_sentiment=None should serialize as null."""
        c, session = client_and_session

        stock_row = _make_row(
            stock_code="000660",
            report_count=3,
            latest_report_date=date(2026, 2, 1),
            avg_sentiment=None,
        )
        name_row = _make_row(stock_code="000660", company_name="SK하이닉스")

        session.scalar.return_value = 1
        session.execute.side_effect = [
            _make_execute_result([stock_row]),
            _make_execute_result([name_row]),
        ]

        resp = c.get("/api/stocks")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["avg_sentiment"] is None

    def test_stock_name_none_when_not_in_name_map(self, client_and_session):
        """If name lookup returns nothing, stock_name should be None."""
        c, session = client_and_session

        stock_row = _make_row(
            stock_code="123456",
            report_count=1,
            latest_report_date=date(2026, 1, 1),
            avg_sentiment=None,
        )

        session.scalar.return_value = 1
        session.execute.side_effect = [
            _make_execute_result([stock_row]),
            _make_execute_result([]),  # no name rows
        ]

        resp = c.get("/api/stocks")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["stock_name"] is None

    def test_search_returns_matching_items(self, client_and_session):
        """search parameter filters results; matching items are returned."""
        c, session = client_and_session

        stock_row = _make_row(
            stock_code="005930",
            report_count=5,
            latest_report_date=date(2026, 3, 1),
            avg_sentiment=0.6,
        )
        name_row = _make_row(stock_code="005930", company_name="삼성전자")

        session.scalar.return_value = 1
        session.execute.side_effect = [
            _make_execute_result([stock_row]),
            _make_execute_result([name_row]),
        ]

        resp = c.get("/api/stocks?search=삼성")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["stock_code"] == "005930"

    def test_search_no_results_returns_empty(self, client_and_session):
        """search with no matching results returns empty items list."""
        c, session = client_and_session

        session.scalar.return_value = 0
        session.execute.return_value = _make_execute_result([])

        resp = c.get("/api/stocks?search=없는종목XYZ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_sort_latest_date_accepted(self, client_and_session):
        """sort=latest_date is a valid parameter and returns 200."""
        c, session = client_and_session

        stock_rows = [
            _make_row(stock_code="005930", report_count=5, latest_report_date=date(2026, 3, 1), avg_sentiment=0.7),
            _make_row(stock_code="000660", report_count=3, latest_report_date=date(2026, 3, 5), avg_sentiment=0.5),
        ]
        name_rows = [
            _make_row(stock_code="005930", company_name="삼성전자"),
            _make_row(stock_code="000660", company_name="SK하이닉스"),
        ]

        session.scalar.return_value = 2
        session.execute.side_effect = [
            _make_execute_result(stock_rows),
            _make_execute_result(name_rows),
        ]

        resp = c.get("/api/stocks?sort=latest_date")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2

    def test_search_with_like_metacharacters_does_not_error(self, client_and_session):
        """search containing % or _ should not cause an error (metacharacters are escaped)."""
        c, session = client_and_session

        session.scalar.return_value = 0
        session.execute.return_value = _make_execute_result([])

        # These would break an unescaped LIKE pattern but should be handled safely
        resp = c.get("/api/stocks?search=100%25return")
        assert resp.status_code == 200
        resp2 = c.get("/api/stocks?search=some_name")
        assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/stocks/{code}/history
# ---------------------------------------------------------------------------

class TestStockHistory:

    def test_invalid_code_returns_400(self, client_and_session):
        """Non-6-digit code should return 400."""
        c, _ = client_and_session
        resp = c.get("/api/stocks/ABC/history")
        assert resp.status_code == 400

    def test_five_digit_code_returns_400(self, client_and_session):
        c, _ = client_and_session
        resp = c.get("/api/stocks/12345/history")
        assert resp.status_code == 400

    def test_seven_digit_code_returns_400(self, client_and_session):
        c, _ = client_and_session
        resp = c.get("/api/stocks/1234567/history")
        assert resp.status_code == 400

    def test_six_char_non_numeric_code_returns_400(self, client_and_session):
        """6-character non-numeric stock code (e.g. ABCDEF) should return 400."""
        c, _ = client_and_session
        resp = c.get("/api/stocks/ABCDEF/history")
        assert resp.status_code == 400

    def test_valid_code_not_in_db_returns_404(self, client_and_session):
        """Valid format but no data → 404."""
        c, session = client_and_session
        session.scalar.return_value = 0  # total count returns 0
        resp = c.get("/api/stocks/999999/history")
        assert resp.status_code == 404

    def test_returns_history_response_shape(self, client_and_session):
        c, session = client_and_session

        report = MagicMock()
        report.id = 42
        report.broker = "신한투자증권"
        report.report_date = date(2026, 3, 1)
        report.title = "삼성전자 분석"
        report.opinion = "매수"
        report.target_price = 90000

        ra = MagicMock()
        ra.analysis_data = {"thesis": {"summary": "투자 논리", "sentiment": 0.8}}

        # scalar calls: 1st = total count, 2nd = stock name lookup
        session.scalar.side_effect = [5, "삼성전자"]
        # execute: reports query
        rows = [(report, ra)]
        result = MagicMock()
        result.all = MagicMock(return_value=rows)
        session.execute.return_value = result

        resp = c.get("/api/stocks/005930/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stock_code"] == "005930"
        assert data["stock_name"] == "삼성전자"
        assert "total" in data
        assert "items" in data
        assert data["total"] == 5
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["report_id"] == 42
        assert item["broker"] == "신한투자증권"
        assert item["opinion"] == "매수"
        assert item["target_price"] == 90000
        assert item["layer2_summary"] == "투자 논리"
        assert item["layer2_sentiment"] == pytest.approx(0.8)

    def test_no_analysis_gives_null_layer2_fields(self, client_and_session):
        c, session = client_and_session

        report = MagicMock()
        report.id = 1
        report.broker = "키움증권"
        report.report_date = date(2026, 1, 1)
        report.title = "리포트"
        report.opinion = None
        report.target_price = None

        session.scalar.side_effect = [1, None]  # total=1, stock_name=None
        rows = [(report, None)]  # ra=None
        result = MagicMock()
        result.all = MagicMock(return_value=rows)
        session.execute.return_value = result

        resp = c.get("/api/stocks/123456/history")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["layer2_summary"] is None
        assert item["layer2_sentiment"] is None

    def test_limit_offset_in_response(self, client_and_session):
        c, session = client_and_session

        session.scalar.side_effect = [10, None]  # total=10, stock_name=None
        result = MagicMock()
        result.all = MagicMock(return_value=[])
        session.execute.return_value = result

        resp = c.get("/api/stocks/005930/history?limit=5&offset=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 5
        assert data["offset"] == 10

    def test_history_with_empty_thesis(self, client_and_session):
        """Empty thesis dict → null layer2 fields."""
        c, session = client_and_session

        report = MagicMock()
        report.id = 2
        report.broker = "미래에셋"
        report.report_date = date(2026, 2, 1)
        report.title = "리포트2"
        report.opinion = "중립"
        report.target_price = 50000

        ra = MagicMock()
        ra.analysis_data = {}

        session.scalar.side_effect = [1, None]  # total=1, stock_name=None
        rows = [(report, ra)]
        result = MagicMock()
        result.all = MagicMock(return_value=rows)
        session.execute.return_value = result

        resp = c.get("/api/stocks/005930/history")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["layer2_summary"] is None
        assert item["layer2_sentiment"] is None


# ---------------------------------------------------------------------------
# stock_code validation logic (unit test without HTTP)
# ---------------------------------------------------------------------------

class TestStockCodeValidation:

    def test_valid_six_digit_codes(self):
        from api.routers.stocks import _is_valid_code
        assert _is_valid_code("005930") is True
        assert _is_valid_code("000660") is True
        assert _is_valid_code("123456") is True

    def test_invalid_codes(self):
        from api.routers.stocks import _is_valid_code
        assert _is_valid_code("ABC") is False
        assert _is_valid_code("12345") is False
        assert _is_valid_code("1234567") is False
        assert _is_valid_code("") is False
        assert _is_valid_code("삼성전자") is False
        assert _is_valid_code("00593X") is False
        # pseudo-codes from company_name[:20]
        assert _is_valid_code("삼성전자주식회사") is False
        assert _is_valid_code("SK하이닉스") is False
