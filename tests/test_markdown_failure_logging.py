"""Tests for markdown failure logging and status transition in run_analysis.py.

Verifies:
1. no_markdown results in pipeline_status=analysis_failed
2. low_quality_markdown results in pipeline_status=analysis_failed
3. Failure details written to logs/markdown_failures.csv (timestamp, report_id, reason, pdf_path)
4. CSV written in append mode (multiple failures accumulate)
5. Header written only on first write (new file)
6. Normal (ok) reports do NOT trigger status transition or file write
"""
from __future__ import annotations

import asyncio
import csv
import io
import pathlib
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_session():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    return sess


def _make_report(report_id: int, pdf_path: str = "pdfs/test.pdf") -> MagicMock:
    r = MagicMock()
    r.id = report_id
    r.pdf_path = pdf_path
    r.source_channel = "test_channel"
    r.raw_text = "some raw text"
    r.title = f"Report {report_id}"
    return r


# ---------------------------------------------------------------------------
# Unit tests for _log_markdown_failure
# ---------------------------------------------------------------------------

class TestLogMarkdownFailure:
    """Direct tests for the _log_markdown_failure helper."""

    def test_creates_logs_dir_if_missing(self, tmp_path):
        logs_dir = tmp_path / "logs"
        assert not logs_dir.exists()

        from run_analysis import _log_markdown_failure, _MARKDOWN_FAILURE_LOG
        with patch("run_analysis._MARKDOWN_FAILURE_LOG", str(tmp_path / "logs" / "markdown_failures.csv")):
            _log_markdown_failure(1, "no_markdown", "/some/path.pdf")

        assert logs_dir.exists()

    def test_writes_header_on_new_file(self, tmp_path):
        csv_path = tmp_path / "markdown_failures.csv"

        from run_analysis import _log_markdown_failure
        with patch("run_analysis._MARKDOWN_FAILURE_LOG", str(csv_path)):
            _log_markdown_failure(42, "no_markdown", "/pdfs/foo.pdf")

        rows = list(csv.reader(csv_path.open(encoding="utf-8")))
        assert rows[0] == ["timestamp", "report_id", "reason", "pdf_path"]

    def test_writes_data_row(self, tmp_path):
        csv_path = tmp_path / "markdown_failures.csv"

        from run_analysis import _log_markdown_failure
        with patch("run_analysis._MARKDOWN_FAILURE_LOG", str(csv_path)):
            _log_markdown_failure(99, "low_quality_markdown", "/pdfs/bar.pdf")

        rows = list(csv.reader(csv_path.open(encoding="utf-8")))
        assert len(rows) == 2  # header + 1 data row
        _ts, rid, reason, pdf_path = rows[1]
        assert rid == "99"
        assert reason == "low_quality_markdown"
        assert pdf_path == "/pdfs/bar.pdf"

    def test_appends_on_subsequent_calls(self, tmp_path):
        csv_path = tmp_path / "markdown_failures.csv"

        from run_analysis import _log_markdown_failure
        with patch("run_analysis._MARKDOWN_FAILURE_LOG", str(csv_path)):
            _log_markdown_failure(1, "no_markdown", "/pdfs/a.pdf")
            _log_markdown_failure(2, "low_quality_markdown", "/pdfs/b.pdf")
            _log_markdown_failure(3, "no_markdown", "/pdfs/c.pdf")

        rows = list(csv.reader(csv_path.open(encoding="utf-8")))
        # header + 3 data rows
        assert len(rows) == 4
        assert rows[1][1] == "1"
        assert rows[2][1] == "2"
        assert rows[3][1] == "3"

    def test_no_duplicate_header_on_append(self, tmp_path):
        csv_path = tmp_path / "markdown_failures.csv"

        from run_analysis import _log_markdown_failure
        with patch("run_analysis._MARKDOWN_FAILURE_LOG", str(csv_path)):
            _log_markdown_failure(1, "no_markdown", "/pdfs/a.pdf")
            _log_markdown_failure(2, "no_markdown", "/pdfs/b.pdf")

        rows = list(csv.reader(csv_path.open(encoding="utf-8")))
        # Only one header row
        header_rows = [r for r in rows if r == ["timestamp", "report_id", "reason", "pdf_path"]]
        assert len(header_rows) == 1

    def test_timestamp_format_is_iso(self, tmp_path):
        csv_path = tmp_path / "markdown_failures.csv"

        from run_analysis import _log_markdown_failure
        import datetime
        with patch("run_analysis._MARKDOWN_FAILURE_LOG", str(csv_path)):
            _log_markdown_failure(1, "no_markdown", "/pdfs/x.pdf")

        rows = list(csv.reader(csv_path.open(encoding="utf-8")))
        ts = rows[1][0]
        # Should parse as ISO datetime without error
        datetime.datetime.fromisoformat(ts)


# ---------------------------------------------------------------------------
# Integration tests: process_single calls update_pipeline_status + logging
# ---------------------------------------------------------------------------

class TestProcessSingleMarkdownFailure:
    """Verify process_single transitions status and logs for markdown failures."""

    @pytest.mark.asyncio
    async def test_no_markdown_sets_analysis_failed(self, tmp_path):
        """When markdown_text is None, pipeline_status must become analysis_failed."""
        report = _make_report(10)
        sess = _mock_session()

        mock_pdf_path = MagicMock()
        mock_pdf_path.exists.return_value = True
        mock_pdf_path.__str__ = lambda self: "/fake/10.pdf"
        mock_pdf_path.__truediv__ = lambda self, other: mock_pdf_path

        mock_settings = MagicMock()
        mock_settings.pdf_base_path = mock_pdf_path

        update_status_mock = AsyncMock()

        with patch("run_analysis.settings", mock_settings), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", update_status_mock), \
             patch("run_analysis.extract_key_data", AsyncMock(return_value=None)), \
             patch("run_analysis.convert_pdf_to_markdown", AsyncMock(return_value=(None, "pymupdf"))), \
             patch("run_analysis.extract_images_from_pdf", AsyncMock(return_value=[])), \
             patch("run_analysis.digitize_charts", AsyncMock()), \
             patch("run_analysis._log_markdown_failure") as mock_log_failure:

            from run_analysis import process_single
            result = await process_single(report)

        assert result["status"] == "no_markdown"
        # update_pipeline_status should be called with "analysis_failed"
        failed_calls = [
            c for c in update_status_mock.call_args_list
            if c.args[2] == "analysis_failed" or (len(c.args) > 1 and "analysis_failed" in str(c))
        ]
        # Check that analysis_failed was passed in at least one call
        all_status_args = [str(c) for c in update_status_mock.call_args_list]
        assert any("analysis_failed" in s for s in all_status_args), (
            f"Expected analysis_failed in status calls, got: {all_status_args}"
        )
        mock_log_failure.assert_called_once()
        call_args = mock_log_failure.call_args
        assert call_args.args[0] == 10
        assert call_args.args[1] == "no_markdown"

    @pytest.mark.asyncio
    async def test_low_quality_markdown_sets_analysis_failed(self, tmp_path):
        """When markdown < 200 chars, pipeline_status must become analysis_failed."""
        report = _make_report(11)
        sess = _mock_session()

        mock_pdf_path = MagicMock()
        mock_pdf_path.exists.return_value = True
        mock_pdf_path.__str__ = lambda self: "/fake/11.pdf"
        mock_pdf_path.__truediv__ = lambda self, other: mock_pdf_path

        mock_settings = MagicMock()
        mock_settings.pdf_base_path = mock_pdf_path

        update_status_mock = AsyncMock()
        short_markdown = "x" * 50  # well below 200

        with patch("run_analysis.settings", mock_settings), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", update_status_mock), \
             patch("run_analysis.extract_key_data", AsyncMock(return_value=None)), \
             patch("run_analysis.convert_pdf_to_markdown", AsyncMock(return_value=(short_markdown, "pymupdf"))), \
             patch("run_analysis.extract_images_from_pdf", AsyncMock(return_value=[])), \
             patch("run_analysis.digitize_charts", AsyncMock()), \
             patch("run_analysis._log_markdown_failure") as mock_log_failure:

            from run_analysis import process_single
            result = await process_single(report)

        assert result["status"] == "low_quality_markdown"
        all_status_args = [str(c) for c in update_status_mock.call_args_list]
        assert any("analysis_failed" in s for s in all_status_args), (
            f"Expected analysis_failed in status calls, got: {all_status_args}"
        )
        mock_log_failure.assert_called_once()
        call_args = mock_log_failure.call_args
        assert call_args.args[0] == 11
        assert call_args.args[1] == "low_quality_markdown"

    @pytest.mark.asyncio
    async def test_good_markdown_does_not_call_log_failure(self):
        """Normal markdown (>=200 chars) must NOT trigger _log_markdown_failure."""
        report = _make_report(12)
        sess = _mock_session()

        mock_pdf_path = MagicMock()
        mock_pdf_path.exists.return_value = True
        mock_pdf_path.__str__ = lambda self: "/fake/12.pdf"
        mock_pdf_path.__truediv__ = lambda self, other: mock_pdf_path

        mock_settings = MagicMock()
        mock_settings.pdf_base_path = mock_pdf_path

        good_markdown = "a" * 300  # above 200

        with patch("run_analysis.settings", mock_settings), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.extract_key_data", AsyncMock(return_value=None)), \
             patch("run_analysis.convert_pdf_to_markdown", AsyncMock(return_value=(good_markdown, "pymupdf"))), \
             patch("run_analysis.extract_images_from_pdf", AsyncMock(return_value=[])), \
             patch("run_analysis.digitize_charts", AsyncMock()), \
             patch("run_analysis.build_user_content", return_value=([], False, 300)), \
             patch("run_analysis._log_markdown_failure") as mock_log_failure:

            from run_analysis import process_single
            result = await process_single(report)

        mock_log_failure.assert_not_called()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_no_markdown_result_has_no_layer2_input(self):
        """When no_markdown, result must NOT contain layer2_input (not queued for Layer2)."""
        report = _make_report(13)
        sess = _mock_session()

        mock_pdf_path = MagicMock()
        mock_pdf_path.exists.return_value = True
        mock_pdf_path.__str__ = lambda self: "/fake/13.pdf"
        mock_pdf_path.__truediv__ = lambda self, other: mock_pdf_path

        mock_settings = MagicMock()
        mock_settings.pdf_base_path = mock_pdf_path

        with patch("run_analysis.settings", mock_settings), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.extract_key_data", AsyncMock(return_value=None)), \
             patch("run_analysis.convert_pdf_to_markdown", AsyncMock(return_value=(None, "pymupdf"))), \
             patch("run_analysis.extract_images_from_pdf", AsyncMock(return_value=[])), \
             patch("run_analysis.digitize_charts", AsyncMock()), \
             patch("run_analysis._log_markdown_failure"):

            from run_analysis import process_single
            result = await process_single(report)

        assert "layer2_input" not in result

    @pytest.mark.asyncio
    async def test_low_quality_markdown_result_has_no_layer2_input(self):
        """When low_quality_markdown, result must NOT contain layer2_input."""
        report = _make_report(14)
        sess = _mock_session()

        mock_pdf_path = MagicMock()
        mock_pdf_path.exists.return_value = True
        mock_pdf_path.__str__ = lambda self: "/fake/14.pdf"
        mock_pdf_path.__truediv__ = lambda self, other: mock_pdf_path

        mock_settings = MagicMock()
        mock_settings.pdf_base_path = mock_pdf_path

        short_markdown = "short " * 10  # 60 chars, below 200

        with patch("run_analysis.settings", mock_settings), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.extract_key_data", AsyncMock(return_value=None)), \
             patch("run_analysis.convert_pdf_to_markdown", AsyncMock(return_value=(short_markdown, "pymupdf"))), \
             patch("run_analysis.extract_images_from_pdf", AsyncMock(return_value=[])), \
             patch("run_analysis.digitize_charts", AsyncMock()), \
             patch("run_analysis._log_markdown_failure"):

            from run_analysis import process_single
            result = await process_single(report)

        assert "layer2_input" not in result


# ---------------------------------------------------------------------------
# End-to-end: main() with markdown failures are NOT re-picked
# ---------------------------------------------------------------------------

class TestMarkdownFailureNotRepicked:
    """
    Reports with analysis_failed status are not in _ANALYZABLE_STATUSES,
    so they won't be picked on subsequent runs.
    """

    def test_analysis_failed_not_in_analyzable_statuses(self):
        """analysis_failed must not appear in _ANALYZABLE_STATUSES inside the query."""
        import inspect
        import run_analysis

        source = inspect.getsource(run_analysis._get_unanalyzed_report_ids)
        assert "analysis_failed" not in source, (
            "analysis_failed should not be in _ANALYZABLE_STATUSES — "
            "failed reports would be re-picked on next run."
        )
        assert "pdf_done" in source
        assert "analysis_pending" in source
