"""Unit tests for api/routers/analysis.py.

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
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    return result


def _make_row(**kwargs):
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
# GET /api/analysis/sectors
# ---------------------------------------------------------------------------

class TestListSectors:

    def test_returns_sector_list_shape(self, client_and_session):
        """Response must have items field."""
        c, session = client_and_session
        session.execute.side_effect = [
            _make_execute_result([]),  # sector aggregation
        ]

        resp = c.get("/api/analysis/sectors")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["items"] == []

    def test_returns_sector_items(self, client_and_session):
        c, session = client_and_session

        sector_row = _make_row(
            sector="반도체",
            report_count=15,
            avg_sentiment=0.6,
        )
        top_stock_row = _make_row(
            sector="반도체",
            stock_code="005930",
            company_name="삼성전자",
            cnt=10,
        )

        session.execute.side_effect = [
            _make_execute_result([sector_row]),   # sectors query
            _make_execute_result([top_stock_row]),  # top stocks query
        ]

        resp = c.get("/api/analysis/sectors")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["sector_name"] == "반도체"
        assert item["report_count"] == 15
        assert item["avg_sentiment"] == pytest.approx(0.6)
        assert len(item["top_stocks"]) == 1
        assert item["top_stocks"][0]["stock_code"] == "005930"
        assert item["top_stocks"][0]["stock_name"] == "삼성전자"
        assert item["top_stocks"][0]["report_count"] == 10

    def test_top_stocks_limited_to_5(self, client_and_session):
        """Each sector should have at most 5 top stocks."""
        c, session = client_and_session

        sector_row = _make_row(sector="IT", report_count=100, avg_sentiment=0.5)
        top_stock_rows = [
            _make_row(sector="IT", stock_code=f"00000{i}", company_name=f"종목{i}", cnt=10 - i)
            for i in range(8)
        ]

        session.execute.side_effect = [
            _make_execute_result([sector_row]),
            _make_execute_result(top_stock_rows),
        ]

        resp = c.get("/api/analysis/sectors")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert len(item["top_stocks"]) == 5

    def test_avg_sentiment_null_when_none(self, client_and_session):
        c, session = client_and_session

        sector_row = _make_row(sector="금융", report_count=5, avg_sentiment=None)

        session.execute.side_effect = [
            _make_execute_result([sector_row]),
            _make_execute_result([]),
        ]

        resp = c.get("/api/analysis/sectors")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["avg_sentiment"] is None

    def test_multiple_sectors(self, client_and_session):
        c, session = client_and_session

        rows = [
            _make_row(sector="반도체", report_count=20, avg_sentiment=0.7),
            _make_row(sector="자동차", report_count=10, avg_sentiment=0.3),
        ]
        session.execute.side_effect = [
            _make_execute_result(rows),
            _make_execute_result([]),
        ]

        resp = c.get("/api/analysis/sectors")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["sector_name"] == "반도체"
        assert data["items"][1]["sector_name"] == "자동차"

    def test_top_stocks_empty_when_no_valid_codes(self, client_and_session):
        """If no valid codes in sector, top_stocks should be empty."""
        c, session = client_and_session

        sector_row = _make_row(sector="매크로", report_count=3, avg_sentiment=None)
        session.execute.side_effect = [
            _make_execute_result([sector_row]),
            _make_execute_result([]),  # no valid stock codes
        ]

        resp = c.get("/api/analysis/sectors")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["top_stocks"] == []

    def test_top_stocks_dedup_by_code(self, client_and_session):
        """Duplicate stock_code entries in the sector should be deduplicated."""
        c, session = client_and_session

        sector_row = _make_row(sector="화학", report_count=5, avg_sentiment=0.4)
        # Same stock code with different company_name entries
        top_rows = [
            _make_row(sector="화학", stock_code="051910", company_name="LG화학", cnt=3),
            _make_row(sector="화학", stock_code="051910", company_name="LG화학", cnt=2),
        ]
        session.execute.side_effect = [
            _make_execute_result([sector_row]),
            _make_execute_result(top_rows),
        ]

        resp = c.get("/api/analysis/sectors")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert len(item["top_stocks"]) == 1
        assert item["top_stocks"][0]["stock_code"] == "051910"


# ---------------------------------------------------------------------------
# GET /api/analysis/sector/{name}
# ---------------------------------------------------------------------------

class TestGetSectorStocks:

    def test_sector_not_found_returns_404(self, client_and_session):
        c, session = client_and_session
        session.scalar.return_value = 0
        resp = c.get("/api/analysis/sector/없는섹터")
        assert resp.status_code == 404

    def test_returns_sector_stock_response_shape(self, client_and_session):
        c, session = client_and_session
        session.scalar.return_value = 5

        stocks_row = _make_row(
            stock_code="005930",
            company_name="삼성전자",
            report_count=8,
            avg_sentiment=0.75,
            latest_date=date(2026, 3, 1),
        )
        opinion_row = _make_row(
            stock_code="005930",
            opinion="매수",
            target_price=90000,
        )

        session.execute.side_effect = [
            _make_execute_result([stocks_row]),
            _make_execute_result([opinion_row]),
        ]

        resp = c.get("/api/analysis/sector/반도체")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sector_name"] == "반도체"
        assert "items" in data
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["stock_code"] == "005930"
        assert item["stock_name"] == "삼성전자"
        assert item["report_count"] == 8
        assert item["avg_sentiment"] == pytest.approx(0.75)
        assert item["latest_opinion"] == "매수"
        assert item["latest_target_price"] == 90000

    def test_empty_sector_returns_empty_items(self, client_and_session):
        c, session = client_and_session
        session.scalar.return_value = 3  # sector exists

        session.execute.side_effect = [
            _make_execute_result([]),  # no stock mentions
        ]

        resp = c.get("/api/analysis/sector/매크로")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sector_name"] == "매크로"
        assert data["items"] == []

    def test_avg_sentiment_null_when_none(self, client_and_session):
        c, session = client_and_session
        session.scalar.return_value = 2

        stocks_row = _make_row(
            stock_code="035420",
            company_name="NAVER",
            report_count=2,
            avg_sentiment=None,
            latest_date=date(2026, 1, 1),
        )
        opinion_row = _make_row(stock_code="035420", opinion=None, target_price=None)

        session.execute.side_effect = [
            _make_execute_result([stocks_row]),
            _make_execute_result([opinion_row]),
        ]

        resp = c.get("/api/analysis/sector/인터넷")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["avg_sentiment"] is None
        assert item["latest_opinion"] is None
        assert item["latest_target_price"] is None

    def test_multiple_stocks_in_sector(self, client_and_session):
        c, session = client_and_session
        session.scalar.return_value = 10

        stocks_rows = [
            _make_row(stock_code="005930", company_name="삼성전자", report_count=8, avg_sentiment=0.7, latest_date=date(2026, 3, 1)),
            _make_row(stock_code="000660", company_name="SK하이닉스", report_count=5, avg_sentiment=0.5, latest_date=date(2026, 2, 1)),
        ]
        opinion_rows = [
            _make_row(stock_code="005930", opinion="매수", target_price=90000),
            _make_row(stock_code="000660", opinion="중립", target_price=150000),
        ]

        session.execute.side_effect = [
            _make_execute_result(stocks_rows),
            _make_execute_result(opinion_rows),
        ]

        resp = c.get("/api/analysis/sector/반도체")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        codes = {item["stock_code"] for item in items}
        assert codes == {"005930", "000660"}

    def test_sector_name_with_special_chars(self, client_and_session):
        """Sector names with Korean/spaces should work."""
        c, session = client_and_session
        session.scalar.return_value = 0
        resp = c.get("/api/analysis/sector/2차전지")
        assert resp.status_code == 404

    def test_stock_without_opinion_in_map(self, client_and_session):
        """If a stock has no entry in opinion_map, opinion/target_price should be None."""
        c, session = client_and_session
        session.scalar.return_value = 1

        stocks_row = _make_row(
            stock_code="012345",
            company_name="테스트회사",
            report_count=1,
            avg_sentiment=0.3,
            latest_date=date(2026, 1, 1),
        )

        session.execute.side_effect = [
            _make_execute_result([stocks_row]),
            _make_execute_result([]),  # no opinion rows
        ]

        resp = c.get("/api/analysis/sector/테스트")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["latest_opinion"] is None
        assert item["latest_target_price"] is None


# ---------------------------------------------------------------------------
# Schema unit tests
# ---------------------------------------------------------------------------

class TestAnalysisSchemas:

    def test_sector_list_item_schema(self):
        from api.schemas import SectorListItem, SectorTopStock

        item = SectorListItem(
            sector_name="반도체",
            report_count=10,
            avg_sentiment=0.65,
            top_stocks=[
                SectorTopStock(stock_code="005930", stock_name="삼성전자", report_count=5)
            ],
        )
        assert item.sector_name == "반도체"
        assert item.report_count == 10
        assert item.avg_sentiment == pytest.approx(0.65)
        assert len(item.top_stocks) == 1

    def test_sector_stock_item_schema(self):
        from api.schemas import SectorStockItem

        item = SectorStockItem(
            stock_code="005930",
            stock_name="삼성전자",
            report_count=8,
            avg_sentiment=0.7,
            latest_opinion="매수",
            latest_target_price=90000,
        )
        assert item.stock_code == "005930"
        assert item.latest_opinion == "매수"
        assert item.latest_target_price == 90000

    def test_sector_stock_item_nullable_fields(self):
        from api.schemas import SectorStockItem

        item = SectorStockItem(
            stock_code="000660",
            stock_name=None,
            report_count=3,
            avg_sentiment=None,
            latest_opinion=None,
            latest_target_price=None,
        )
        assert item.stock_name is None
        assert item.avg_sentiment is None
        assert item.latest_opinion is None
        assert item.latest_target_price is None

    def test_stock_list_item_schema(self):
        from api.schemas import StockListItem

        item = StockListItem(
            stock_code="005930",
            stock_name="삼성전자",
            report_count=15,
            latest_report_date=date(2026, 3, 1),
            avg_sentiment=0.8,
        )
        assert item.stock_code == "005930"
        assert item.report_count == 15

    def test_stock_history_item_schema(self):
        from api.schemas import StockHistoryItem

        item = StockHistoryItem(
            report_id=42,
            broker="신한투자증권",
            report_date=date(2026, 3, 1),
            title="삼성전자 분석",
            opinion="매수",
            target_price=90000,
            layer2_summary="투자 논리",
            layer2_sentiment=0.8,
        )
        assert item.report_id == 42
        assert item.layer2_sentiment == pytest.approx(0.8)
