"""
Tests for Task 1: upsert_report transaction atomicity fix.

Verifies that:
- upsert_report does NOT call session.commit() internally
- upsert_report calls session.flush() so report.id is available
- listener.py and backfill.py own the commit boundary
"""
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parsed(title_normalized="테스트리포트_abc"):
    parsed = MagicMock()
    parsed.title = "테스트 리포트"
    parsed.title_normalized = title_normalized
    parsed.broker = "미래에셋"
    parsed.source_channel = "@testchannel"
    parsed.report_date = None
    parsed.analyst = "홍길동"
    parsed.stock_name = "삼성전자"
    parsed.stock_code = "005930"
    parsed.sector = None
    parsed.report_type = None
    parsed.opinion = "매수"
    parsed.target_price = 85000
    parsed.prev_opinion = None
    parsed.prev_target_price = None
    parsed.earnings_quarter = None
    parsed.est_revenue = None
    parsed.est_op_profit = None
    parsed.est_eps = None
    parsed.earnings_surprise = None
    parsed.pdf_url = "https://example.com/report.pdf"
    parsed.source_message_id = 12345
    parsed.raw_text = "리포트 원문"
    parsed.parse_quality = "high"
    return parsed


def _make_mock_report(report_id=42):
    report = MagicMock()
    report.id = report_id
    report.pdf_path = None
    report.pdf_url = "https://example.com/report.pdf"
    return report


# ---------------------------------------------------------------------------
# upsert_report: no internal commit
# ---------------------------------------------------------------------------

class TestUpsertReportNoCommit:

    @pytest.mark.asyncio
    async def test_upsert_report_does_not_call_commit(self):
        """upsert_report must NOT call session.commit() — callers own the transaction."""
        from storage.report_repo import upsert_report

        mock_report = _make_mock_report()

        # Simulate RETURNING result (inserted)
        mock_pair = MagicMock()
        mock_pair.tuple.return_value = (mock_report, True)

        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_pair

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        parsed = _make_parsed()
        report, action = await upsert_report(session, parsed)

        session.commit.assert_not_called()
        assert action == "inserted"
        assert report is mock_report

    @pytest.mark.asyncio
    async def test_upsert_report_calls_flush(self):
        """upsert_report must call session.flush() so report.id is available to callers."""
        from storage.report_repo import upsert_report

        mock_report = _make_mock_report()

        mock_pair = MagicMock()
        mock_pair.tuple.return_value = (mock_report, False)

        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_pair

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        parsed = _make_parsed()
        await upsert_report(session, parsed)

        session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_report_skipped_case_no_commit(self):
        """DO NOTHING case (pair is None) also must not commit."""
        from storage.report_repo import upsert_report

        # Simulate DO NOTHING: RETURNING returns nothing
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = None

        existing_report = _make_mock_report(report_id=99)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        session.scalar = AsyncMock(return_value=existing_report)

        parsed = _make_parsed()
        report, action = await upsert_report(session, parsed)

        session.commit.assert_not_called()
        assert action == "skipped"
        assert report is existing_report

    @pytest.mark.asyncio
    async def test_upsert_report_missing_title_normalized_no_commit(self):
        """Early-return (missing title_normalized) path also must not commit."""
        from storage.report_repo import upsert_report

        session = AsyncMock()
        parsed = _make_parsed(title_normalized=None)

        report, action = await upsert_report(session, parsed)

        session.commit.assert_not_called()
        session.flush.assert_not_called()
        assert report is None
        assert action == "skipped"


# ---------------------------------------------------------------------------
# Source-code assertions: flush present, commit absent
# ---------------------------------------------------------------------------

class TestUpsertReportSourceCode:

    def test_upsert_report_has_flush_not_commit(self):
        """upsert_report source must use flush(), not commit()."""
        from storage import report_repo
        source = inspect.getsource(report_repo.upsert_report)
        assert "session.flush()" in source, "upsert_report must call session.flush()"
        assert "session.commit()" not in source, "upsert_report must NOT call session.commit()"


# ---------------------------------------------------------------------------
# listener.py: commit is owned by caller, not upsert_report
# ---------------------------------------------------------------------------

class TestListenerCommitBoundary:

    def test_listener_commits_after_upsert(self):
        """listener.handle_new_message must call session.commit() after upsert_report."""
        from collector import listener
        source = inspect.getsource(listener.handle_new_message)
        assert "session.commit()" in source, (
            "listener.handle_new_message must commit the session after upsert"
        )

    def test_listener_does_not_call_commit_inside_upsert_report(self):
        """The commit in listener must be at the caller level, not delegated to upsert_report."""
        from storage import report_repo
        upsert_source = inspect.getsource(report_repo.upsert_report)
        assert "session.commit()" not in upsert_source


# ---------------------------------------------------------------------------
# backfill.py: commit is owned by caller, not upsert_report
# ---------------------------------------------------------------------------

class TestBackfillCommitBoundary:

    def test_process_single_report_commits_after_upsert(self):
        """backfill._process_single_report must call session.commit() after upsert_report."""
        from collector import backfill
        source = inspect.getsource(backfill._process_single_report)
        assert "session.commit()" in source, (
            "backfill._process_single_report must commit the session"
        )

    @pytest.mark.asyncio
    async def test_backfill_commit_called_once_per_report(self):
        """In a single report pipeline, session.commit() is called once (at the end)."""
        from collector.backfill import _process_single_report, _ReportTask
        from parser.llm_parser import S2aResult
        from datetime import datetime, timezone

        mock_report = _make_mock_report(report_id=10)
        mock_report.tme_message_links = None
        mock_report.pdf_url = None
        mock_report.pdf_path = None

        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)  # no existing analysis

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield mock_session

        msg = MagicMock()
        msg.id = 1
        msg.date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        msg.media = None

        parsed = _make_parsed()
        parsed.tme_message_links = None
        parsed.report_date = None

        task = _ReportTask(
            parsed=parsed,
            message=msg,
            pdf_fname=None,
            channel_username="@testchannel",
            client=AsyncMock(),
        )

        with patch("collector.backfill.AsyncSessionLocal", mock_session_ctx), \
             patch("collector.backfill.classify_message", new_callable=AsyncMock,
                   return_value=S2aResult("broker_report")), \
             patch("collector.backfill.stock_mapper.get_code", new_callable=AsyncMock,
                   return_value="005930"), \
             patch("collector.backfill.assess_parse_quality", return_value="high"), \
             patch("collector.backfill.upsert_report", new_callable=AsyncMock,
                   return_value=(mock_report, "inserted")), \
             patch("collector.backfill.update_pipeline_status", new_callable=AsyncMock), \
             patch("collector.backfill.settings") as mock_settings:

            mock_settings.pdf_base_path = MagicMock()
            mock_settings.analysis_enabled = False
            mock_settings.anthropic_api_key = None

            result = await _process_single_report(task)

        # commit is called exactly once — at the end of the session block
        mock_session.commit.assert_called_once()
        assert result.action == "inserted"
