"""Unit tests for storage.chart_text_repo.

Uses mock AsyncSession — no live DB required.
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_digitize_result(texts=None, image_count=3, success_count=2,
                           total_input_tokens=100, total_output_tokens=50,
                           total_cost_usd=None):
    from parser.chart_digitizer import DigitizeResult
    return DigitizeResult(
        texts=texts or ["## 표1\n| a | b |\n|---|---|\n| 1 | 2 |"],
        image_count=image_count,
        success_count=success_count,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cost_usd=total_cost_usd or Decimal("0.001234"),
    )


def _make_chart_text_row(report_id=42, texts=None, image_count=3, success_count=2,
                          total_input_tokens=100, total_output_tokens=50,
                          total_cost_usd=Decimal("0.001234")):
    """Fake ORM row mimicking ReportChartText."""
    row = MagicMock()
    row.report_id = report_id
    row.chart_texts = texts or ["## 표1\n| a | b |\n|---|---|\n| 1 | 2 |"]
    row.image_count = image_count
    row.success_count = success_count
    row.total_input_tokens = total_input_tokens
    row.total_output_tokens = total_output_tokens
    row.total_cost_usd = total_cost_usd
    return row


def _make_async_session_context(session: AsyncMock):
    """Return a context manager that yields `session`."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        yield session

    return _ctx


# ---------------------------------------------------------------------------
# test_save_uses_upsert
# ---------------------------------------------------------------------------

class TestSaveUsesUpsert:

    def test_save_uses_upsert_source(self):
        """save_chart_text source must contain on_conflict_do_update."""
        from storage import chart_text_repo
        source = inspect.getsource(chart_text_repo.save_chart_text)
        assert "on_conflict_do_update" in source, (
            "save_chart_text must use on_conflict_do_update for upsert"
        )

    @pytest.mark.asyncio
    async def test_save_executes_and_commits(self):
        """save_chart_text must call session.execute() and session.commit()."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()

        result = _make_digitize_result()

        with patch("storage.chart_text_repo.AsyncSessionLocal",
                   _make_async_session_context(session)), \
             patch("storage.chart_text_repo.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-2.0-flash-lite"
            await __import__("storage.chart_text_repo",
                              fromlist=["save_chart_text"]).save_chart_text(42, result)

        session.execute.assert_called_once()
        session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# test_load_returns_none_on_miss
# ---------------------------------------------------------------------------

class TestLoadReturnsNoneOnMiss:

    @pytest.mark.asyncio
    async def test_load_returns_none_when_no_row(self):
        """load_chart_text returns None when no matching row exists."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        with patch("storage.chart_text_repo.AsyncSessionLocal",
                   _make_async_session_context(session)):
            from storage.chart_text_repo import load_chart_text
            result = await load_chart_text(99)

        assert result is None


# ---------------------------------------------------------------------------
# test_load_returns_digitize_result_on_hit
# ---------------------------------------------------------------------------

class TestLoadReturnsDigitizeResultOnHit:

    @pytest.mark.asyncio
    async def test_load_returns_digitize_result(self):
        """load_chart_text returns a DigitizeResult populated from DB row."""
        from parser.chart_digitizer import DigitizeResult

        row = _make_chart_text_row(
            report_id=42,
            texts=["text1", "text2"],
            image_count=5,
            success_count=3,
            total_input_tokens=200,
            total_output_tokens=80,
            total_cost_usd=Decimal("0.005"),
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        with patch("storage.chart_text_repo.AsyncSessionLocal",
                   _make_async_session_context(session)):
            from storage.chart_text_repo import load_chart_text
            result = await load_chart_text(42)

        assert isinstance(result, DigitizeResult)
        assert result.texts == ["text1", "text2"]
        assert result.image_count == 5
        assert result.success_count == 3
        assert result.total_input_tokens == 200
        assert result.total_output_tokens == 80
        assert result.total_cost_usd == Decimal("0.005")

    @pytest.mark.asyncio
    async def test_load_row_with_none_fields(self):
        """load_chart_text handles None token/cost fields gracefully."""
        from parser.chart_digitizer import DigitizeResult

        row = _make_chart_text_row(
            texts=[],
            image_count=2,
            success_count=0,
            total_input_tokens=None,
            total_output_tokens=None,
            total_cost_usd=None,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        with patch("storage.chart_text_repo.AsyncSessionLocal",
                   _make_async_session_context(session)):
            from storage.chart_text_repo import load_chart_text
            result = await load_chart_text(7)

        assert isinstance(result, DigitizeResult)
        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0
        assert result.total_cost_usd == Decimal("0")


# ---------------------------------------------------------------------------
# test_save_failure_is_silent
# ---------------------------------------------------------------------------

class TestSaveFailureIsSilent:

    @pytest.mark.asyncio
    async def test_save_does_not_raise_on_db_error(self):
        """save_chart_text must NOT propagate exceptions — fail-silent."""
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=Exception("DB connection lost"))
        session.commit = AsyncMock()

        result = _make_digitize_result()

        # Should not raise
        with patch("storage.chart_text_repo.AsyncSessionLocal",
                   _make_async_session_context(session)), \
             patch("storage.chart_text_repo.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-2.0-flash-lite"
            from storage.chart_text_repo import save_chart_text
            await save_chart_text(42, result)  # must not raise

    @pytest.mark.asyncio
    async def test_load_does_not_raise_on_db_error(self):
        """load_chart_text must NOT propagate exceptions — returns None."""
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("timeout"))

        with patch("storage.chart_text_repo.AsyncSessionLocal",
                   _make_async_session_context(session)):
            from storage.chart_text_repo import load_chart_text
            result = await load_chart_text(42)

        assert result is None
