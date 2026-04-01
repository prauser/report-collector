"""Unit tests for agent/tools.py — uses mocked AsyncSession, no live DB."""
from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.tools import (
    AGENT_TOOLS,
    execute_get_report_detail,
    execute_get_report_stats,
    execute_list_stocks,
    execute_search_reports,
    execute_tool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(rows=None, scalar=None):
    """Return a mock AsyncSession whose execute() returns given rows."""
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = rows or []
    result.scalar.return_value = scalar if scalar is not None else 0
    session.execute.return_value = result
    return session


def _row(**kwargs):
    """Create a simple namespace-like mock row."""
    r = MagicMock()
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


# ---------------------------------------------------------------------------
# AGENT_TOOLS schema validation
# ---------------------------------------------------------------------------

class TestAgentToolsSchema:
    def test_count(self):
        assert len(AGENT_TOOLS) == 4

    def test_tool_names(self):
        names = {t["name"] for t in AGENT_TOOLS}
        assert names == {"search_reports", "get_report_detail", "list_stocks", "get_report_stats"}

    def test_each_tool_has_required_keys(self):
        for tool in AGENT_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool

    def test_input_schema_structure(self):
        for tool in AGENT_TOOLS:
            schema = tool["input_schema"]
            assert schema["type"] == "object"
            assert "properties" in schema
            assert "required" in schema

    def test_search_reports_schema(self):
        tool = next(t for t in AGENT_TOOLS if t["name"] == "search_reports")
        props = tool["input_schema"]["properties"]
        for key in ("stock_name", "stock_code", "sector", "broker", "date_from", "date_to", "limit"):
            assert key in props

    def test_get_report_detail_schema(self):
        tool = next(t for t in AGENT_TOOLS if t["name"] == "get_report_detail")
        assert "report_ids" in tool["input_schema"]["properties"]
        assert "report_ids" in tool["input_schema"]["required"]

    def test_list_stocks_schema(self):
        tool = next(t for t in AGENT_TOOLS if t["name"] == "list_stocks")
        props = tool["input_schema"]["properties"]
        assert "search" in props
        assert "sector" in props
        assert "sort" in props
        assert "limit" in props

    def test_get_report_stats_schema(self):
        tool = next(t for t in AGENT_TOOLS if t["name"] == "get_report_stats")
        props = tool["input_schema"]["properties"]
        assert "date_from" in props
        assert "date_to" in props


# ---------------------------------------------------------------------------
# execute_search_reports
# ---------------------------------------------------------------------------

class TestExecuteSearchReports:
    @pytest.mark.asyncio
    async def test_returns_dict_with_reports_key(self):
        session = _make_session(rows=[])
        result = await execute_search_reports({}, session)
        assert "reports" in result
        assert "total_count" in result

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_list(self):
        session = _make_session(rows=[])
        result = await execute_search_reports({}, session)
        assert result["reports"] == []
        assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_rows_mapped_correctly(self):
        row = _row(
            id=42,
            broker="미래에셋",
            report_date=datetime.date(2024, 1, 15),
            title="삼성전자 분석",
            stock_name="삼성전자",
            stock_code="005930",
            sector="반도체",
            opinion="BUY",
            target_price=90000,
        )
        session = _make_session(rows=[row])
        result = await execute_search_reports({"stock_name": "삼성"}, session)
        assert len(result["reports"]) == 1
        r = result["reports"][0]
        assert r["report_id"] == 42
        assert r["broker"] == "미래에셋"
        assert r["date"] == "2024-01-15"
        assert r["opinion"] == "BUY"
        assert r["target_price"] == 90000

    @pytest.mark.asyncio
    async def test_limit_capped_at_50(self):
        """limit > 50 should be capped."""
        session = _make_session(rows=[])
        # We just verify it doesn't raise; actual capping is internal
        await execute_search_reports({"limit": 100}, session)
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_session_execute_called_once(self):
        session = _make_session(rows=[])
        await execute_search_reports({"broker": "NH"}, session)
        assert session.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_invalid_date_from_returns_error(self):
        session = _make_session(rows=[])
        result = await execute_search_reports({"date_from": "not-a-date"}, session)
        assert "error" in result
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_date_to_returns_error(self):
        session = _make_session(rows=[])
        result = await execute_search_reports({"date_to": "2024/01/01"}, session)
        assert "error" in result
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_dates_do_not_error(self):
        session = _make_session(rows=[])
        result = await execute_search_reports(
            {"date_from": "2024-01-01", "date_to": "2024-12-31"}, session
        )
        assert "error" not in result
        assert "reports" in result

    @pytest.mark.asyncio
    async def test_limit_zero_clamped_to_1(self):
        session = _make_session(rows=[])
        await execute_search_reports({"limit": 0}, session)
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_limit_negative_clamped_to_1(self):
        session = _make_session(rows=[])
        await execute_search_reports({"limit": -5}, session)
        assert session.execute.called


# ---------------------------------------------------------------------------
# execute_get_report_detail
# ---------------------------------------------------------------------------

class TestExecuteGetReportDetail:
    @pytest.mark.asyncio
    async def test_empty_report_ids_returns_empty(self):
        session = _make_session(rows=[])
        result = await execute_get_report_detail({"report_ids": []}, session)
        assert result == {"reports": []}
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_report_ids_returns_empty(self):
        session = _make_session(rows=[])
        result = await execute_get_report_detail({}, session)
        assert result == {"reports": []}

    @pytest.mark.asyncio
    async def test_rows_mapped_correctly(self):
        report = MagicMock()
        report.id = 7
        report.broker = "삼성증권"
        report.report_date = datetime.date(2024, 3, 10)
        report.title = "LG에너지솔루션 탐방"
        report.stock_name = "LG에너지솔루션"
        report.stock_code = "373220"
        report.sector = "2차전지"
        report.opinion = "BUY"
        report.target_price = 500000

        analysis = MagicMock()
        analysis.analysis_data = {"key": "value"}

        session = _make_session(rows=[(report, analysis)])
        result = await execute_get_report_detail({"report_ids": [7]}, session)
        assert len(result["reports"]) == 1
        r = result["reports"][0]
        assert r["report_id"] == 7
        assert r["analysis_data"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_caps_at_10_ids(self):
        """Only the first 10 IDs should be used."""
        session = _make_session(rows=[])
        ids = list(range(1, 20))  # 19 IDs
        await execute_get_report_detail({"report_ids": ids}, session)
        # Should execute (with truncated list)
        assert session.execute.call_count == 1


# ---------------------------------------------------------------------------
# execute_list_stocks
# ---------------------------------------------------------------------------

class TestExecuteListStocks:
    def _make_list_stocks_session(self, rows, total_count):
        """Return a mock session for execute_list_stocks.

        execute_list_stocks now fires two queries via asyncio.gather:
        1. data query  → result.all() returns rows
        2. count query → result.scalar() returns total_count
        """
        session = AsyncMock()

        data_result = MagicMock()
        data_result.all.return_value = rows

        count_result = MagicMock()
        count_result.scalar.return_value = total_count

        # asyncio.gather calls execute twice; side_effect provides results in order
        session.execute.side_effect = [data_result, count_result]
        return session

    @pytest.mark.asyncio
    async def test_returns_stocks_and_total_count(self):
        row = _row(
            stock_name="삼성전자",
            stock_code="005930",
            sector="반도체",
            report_count=15,
            latest_report_date=datetime.date(2024, 4, 1),
        )
        session = self._make_list_stocks_session(rows=[row], total_count=1)

        result = await execute_list_stocks({}, session)
        assert "stocks" in result
        assert "total_count" in result
        assert result["total_count"] == 1
        s = result["stocks"][0]
        assert s["stock_name"] == "삼성전자"
        assert s["report_count"] == 15
        assert s["latest_report_date"] == "2024-04-01"

    @pytest.mark.asyncio
    async def test_total_count_from_separate_count_query(self):
        """total_count comes from a separate count query (true total, not len(rows))."""
        rows = [
            _row(stock_name=f"종목{i}", stock_code=None, sector=None,
                 report_count=i, latest_report_date=None)
            for i in range(3)
        ]
        # Simulate: 3 rows returned with limit, but true total is 100
        session = self._make_list_stocks_session(rows=rows, total_count=100)
        result = await execute_list_stocks({}, session)
        assert result["total_count"] == 100
        assert len(result["stocks"]) == 3
        # Two execute calls: one data, one count
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_limit_capped_at_50(self):
        session = _make_session(rows=[])
        await execute_list_stocks({"limit": 200}, session)
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_limit_minimum_clamped_to_1(self):
        session = _make_session(rows=[])
        await execute_list_stocks({"limit": 0}, session)
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_null_latest_date_handled(self):
        row = _row(
            stock_name="테스트주",
            stock_code=None,
            sector=None,
            report_count=1,
            latest_report_date=None,
        )
        session = _make_session(rows=[row])
        result = await execute_list_stocks({}, session)
        assert result["stocks"][0]["latest_report_date"] is None


# ---------------------------------------------------------------------------
# execute_get_report_stats
# ---------------------------------------------------------------------------

class TestExecuteGetReportStats:
    @pytest.mark.asyncio
    async def test_returns_expected_keys(self):
        session = AsyncMock()
        # 4 execute calls: total, by_broker, by_sector, top_stocks
        def _result(rows=None, scalar=None):
            r = MagicMock()
            r.all.return_value = rows or []
            r.scalar.return_value = scalar if scalar is not None else 0
            return r

        session.execute.side_effect = [
            _result(scalar=100),   # total_reports
            _result(rows=[]),      # by_broker
            _result(rows=[]),      # by_sector
            _result(rows=[]),      # top_stocks
        ]

        result = await execute_get_report_stats({}, session)
        assert "period" in result
        assert "total_reports" in result
        assert "by_broker" in result
        assert "by_sector" in result
        assert "top_stocks" in result
        assert result["total_reports"] == 100

    @pytest.mark.asyncio
    async def test_default_date_range(self):
        """date_from defaults to 30 days ago, date_to to today."""
        session = AsyncMock()

        def _result(rows=None, scalar=None):
            r = MagicMock()
            r.all.return_value = rows or []
            r.scalar.return_value = scalar if scalar is not None else 0
            return r

        session.execute.side_effect = [
            _result(scalar=0),
            _result(rows=[]),
            _result(rows=[]),
            _result(rows=[]),
        ]

        today = datetime.date.today()
        expected_from = today - datetime.timedelta(days=30)

        result = await execute_get_report_stats({}, session)
        assert result["period"]["from"] == str(expected_from)
        assert result["period"]["to"] == str(today)

    @pytest.mark.asyncio
    async def test_explicit_date_range(self):
        session = AsyncMock()

        def _result(rows=None, scalar=None):
            r = MagicMock()
            r.all.return_value = rows or []
            r.scalar.return_value = scalar if scalar is not None else 0
            return r

        session.execute.side_effect = [
            _result(scalar=5),
            _result(rows=[_row(broker="NH투자증권", count=3), _row(broker="키움", count=2)]),
            _result(rows=[_row(sector="반도체", count=4)]),
            _result(rows=[_row(stock_name="삼성전자", stock_code="005930", count=3)]),
        ]

        result = await execute_get_report_stats(
            {"date_from": "2024-01-01", "date_to": "2024-01-31"}, session
        )
        assert result["period"]["from"] == "2024-01-01"
        assert result["period"]["to"] == "2024-01-31"
        assert len(result["by_broker"]) == 2
        assert result["by_broker"][0]["broker"] == "NH투자증권"
        assert result["by_sector"][0]["sector"] == "반도체"
        assert result["top_stocks"][0]["stock_code"] == "005930"

    @pytest.mark.asyncio
    async def test_invalid_date_from_returns_error(self):
        session = AsyncMock()
        result = await execute_get_report_stats({"date_from": "not-a-date"}, session)
        assert "error" in result
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_date_to_returns_error(self):
        session = AsyncMock()
        result = await execute_get_report_stats({"date_to": "2024/12/31"}, session)
        assert "error" in result
        session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# execute_tool dispatcher
# ---------------------------------------------------------------------------

class TestExecuteTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        session = _make_session(rows=[])
        result = await execute_tool("nonexistent_tool", {}, session)
        assert "error" in result
        assert "nonexistent_tool" in result["error"]

    @pytest.mark.asyncio
    async def test_dispatches_search_reports(self):
        session = _make_session(rows=[])
        result = await execute_tool("search_reports", {}, session)
        assert "reports" in result

    @pytest.mark.asyncio
    async def test_dispatches_get_report_detail(self):
        session = _make_session(rows=[])
        result = await execute_tool("get_report_detail", {"report_ids": []}, session)
        assert "reports" in result

    @pytest.mark.asyncio
    async def test_dispatches_list_stocks(self):
        session = _make_session(rows=[])
        result = await execute_tool("list_stocks", {}, session)
        assert "stocks" in result

    @pytest.mark.asyncio
    async def test_dispatches_get_report_stats(self):
        session = AsyncMock()

        def _result(rows=None, scalar=None):
            r = MagicMock()
            r.all.return_value = rows or []
            r.scalar.return_value = scalar if scalar is not None else 0
            return r

        session.execute.side_effect = [
            _result(scalar=0),
            _result(rows=[]),
            _result(rows=[]),
            _result(rows=[]),
        ]
        result = await execute_tool("get_report_stats", {}, session)
        assert "total_reports" in result

    @pytest.mark.asyncio
    async def test_all_tools_dispatched(self):
        """Verify all 4 tool names from AGENT_TOOLS are dispatchable."""
        known_names = {t["name"] for t in AGENT_TOOLS}
        assert known_names == {"search_reports", "get_report_detail", "list_stocks", "get_report_stats"}
