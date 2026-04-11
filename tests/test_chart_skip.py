"""Tests for chart_digitize conditional execution in process_single().

Verifies:
1. chart_mode="auto" skips charts for non-퀀트 report types
2. chart_mode="auto" runs charts for 퀀트 reports
3. chart_mode="enabled" runs charts for all report types
4. chart_mode="disabled" skips charts for all types including 퀀트
5. key_data extraction failure (None) defaults to skipping charts
6. charts_skipped log is emitted with correct fields when skipping
7. --enable-charts and --disable-charts CLI flags produce correct chart_mode
8. chart_mode is passed to process_single from main() via workers
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_key_data(report_type: str | None):
    """Return a mock KeyDataResult with the given report_type."""
    kd = MagicMock()
    kd.report_type = report_type
    kd.broker = "TestBroker"
    kd.analyst = "홍길동"
    kd.date = "2026-01-01"
    kd.stock_name = None
    kd.stock_code = None
    kd.opinion = None
    kd.target_price = None
    kd.title = "Test Title"
    return kd


def _make_report_model(report_id: int = 1):
    """Return a mock ReportModel."""
    r = MagicMock()
    r.id = report_id
    r.pdf_path = f"test/{report_id}.pdf"
    r.source_channel = "test_channel"
    r.raw_text = "raw text"
    r.title = f"Report {report_id}"
    return r


def _make_dig_result(n_images: int = 2, success_count: int = 2):
    """Return a mock digitize_charts result."""
    dig = MagicMock()
    dig.texts = [f"chart_{i}" for i in range(success_count)]
    dig.success_count = success_count
    return dig


def _make_args(
    enable_charts: bool = False,
    disable_charts: bool = False,
    concurrency: int = 1,
    limit: int = 0,
    dry_run: bool = False,
    batch_size: int = 100,
) -> argparse.Namespace:
    return argparse.Namespace(
        enable_charts=enable_charts,
        disable_charts=disable_charts,
        concurrency=concurrency,
        limit=limit,
        dry_run=dry_run,
        batch_size=batch_size,
    )


def _mock_session():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    return sess


# ---------------------------------------------------------------------------
# process_single unit tests — chart_mode="auto"
# ---------------------------------------------------------------------------

class TestAutoModeNonQuant:
    """Auto mode: non-퀀트 reports skip chart digitization."""

    @pytest.mark.parametrize("report_type", [
        "기업분석", "산업분석", "매크로", "실적리뷰", "주간전략", "기타",
    ])
    @pytest.mark.asyncio
    async def test_non_quant_skips_digitize(self, report_type):
        """digitize_charts must NOT be called for non-퀀트 in auto mode."""
        report = _make_report_model()
        fake_images = [MagicMock(), MagicMock()]

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=_make_key_data(report_type)), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown content " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=fake_images), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock) as mock_dig, \
             patch("run_analysis.build_user_content", return_value=([], False, 500)):
            ms.pdf_base_path = Path("/fake")
            # Make pdf path exist
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                result = await process_single(report, chart_mode="auto")

        mock_dig.assert_not_called()
        assert result["steps"].get("charts") == "skipped"

    @pytest.mark.asyncio
    async def test_non_quant_sets_chart_texts_none(self):
        """chart_texts is None when charts are skipped (non-퀀트)."""
        report = _make_report_model()
        captured_chart_texts = []

        def fake_build_user_content(text, markdown, chart_texts, channel):
            captured_chart_texts.append(chart_texts)
            return ([], False, 500)

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=_make_key_data("기업분석")), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=[MagicMock()]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock), \
             patch("run_analysis.build_user_content",
                   side_effect=fake_build_user_content):
            ms.pdf_base_path = Path("/fake")
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                await process_single(report, chart_mode="auto")

        assert len(captured_chart_texts) == 1
        assert captured_chart_texts[0] is None

    @pytest.mark.asyncio
    async def test_non_quant_logs_charts_skipped(self):
        """charts_skipped must be logged with correct report_type and reason."""
        report = _make_report_model()
        log_calls = []

        def capture_info(event, **kwargs):
            log_calls.append((event, kwargs))

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=_make_key_data("산업분석")), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=[MagicMock()]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock), \
             patch("run_analysis.build_user_content", return_value=([], False, 500)), \
             patch("run_analysis.log") as mock_log:
            ms.pdf_base_path = Path("/fake")
            mock_log.info.side_effect = capture_info
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                await process_single(report, chart_mode="auto")

        skip_calls = [(e, kw) for e, kw in log_calls if e == "charts_skipped"]
        assert len(skip_calls) == 1
        _, kw = skip_calls[0]
        assert kw["report_type"] == "산업분석"
        assert kw["reason"] == "non_quant"
        assert kw["report_id"] == report.id


class TestAutoModeQuant:
    """Auto mode: 퀀트 reports run chart digitization."""

    @pytest.mark.asyncio
    async def test_quant_runs_digitize(self):
        """digitize_charts IS called for 퀀트 in auto mode."""
        report = _make_report_model()
        fake_images = [MagicMock(), MagicMock()]
        dig_result = _make_dig_result()

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=_make_key_data("퀀트")), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=fake_images), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock,
                   return_value=dig_result) as mock_dig, \
             patch("run_analysis.build_user_content", return_value=([], False, 500)):
            ms.pdf_base_path = Path("/fake")
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                result = await process_single(report, chart_mode="auto")

        mock_dig.assert_called_once()
        assert "digitized" in result["steps"].get("charts", "")

    @pytest.mark.asyncio
    async def test_quant_no_charts_skipped_log(self):
        """charts_skipped must NOT be logged for 퀀트 in auto mode."""
        report = _make_report_model()
        dig_result = _make_dig_result()
        log_calls = []

        def capture_info(event, **kwargs):
            log_calls.append((event, kwargs))

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=_make_key_data("퀀트")), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=[MagicMock()]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock,
                   return_value=dig_result), \
             patch("run_analysis.build_user_content", return_value=([], False, 500)), \
             patch("run_analysis.log") as mock_log:
            ms.pdf_base_path = Path("/fake")
            mock_log.info.side_effect = capture_info
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                await process_single(report, chart_mode="auto")

        skip_calls = [(e, kw) for e, kw in log_calls if e == "charts_skipped"]
        assert len(skip_calls) == 0


# ---------------------------------------------------------------------------
# process_single unit tests — chart_mode="enabled"
# ---------------------------------------------------------------------------

class TestEnabledMode:
    """--enable-charts: all report types run chart digitization."""

    @pytest.mark.parametrize("report_type", [
        "기업분석", "산업분석", "매크로", "실적리뷰", "주간전략", "기타", "퀀트",
    ])
    @pytest.mark.asyncio
    async def test_all_types_run_digitize(self, report_type):
        """digitize_charts IS called for any report_type in enabled mode."""
        report = _make_report_model()
        dig_result = _make_dig_result()

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=_make_key_data(report_type)), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=[MagicMock()]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock,
                   return_value=dig_result) as mock_dig, \
             patch("run_analysis.build_user_content", return_value=([], False, 500)):
            ms.pdf_base_path = Path("/fake")
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                result = await process_single(report, chart_mode="enabled")

        mock_dig.assert_called_once()
        assert "digitized" in result["steps"].get("charts", "")


# ---------------------------------------------------------------------------
# process_single unit tests — chart_mode="disabled"
# ---------------------------------------------------------------------------

class TestDisabledMode:
    """--disable-charts: all report types skip chart digitization."""

    @pytest.mark.parametrize("report_type", [
        "기업분석", "산업분석", "매크로", "실적리뷰", "주간전략", "기타", "퀀트",
    ])
    @pytest.mark.asyncio
    async def test_all_types_skip_digitize(self, report_type):
        """digitize_charts must NOT be called for any report_type in disabled mode."""
        report = _make_report_model()

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=_make_key_data(report_type)), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=[MagicMock()]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock) as mock_dig, \
             patch("run_analysis.build_user_content", return_value=([], False, 500)):
            ms.pdf_base_path = Path("/fake")
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                result = await process_single(report, chart_mode="disabled")

        mock_dig.assert_not_called()
        assert result["steps"].get("charts") == "skipped"

    @pytest.mark.asyncio
    async def test_quant_also_skips_in_disabled_mode(self):
        """Even 퀀트 is skipped when chart_mode='disabled'."""
        report = _make_report_model()

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=_make_key_data("퀀트")), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=[MagicMock()]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock) as mock_dig, \
             patch("run_analysis.build_user_content", return_value=([], False, 500)):
            ms.pdf_base_path = Path("/fake")
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                result = await process_single(report, chart_mode="disabled")

        mock_dig.assert_not_called()


# ---------------------------------------------------------------------------
# process_single: key_data is None → skip charts
# ---------------------------------------------------------------------------

class TestKeyDataNoneSkipsCharts:
    """When key_data extraction returns None, charts are skipped in auto mode."""

    @pytest.mark.asyncio
    async def test_key_data_none_skips_digitize(self):
        """key_data=None → digitize_charts not called in auto mode."""
        report = _make_report_model()

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=None), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=[MagicMock()]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock) as mock_dig, \
             patch("run_analysis.build_user_content", return_value=([], False, 500)):
            ms.pdf_base_path = Path("/fake")
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                result = await process_single(report, chart_mode="auto")

        mock_dig.assert_not_called()
        assert result["steps"].get("charts") == "skipped"

    @pytest.mark.asyncio
    async def test_key_data_none_logs_charts_skipped(self):
        """key_data=None → charts_skipped logged with report_type=None."""
        report = _make_report_model()
        log_calls = []

        def capture_info(event, **kwargs):
            log_calls.append((event, kwargs))

        sess = _mock_session()
        with patch("run_analysis.settings") as ms, \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock,
                   return_value=None), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# Markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock,
                   return_value=[MagicMock()]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock), \
             patch("run_analysis.build_user_content", return_value=([], False, 500)), \
             patch("run_analysis.log") as mock_log:
            ms.pdf_base_path = Path("/fake")
            mock_log.info.side_effect = capture_info
            with patch.object(Path, "exists", return_value=True):
                from run_analysis import process_single
                await process_single(report, chart_mode="auto")

        skip_calls = [(e, kw) for e, kw in log_calls if e == "charts_skipped"]
        assert len(skip_calls) == 1
        _, kw = skip_calls[0]
        assert kw["report_type"] is None
        assert kw["reason"] == "non_quant"


# ---------------------------------------------------------------------------
# CLI flag tests
# ---------------------------------------------------------------------------

class TestCliFlags:
    """--enable-charts and --disable-charts produce correct argparse values."""

    def test_default_no_flags(self):
        """No flags: enable_charts=False, disable_charts=False."""
        import sys
        with patch.object(sys, "argv", ["run_analysis.py"]):
            from run_analysis import cli
            args = cli()
        assert args.enable_charts is False
        assert args.disable_charts is False

    def test_enable_charts_flag(self):
        """--enable-charts sets enable_charts=True."""
        import sys
        with patch.object(sys, "argv", ["run_analysis.py", "--enable-charts"]):
            from run_analysis import cli
            args = cli()
        assert args.enable_charts is True
        assert args.disable_charts is False

    def test_disable_charts_flag(self):
        """--disable-charts sets disable_charts=True."""
        import sys
        with patch.object(sys, "argv", ["run_analysis.py", "--disable-charts"]):
            from run_analysis import cli
            args = cli()
        assert args.disable_charts is True
        assert args.enable_charts is False

    def test_mutually_exclusive(self):
        """--enable-charts and --disable-charts are mutually exclusive."""
        import sys
        with patch.object(sys, "argv", ["run_analysis.py", "--enable-charts", "--disable-charts"]):
            with pytest.raises(SystemExit):
                from run_analysis import cli
                cli()


# ---------------------------------------------------------------------------
# main() passes chart_mode to process_single
# ---------------------------------------------------------------------------

class TestMainPassesChartMode:
    """main() resolves chart_mode from CLI args and passes it to process_single."""

    def _make_report(self, report_id: int = 1):
        r = MagicMock()
        r.id = report_id
        r.report_date = "2026-01-01"
        r.broker = "TestBroker"
        r.stock_name = "TestStock"
        r.sector = None
        r.title = f"Report {report_id}"
        r.source_channel = "test_channel"
        r.raw_text = "raw"
        return r

    @pytest.mark.asyncio
    async def test_default_args_passes_auto(self):
        """Default (no flags) → chart_mode='auto' is passed to process_single."""
        reports = [self._make_report(1)]
        captured_modes = []

        async def fake_process(report, chart_mode="auto"):
            captured_modes.append(chart_mode)
            return {"report_id": report.id, "status": "ok", "steps": {}}

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None
            ms.gemini_api_key = "fake"

            args = _make_args()  # no enable_charts, no disable_charts
            from run_analysis import main
            await main(args)

        assert captured_modes == ["auto"]

    @pytest.mark.asyncio
    async def test_enable_charts_arg_passes_enabled(self):
        """enable_charts=True → chart_mode='enabled' is passed to process_single."""
        reports = [self._make_report(1)]
        captured_modes = []

        async def fake_process(report, chart_mode="auto"):
            captured_modes.append(chart_mode)
            return {"report_id": report.id, "status": "ok", "steps": {}}

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None
            ms.gemini_api_key = "fake"

            args = _make_args(enable_charts=True)
            from run_analysis import main
            await main(args)

        assert captured_modes == ["enabled"]

    @pytest.mark.asyncio
    async def test_disable_charts_arg_passes_disabled(self):
        """disable_charts=True → chart_mode='disabled' is passed to process_single."""
        reports = [self._make_report(1)]
        captured_modes = []

        async def fake_process(report, chart_mode="auto"):
            captured_modes.append(chart_mode)
            return {"report_id": report.id, "status": "ok", "steps": {}}

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None
            ms.gemini_api_key = "fake"

            args = _make_args(disable_charts=True)
            from run_analysis import main
            await main(args)

        assert captured_modes == ["disabled"]
