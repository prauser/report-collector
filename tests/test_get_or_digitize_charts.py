"""Unit tests for get_or_digitize_charts (cache-first chart digitization)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_digitize_result(success_count: int = 2):
    from parser.chart_digitizer import DigitizeResult
    return DigitizeResult(
        texts=[f"chart_{i}" for i in range(success_count)],
        total_input_tokens=100,
        total_output_tokens=50,
        total_cost_usd=Decimal("0.001"),
        image_count=3,
        success_count=success_count,
    )


def _make_images(n: int = 2):
    return [MagicMock() for _ in range(n)]


# ---------------------------------------------------------------------------
# Tests
# Because get_or_digitize_charts imports load_chart_text / save_chart_text
# lazily inside the function body, we patch them at the source module
# (storage.chart_text_repo) rather than on parser.chart_digitizer.
# ---------------------------------------------------------------------------

class TestGetOrDigitizeCharts:

    @pytest.mark.asyncio
    async def test_cache_hit_skips_digitize(self):
        """load_chart_text가 결과 반환 시 digitize_charts/save 호출 안 됨."""
        from parser.chart_digitizer import get_or_digitize_charts
        cached = _make_digitize_result(success_count=2)
        images = _make_images(2)

        with patch(
            "storage.chart_text_repo.load_chart_text", new_callable=AsyncMock, return_value=cached
        ) as mock_load, patch(
            "parser.chart_digitizer.digitize_charts", new_callable=AsyncMock
        ) as mock_dig, patch(
            "storage.chart_text_repo.save_chart_text", new_callable=AsyncMock
        ) as mock_save:
            result = await get_or_digitize_charts(images, report_id=42)

        mock_load.assert_awaited_once_with(42)
        mock_dig.assert_not_called()
        mock_save.assert_not_called()
        assert result is cached

    @pytest.mark.asyncio
    async def test_cache_miss_calls_digitize_and_saves(self):
        """load None → digitize 호출 → save 호출."""
        from parser.chart_digitizer import get_or_digitize_charts
        fresh = _make_digitize_result(success_count=3)
        images = _make_images(3)

        with patch(
            "storage.chart_text_repo.load_chart_text", new_callable=AsyncMock, return_value=None
        ) as mock_load, patch(
            "parser.chart_digitizer.digitize_charts", new_callable=AsyncMock, return_value=fresh
        ) as mock_dig, patch(
            "storage.chart_text_repo.save_chart_text", new_callable=AsyncMock
        ) as mock_save:
            result = await get_or_digitize_charts(images, report_id=99, channel="test_ch")

        mock_load.assert_awaited_once_with(99)
        mock_dig.assert_awaited_once_with(images, report_id=99, channel="test_ch")
        mock_save.assert_awaited_once_with(99, fresh)
        assert result is fresh

    @pytest.mark.asyncio
    async def test_empty_result_is_still_saved(self):
        """success_count=0이어도 save 호출 (안정적 캐시)."""
        from parser.chart_digitizer import get_or_digitize_charts
        empty = _make_digitize_result(success_count=0)
        images = _make_images(2)

        with patch(
            "storage.chart_text_repo.load_chart_text", new_callable=AsyncMock, return_value=None
        ), patch(
            "parser.chart_digitizer.digitize_charts", new_callable=AsyncMock, return_value=empty
        ), patch(
            "storage.chart_text_repo.save_chart_text", new_callable=AsyncMock
        ) as mock_save:
            result = await get_or_digitize_charts(images, report_id=7)

        mock_save.assert_awaited_once_with(7, empty)
        assert result.success_count == 0

    @pytest.mark.asyncio
    async def test_report_id_none_bypasses_cache(self):
        """report_id=None → load/save 호출 안 됨, digitize_charts 직접 호출."""
        from parser.chart_digitizer import get_or_digitize_charts
        fresh = _make_digitize_result(success_count=1)
        images = _make_images(1)

        with patch(
            "storage.chart_text_repo.load_chart_text", new_callable=AsyncMock
        ) as mock_load, patch(
            "parser.chart_digitizer.digitize_charts", new_callable=AsyncMock, return_value=fresh
        ) as mock_dig, patch(
            "storage.chart_text_repo.save_chart_text", new_callable=AsyncMock
        ) as mock_save:
            result = await get_or_digitize_charts(images, report_id=None, channel="ch")

        mock_load.assert_not_called()
        mock_save.assert_not_called()
        mock_dig.assert_awaited_once_with(images, report_id=None, channel="ch")
        assert result is fresh

    @pytest.mark.asyncio
    async def test_save_failure_does_not_break_flow(self):
        """save에서 예외 발생해도 fresh 결과 반환 (fail-silent)."""
        from parser.chart_digitizer import get_or_digitize_charts
        fresh = _make_digitize_result(success_count=2)
        images = _make_images(2)

        with patch(
            "storage.chart_text_repo.load_chart_text", new_callable=AsyncMock, return_value=None
        ), patch(
            "parser.chart_digitizer.digitize_charts", new_callable=AsyncMock, return_value=fresh
        ), patch(
            "storage.chart_text_repo.save_chart_text",
            new_callable=AsyncMock,
            side_effect=Exception("DB connection lost"),
        ):
            result = await get_or_digitize_charts(images, report_id=5)

        # save raised but result is still returned
        assert result is fresh
        assert result.success_count == 2
