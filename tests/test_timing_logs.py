"""Tests for timing instrumentation added in task-1-timing-logs.

Verifies:
1. process_single() logs step_done with duration_s for key_data, markdown, images_charts
2. process_single() logs report_done with duration_s at the end (including early-exit branches)
3. _submit_and_poll_batch() logs layer2_batch_completed with duration_s
4. duration_s values are non-negative floats
5. No existing behavior changes (all logic is identical, only log calls added)
"""
import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _make_report(report_id: int = 1) -> MagicMock:
    r = MagicMock()
    r.id = report_id
    r.source_channel = "test_channel"
    r.raw_text = "raw"
    r.title = f"Report {report_id}"
    r.pdf_path = f"pdfs/test_{report_id}.pdf"
    return r


def _mock_session():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    return sess


def _collect_step_done_calls(mock_log_info):
    """Extract all step_done log call kwargs from mock_log.info calls."""
    calls = []
    for c in mock_log_info.call_args_list:
        event = c.args[0] if c.args else c.kwargs.get("event")
        if event == "step_done":
            calls.append(c.kwargs)
    return calls


def _collect_report_done_calls(mock_log_info):
    """Extract all report_done log call kwargs from mock_log.info calls."""
    calls = []
    for c in mock_log_info.call_args_list:
        event = c.args[0] if c.args else c.kwargs.get("event")
        if event == "report_done":
            calls.append(c.kwargs)
    return calls


# ──────────────────────────────────────────────
# process_single timing tests
# ──────────────────────────────────────────────

class TestProcessSingleTimingLogs:
    """process_single() emits step_done and report_done with duration_s."""

    @pytest.mark.asyncio
    async def test_all_steps_logged_with_duration_s(self):
        """Each step (key_data, markdown, images_charts) is logged with duration_s >= 0."""
        from run_analysis import process_single

        report = _make_report(1)

        with patch("run_analysis.settings") as mock_settings, \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.AsyncSessionLocal", return_value=_mock_session()), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock, return_value=None), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# markdown content with enough text " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock, return_value=[]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock), \
             patch("run_analysis.build_user_content", return_value=("content", False, 100)), \
             patch("run_analysis.log") as mock_log:

            mock_settings.pdf_base_path = Path("/fake")
            mock_settings.gemini_api_key = "fake"

            # Make the PDF path appear to exist
            with patch("pathlib.Path.exists", return_value=True):
                await process_single(report)

        step_done_calls = _collect_step_done_calls(mock_log.info)
        step_names = [c["step"] for c in step_done_calls]

        assert "key_data" in step_names, f"key_data step_done not logged; got: {step_names}"
        assert "markdown" in step_names, f"markdown step_done not logged; got: {step_names}"
        assert "images_charts" in step_names, f"images_charts step_done not logged; got: {step_names}"

        for c in step_done_calls:
            assert "duration_s" in c, f"duration_s missing in step_done for step={c.get('step')}"
            assert isinstance(c["duration_s"], float), f"duration_s is not float: {c['duration_s']!r}"
            assert c["duration_s"] >= 0, f"duration_s is negative: {c['duration_s']}"

    @pytest.mark.asyncio
    async def test_report_done_logged_with_duration_s(self):
        """report_done is logged with duration_s at the end of a normal run."""
        from run_analysis import process_single

        report = _make_report(2)

        with patch("run_analysis.settings") as mock_settings, \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.AsyncSessionLocal", return_value=_mock_session()), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock, return_value=None), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# markdown content with enough text " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock, return_value=[]), \
             patch("run_analysis.digitize_charts", new_callable=AsyncMock), \
             patch("run_analysis.build_user_content", return_value=("content", False, 100)), \
             patch("run_analysis.log") as mock_log:

            mock_settings.pdf_base_path = Path("/fake")
            mock_settings.gemini_api_key = "fake"

            with patch("pathlib.Path.exists", return_value=True):
                await process_single(report)

        report_done_calls = _collect_report_done_calls(mock_log.info)
        assert len(report_done_calls) == 1, f"Expected 1 report_done log, got {len(report_done_calls)}"
        rd = report_done_calls[0]
        assert "duration_s" in rd
        assert isinstance(rd["duration_s"], float)
        assert rd["duration_s"] >= 0

    @pytest.mark.asyncio
    async def test_report_done_has_report_id(self):
        """report_done log includes report_id."""
        from run_analysis import process_single

        report = _make_report(42)

        with patch("run_analysis.settings") as mock_settings, \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.AsyncSessionLocal", return_value=_mock_session()), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock, return_value=None), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# markdown content with enough text " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock, return_value=[]), \
             patch("run_analysis.build_user_content", return_value=("content", False, 100)), \
             patch("run_analysis.log") as mock_log:

            mock_settings.pdf_base_path = Path("/fake")

            with patch("pathlib.Path.exists", return_value=True):
                await process_single(report)

        report_done_calls = _collect_report_done_calls(mock_log.info)
        assert len(report_done_calls) == 1
        assert report_done_calls[0]["report_id"] == 42

    @pytest.mark.asyncio
    async def test_step_done_has_report_id(self):
        """step_done log includes report_id."""
        from run_analysis import process_single

        report = _make_report(7)

        with patch("run_analysis.settings") as mock_settings, \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.AsyncSessionLocal", return_value=_mock_session()), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock, return_value=None), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# markdown " * 30, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock, return_value=[]), \
             patch("run_analysis.build_user_content", return_value=("content", False, 100)), \
             patch("run_analysis.log") as mock_log:

            mock_settings.pdf_base_path = Path("/fake")

            with patch("pathlib.Path.exists", return_value=True):
                await process_single(report)

        step_done_calls = _collect_step_done_calls(mock_log.info)
        for c in step_done_calls:
            assert c.get("report_id") == 7, f"report_id missing or wrong in step_done: {c}"

    @pytest.mark.asyncio
    async def test_low_quality_markdown_still_logs_report_done(self):
        """Early exit for low_quality_markdown branch also logs report_done."""
        from run_analysis import process_single

        report = _make_report(3)
        short_md = "x" * 10  # shorter than _MIN_MARKDOWN_CHARS (200)

        with patch("run_analysis.settings") as mock_settings, \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.AsyncSessionLocal", return_value=_mock_session()), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock, return_value=None), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=(short_md, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock, return_value=[]), \
             patch("run_analysis.log") as mock_log:

            mock_settings.pdf_base_path = Path("/fake")

            with patch("pathlib.Path.exists", return_value=True):
                result = await process_single(report)

        assert result["status"] == "low_quality_markdown"
        report_done_calls = _collect_report_done_calls(mock_log.info)
        assert len(report_done_calls) == 1
        rd = report_done_calls[0]
        assert "duration_s" in rd
        assert rd["duration_s"] >= 0

    @pytest.mark.asyncio
    async def test_pdf_not_found_no_timing_logs(self):
        """pdf_not_found early exit: timing hasn't started yet, no report_done log."""
        from run_analysis import process_single

        report = _make_report(4)

        with patch("run_analysis.settings") as mock_settings, \
             patch("run_analysis.log") as mock_log:

            mock_settings.pdf_base_path = Path("/fake")

            with patch("pathlib.Path.exists", return_value=False):
                result = await process_single(report)

        assert result["status"] == "error"
        assert result["error"] == "pdf_not_found"
        # No report_done log since timing hasn't started
        report_done_calls = _collect_report_done_calls(mock_log.info)
        assert len(report_done_calls) == 0

    @pytest.mark.asyncio
    async def test_duration_s_is_rounded_to_2_decimals(self):
        """duration_s values have at most 2 decimal places."""
        from run_analysis import process_single

        report = _make_report(5)

        with patch("run_analysis.settings") as mock_settings, \
             patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
             patch("run_analysis.AsyncSessionLocal", return_value=_mock_session()), \
             patch("run_analysis.extract_key_data", new_callable=AsyncMock, return_value=None), \
             patch("run_analysis.convert_pdf_to_markdown", new_callable=AsyncMock,
                   return_value=("# enough markdown " * 20, "pymupdf4llm")), \
             patch("run_analysis.extract_images_from_pdf", new_callable=AsyncMock, return_value=[]), \
             patch("run_analysis.build_user_content", return_value=("content", False, 100)), \
             patch("run_analysis.log") as mock_log:

            mock_settings.pdf_base_path = Path("/fake")

            with patch("pathlib.Path.exists", return_value=True):
                await process_single(report)

        all_timing_calls = _collect_step_done_calls(mock_log.info) + _collect_report_done_calls(mock_log.info)
        for c in all_timing_calls:
            d = c["duration_s"]
            # round(x, 2) should produce a value with at most 2 decimal places
            assert round(d, 2) == d, f"duration_s not rounded to 2 decimals: {d}"


# ──────────────────────────────────────────────
# _submit_and_poll_batch timing tests
# ──────────────────────────────────────────────

class TestSubmitAndPollBatchTimingLogs:
    """_submit_and_poll_batch() emits layer2_batch_completed with duration_s."""

    @pytest.mark.asyncio
    async def test_batch_completed_logged_with_duration_s(self):
        """layer2_batch_completed log includes duration_s >= 0."""
        from parser.layer2_extractor import _submit_and_poll_batch

        # Mock Anthropic batch client
        mock_batch = MagicMock()
        mock_batch.id = "batch_test_001"
        mock_batch.processing_status = "ended"
        mock_batch.request_counts = MagicMock(
            succeeded=2, errored=0, expired=0, processing=0
        )

        mock_entry_1 = MagicMock()
        mock_entry_1.custom_id = "report-1"
        mock_entry_1.result.type = "succeeded"
        mock_entry_1.result.message.content = []
        mock_entry_1.result.message.usage = MagicMock(
            input_tokens=100, output_tokens=50,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )

        mock_entry_2 = MagicMock()
        mock_entry_2.custom_id = "report-2"
        mock_entry_2.result.type = "succeeded"
        mock_entry_2.result.message.content = []
        mock_entry_2.result.message.usage = MagicMock(
            input_tokens=100, output_tokens=50,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )

        async def _fake_results_iter(*args, **kwargs):
            for entry in [mock_entry_1, mock_entry_2]:
                yield entry

        mock_client = AsyncMock()
        mock_client.messages.batches.create = AsyncMock(return_value=mock_batch)
        mock_client.messages.batches.retrieve = AsyncMock(return_value=mock_batch)
        mock_client.messages.batches.results = AsyncMock(return_value=_fake_results_iter())

        logged_batch_completed = []

        def capture_info(event, **kwargs):
            if event == "layer2_batch_completed":
                logged_batch_completed.append(kwargs)

        with patch("parser.layer2_extractor._get_client", return_value=mock_client), \
             patch("parser.layer2_extractor.log") as mock_log:

            mock_log.info.side_effect = capture_info
            mock_log.debug = MagicMock()
            mock_log.warning = MagicMock()

            fake_requests = [MagicMock(), MagicMock()]
            await _submit_and_poll_batch(fake_requests)

        assert len(logged_batch_completed) == 1, \
            f"Expected 1 layer2_batch_completed, got {len(logged_batch_completed)}"
        completed = logged_batch_completed[0]
        assert "duration_s" in completed, "duration_s missing from layer2_batch_completed log"
        assert isinstance(completed["duration_s"], float), \
            f"duration_s is not float: {completed['duration_s']!r}"
        assert completed["duration_s"] >= 0, \
            f"duration_s is negative: {completed['duration_s']}"

    @pytest.mark.asyncio
    async def test_batch_completed_preserves_existing_fields(self):
        """layer2_batch_completed log still contains batch_id, succeeded, errored, expired."""
        from parser.layer2_extractor import _submit_and_poll_batch

        mock_batch = MagicMock()
        mock_batch.id = "batch_xyz_999"
        mock_batch.processing_status = "ended"
        mock_batch.request_counts = MagicMock(
            succeeded=5, errored=1, expired=0, processing=0
        )

        async def _fake_results_iter(*args, **kwargs):
            entry = MagicMock()
            entry.custom_id = "report-5"
            entry.result.type = "errored"
            yield entry

        mock_client = AsyncMock()
        mock_client.messages.batches.create = AsyncMock(return_value=mock_batch)
        mock_client.messages.batches.retrieve = AsyncMock(return_value=mock_batch)
        mock_client.messages.batches.results = AsyncMock(return_value=_fake_results_iter())

        logged_batch_completed = []

        def capture_info(event, **kwargs):
            if event == "layer2_batch_completed":
                logged_batch_completed.append(kwargs)

        with patch("parser.layer2_extractor._get_client", return_value=mock_client), \
             patch("parser.layer2_extractor.log") as mock_log:

            mock_log.info.side_effect = capture_info
            mock_log.debug = MagicMock()
            mock_log.warning = MagicMock()

            await _submit_and_poll_batch([MagicMock()])

        assert len(logged_batch_completed) == 1
        completed = logged_batch_completed[0]
        # Original fields must still be present
        assert completed["batch_id"] == "batch_xyz_999"
        assert completed["succeeded"] == 5
        assert completed["errored"] == 1
        assert completed["expired"] == 0
        # New timing field
        assert "duration_s" in completed

    @pytest.mark.asyncio
    async def test_batch_duration_measures_submit_to_completion(self):
        """duration_s reflects wall-clock time from submission to completion."""
        from parser.layer2_extractor import _submit_and_poll_batch

        mock_batch = MagicMock()
        mock_batch.id = "batch_slow_001"
        mock_batch.processing_status = "ended"
        mock_batch.request_counts = MagicMock(
            succeeded=1, errored=0, expired=0, processing=0
        )

        async def _fake_results_iter(*args, **kwargs):
            entry = MagicMock()
            entry.custom_id = "report-1"
            entry.result.type = "succeeded"
            entry.result.message.content = []
            entry.result.message.usage = MagicMock(
                input_tokens=10, output_tokens=5,
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
            )
            yield entry

        sleep_duration = 0.05  # 50ms artificial delay

        async def _slow_create(*args, **kwargs):
            await asyncio.sleep(sleep_duration)
            return mock_batch

        mock_client = AsyncMock()
        mock_client.messages.batches.create = _slow_create
        mock_client.messages.batches.retrieve = AsyncMock(return_value=mock_batch)
        mock_client.messages.batches.results = AsyncMock(return_value=_fake_results_iter())

        logged_batch_completed = []

        def capture_info(event, **kwargs):
            if event == "layer2_batch_completed":
                logged_batch_completed.append(kwargs)

        with patch("parser.layer2_extractor._get_client", return_value=mock_client), \
             patch("parser.layer2_extractor.log") as mock_log:

            mock_log.info.side_effect = capture_info
            mock_log.debug = MagicMock()
            mock_log.warning = MagicMock()

            await _submit_and_poll_batch([MagicMock()])

        assert len(logged_batch_completed) == 1
        d = logged_batch_completed[0]["duration_s"]
        # Should be at least sleep_duration (50ms = 0.05s)
        assert d >= sleep_duration, f"duration_s={d} should be >= {sleep_duration}"


# ──────────────────────────────────────────────
# Smoke test: no regressions in imports
# ──────────────────────────────────────────────

class TestTimingImports:
    """Verify that `time` module is imported in the modified modules."""

    def test_time_imported_in_run_analysis(self):
        import run_analysis
        import time as _time
        assert hasattr(run_analysis, "time") or "time" in dir(run_analysis), \
            "time module not accessible in run_analysis"

    def test_time_imported_in_layer2_extractor(self):
        import parser.layer2_extractor as le
        import importlib
        # Re-import to check module source has `import time`
        import inspect
        src = inspect.getsource(le)
        assert "import time" in src, "import time not found in layer2_extractor"
