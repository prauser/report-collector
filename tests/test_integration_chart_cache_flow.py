"""Integration tests for chart digitize DB cache end-to-end flow.

Tests the contract between:
  run_analysis (call site) -> get_or_digitize_charts (parser/chart_digitizer)
  -> load_chart_text / save_chart_text (storage/chart_text_repo)
  -> ReportChartText (db/models)

No live DB required: uses in-memory mocks for DB layer only.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(success_count: int = 2, texts: list[str] | None = None):
    from parser.chart_digitizer import DigitizeResult
    if texts is None:
        texts = [f"## Chart {i}\n| col | val |\n|---|---|\n| a | {i} |" for i in range(success_count)]
    return DigitizeResult(
        texts=texts,
        total_input_tokens=500,
        total_output_tokens=200,
        total_cost_usd=Decimal("0.0025"),
        image_count=success_count + 1,
        success_count=success_count,
    )


def _make_model_row(report_id: int, texts: list[str]):
    """Simulate a ReportChartText ORM row returned from DB."""
    from db.models import ReportChartText
    row = MagicMock(spec=ReportChartText)
    row.report_id = report_id
    row.chart_texts = texts
    row.image_count = len(texts) + 1
    row.success_count = len(texts)
    row.total_input_tokens = 400
    row.total_output_tokens = 150
    row.total_cost_usd = Decimal("0.002")
    return row


# ---------------------------------------------------------------------------
# Integration: run_analysis call site → get_or_digitize_charts pipeline
# ---------------------------------------------------------------------------

class TestRunAnalysisCallsGetOrDigitize:
    """Verify run_analysis uses get_or_digitize_charts at the call site."""

    def test_run_analysis_imports_get_or_digitize_charts(self):
        """run_analysis module must expose get_or_digitize_charts as the imported name."""
        import run_analysis
        assert hasattr(run_analysis, "get_or_digitize_charts"), (
            "run_analysis must import get_or_digitize_charts from parser.chart_digitizer"
        )

    def test_run_analysis_does_not_import_digitize_charts_directly(self):
        """run_analysis must NOT directly expose the old digitize_charts name."""
        import run_analysis
        assert not hasattr(run_analysis, "digitize_charts"), (
            "run_analysis should no longer import digitize_charts directly"
        )


# ---------------------------------------------------------------------------
# Integration: cache-hit flow (load → return, skip Gemini, skip save)
# ---------------------------------------------------------------------------

class TestCacheHitFlow:
    """DB hit → Gemini call skipped, result passed back correctly."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_stored_result_with_correct_fields(self):
        """On DB cache hit, returned DigitizeResult has fields from the stored row."""
        from parser.chart_digitizer import get_or_digitize_charts

        stored_texts = ["## Revenue\n| Year | Revenue |\n|---|---|\n| 2024 | 1234 |"]
        row = _make_model_row(report_id=101, texts=stored_texts)

        # Patch AsyncSessionLocal so load_chart_text finds our row
        mock_session = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.scalar_one_or_none.return_value = row
        mock_session.execute = AsyncMock(return_value=mock_execute_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls = MagicMock(return_value=mock_session)

        with patch("storage.chart_text_repo.AsyncSessionLocal", mock_session_cls), \
             patch("parser.chart_digitizer.digitize_charts", new_callable=AsyncMock) as mock_gemini:

            result = await get_or_digitize_charts(
                images=[MagicMock()],
                report_id=101,
                channel="test_channel",
            )

        # Gemini must NOT have been called
        mock_gemini.assert_not_called()

        # Result should reflect the DB row values
        assert result.texts == stored_texts
        assert result.success_count == row.success_count
        assert result.image_count == row.image_count
        assert result.total_input_tokens == row.total_input_tokens
        assert result.total_output_tokens == row.total_output_tokens
        assert result.total_cost_usd == Decimal(str(row.total_cost_usd))


# ---------------------------------------------------------------------------
# Integration: cache-miss flow (load None → digitize → save)
# ---------------------------------------------------------------------------

class TestCacheMissFlow:
    """DB miss → Gemini called, result saved to DB, same result returned."""

    @pytest.mark.asyncio
    async def test_cache_miss_calls_gemini_then_saves(self):
        """On DB cache miss, digitize_charts runs and result is persisted."""
        from parser.chart_digitizer import get_or_digitize_charts

        fresh = _make_result(success_count=3)

        # Session for load returns no row (cache miss)
        mock_load_session = AsyncMock()
        mock_load_exec = MagicMock()
        mock_load_exec.scalar_one_or_none.return_value = None
        mock_load_session.execute = AsyncMock(return_value=mock_load_exec)
        mock_load_session.__aenter__ = AsyncMock(return_value=mock_load_session)
        mock_load_session.__aexit__ = AsyncMock(return_value=False)

        # Session for save (execute + commit)
        mock_save_session = AsyncMock()
        mock_save_session.__aenter__ = AsyncMock(return_value=mock_save_session)
        mock_save_session.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        def session_factory():
            nonlocal call_count
            call_count += 1
            return mock_load_session if call_count == 1 else mock_save_session

        with patch("storage.chart_text_repo.AsyncSessionLocal", side_effect=session_factory), \
             patch("parser.chart_digitizer.digitize_charts",
                   new_callable=AsyncMock, return_value=fresh) as mock_gemini:

            result = await get_or_digitize_charts(
                images=[MagicMock(), MagicMock(), MagicMock()],
                report_id=202,
                channel="@sunstudy1004",
            )

        # Gemini must have been called exactly once
        mock_gemini.assert_awaited_once()
        args, kwargs = mock_gemini.call_args
        assert kwargs.get("report_id") == 202
        assert kwargs.get("channel") == "@sunstudy1004"

        # Save must have been called (session execute + commit)
        mock_save_session.execute.assert_awaited_once()
        mock_save_session.commit.assert_awaited_once()

        # Result is the fresh digitize result
        assert result is fresh
        assert result.success_count == 3

    @pytest.mark.asyncio
    async def test_cache_miss_save_failure_still_returns_fresh_result(self):
        """Save error must not propagate — fresh result returned regardless."""
        from parser.chart_digitizer import get_or_digitize_charts

        fresh = _make_result(success_count=1)

        # load returns None (cache miss)
        mock_load_session = AsyncMock()
        mock_load_exec = MagicMock()
        mock_load_exec.scalar_one_or_none.return_value = None
        mock_load_session.execute = AsyncMock(return_value=mock_load_exec)
        mock_load_session.__aenter__ = AsyncMock(return_value=mock_load_session)
        mock_load_session.__aexit__ = AsyncMock(return_value=False)

        # save session raises on execute
        mock_save_session = AsyncMock()
        mock_save_session.execute = AsyncMock(side_effect=Exception("DB write failed"))
        mock_save_session.__aenter__ = AsyncMock(return_value=mock_save_session)
        mock_save_session.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        def session_factory():
            nonlocal call_count
            call_count += 1
            return mock_load_session if call_count == 1 else mock_save_session

        with patch("storage.chart_text_repo.AsyncSessionLocal", side_effect=session_factory), \
             patch("parser.chart_digitizer.digitize_charts",
                   new_callable=AsyncMock, return_value=fresh):

            result = await get_or_digitize_charts(images=[MagicMock()], report_id=303)

        # Must still return the fresh result despite save failure
        assert result is fresh


# ---------------------------------------------------------------------------
# Integration: report_id=None bypass
# ---------------------------------------------------------------------------

class TestNullReportIdBypass:
    """report_id=None must bypass load/save entirely."""

    @pytest.mark.asyncio
    async def test_none_report_id_bypasses_db_entirely(self):
        """No DB session opened when report_id is None."""
        from parser.chart_digitizer import get_or_digitize_charts

        fresh = _make_result(success_count=1)
        mock_session_cls = MagicMock()

        with patch("storage.chart_text_repo.AsyncSessionLocal", mock_session_cls), \
             patch("parser.chart_digitizer.digitize_charts",
                   new_callable=AsyncMock, return_value=fresh) as mock_gemini:

            result = await get_or_digitize_charts(
                images=[MagicMock()],
                report_id=None,
                channel="chan",
            )

        # No DB session should have been created
        mock_session_cls.assert_not_called()
        # Gemini called directly
        mock_gemini.assert_awaited_once()
        assert result is fresh


# ---------------------------------------------------------------------------
# Integration: ReportChartText model field coverage
# ---------------------------------------------------------------------------

class TestReportChartTextModel:
    """Verify ReportChartText model has all fields expected by the repo layer."""

    def test_model_has_required_columns(self):
        from db.models import ReportChartText
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(ReportChartText)
        col_names = {c.key for c in mapper.mapper.column_attrs}
        required = {
            "id", "report_id", "chart_texts", "image_count", "success_count",
            "model", "total_input_tokens", "total_output_tokens",
            "total_cost_usd", "created_at", "updated_at",
        }
        missing = required - col_names
        assert not missing, f"ReportChartText missing columns: {missing}"

    def test_model_tablename(self):
        from db.models import ReportChartText
        assert ReportChartText.__tablename__ == "report_chart_text"


# ---------------------------------------------------------------------------
# Integration: load_chart_text reconstructs DigitizeResult correctly
# ---------------------------------------------------------------------------

class TestLoadChartTextReconstruction:
    """Verify load_chart_text returns a properly typed DigitizeResult."""

    @pytest.mark.asyncio
    async def test_load_reconstructs_decimal_cost(self):
        """total_cost_usd must be returned as Decimal, not float."""
        from storage.chart_text_repo import load_chart_text

        row = _make_model_row(report_id=555, texts=["table text"])
        row.total_cost_usd = "0.0031"   # simulate DB string/Decimal

        mock_session = AsyncMock()
        mock_exec = MagicMock()
        mock_exec.scalar_one_or_none.return_value = row
        mock_session.execute = AsyncMock(return_value=mock_exec)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("storage.chart_text_repo.AsyncSessionLocal", return_value=mock_session):
            result = await load_chart_text(555)

        assert isinstance(result.total_cost_usd, Decimal)
        assert result.total_cost_usd == Decimal("0.0031")

    @pytest.mark.asyncio
    async def test_load_handles_none_tokens_as_zero(self):
        """None token fields in DB row → 0 in DigitizeResult."""
        from storage.chart_text_repo import load_chart_text

        row = _make_model_row(report_id=556, texts=[])
        row.total_input_tokens = None
        row.total_output_tokens = None
        row.total_cost_usd = None

        mock_session = AsyncMock()
        mock_exec = MagicMock()
        mock_exec.scalar_one_or_none.return_value = row
        mock_session.execute = AsyncMock(return_value=mock_exec)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("storage.chart_text_repo.AsyncSessionLocal", return_value=mock_session):
            result = await load_chart_text(556)

        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0
        assert result.total_cost_usd == Decimal("0")
