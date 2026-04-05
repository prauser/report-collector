"""Tests for scripts/recover_batches.py."""
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from decimal import Decimal

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers to build fake Anthropic batch/entry objects
# ---------------------------------------------------------------------------

def _make_batch(batch_id: str, status: str, succeeded=0, errored=0, expired=0, processing=0):
    batch = MagicMock()
    batch.id = batch_id
    batch.processing_status = status
    counts = MagicMock()
    counts.succeeded = succeeded
    counts.errored = errored
    counts.expired = expired
    counts.processing = processing
    batch.request_counts = counts
    return batch


def _make_succeeded_entry(custom_id: str, tool_input: dict):
    entry = MagicMock()
    entry.custom_id = custom_id
    entry.result.type = "succeeded"
    msg = MagicMock()
    usage = MagicMock()
    usage.input_tokens = 1000
    usage.output_tokens = 500
    usage.cache_creation_input_tokens = 0
    usage.cache_read_input_tokens = 0
    msg.usage = usage
    block = MagicMock()
    block.type = "tool_use"
    block.name = "extract_layer2"
    block.input = tool_input
    msg.content = [block]
    entry.result.message = msg
    return entry


def _make_failed_entry(custom_id: str, result_type: str = "errored"):
    entry = MagicMock()
    entry.custom_id = custom_id
    entry.result.type = result_type
    return entry


# ---------------------------------------------------------------------------
# Minimal valid tool_input returned by LLM
# ---------------------------------------------------------------------------

_VALID_TOOL_INPUT = {
    "report_category": "stock",
    "meta": {
        "broker": "테스트증권",
        "stock_name": "삼성전자",
        "stock_code": "005930",
    },
    "thesis": {
        "summary": "테스트 요약",
        "sentiment": 0.5,
    },
    "chain": [],
    "extraction_quality": "medium",
    "stock_mentions": [],
    "sector_mentions": [],
    "keywords": [],
}


# ---------------------------------------------------------------------------
# Import the module under test after sys.path is set
# ---------------------------------------------------------------------------

from scripts.recover_batches import (
    _check_and_recover_batch,
    _list_pending_reports,
    _print_summary,
    _analysis_exists,
    _recover_all_batches,
)


# ---------------------------------------------------------------------------
# Tests: _check_and_recover_batch
# ---------------------------------------------------------------------------

class TestCheckAndRecoverBatch:

    @pytest.mark.asyncio
    async def test_retrieve_error_returns_error_status(self):
        """If batch retrieval raises, summary reflects retrieve_error."""
        client = MagicMock()
        client.messages.batches.retrieve = AsyncMock(side_effect=Exception("API error"))

        summary = await _check_and_recover_batch(client, "msgbatch_test", apply=False)

        assert summary["status"] == "retrieve_error"
        assert summary["error"] is not None
        assert "API error" in summary["error"]

    @pytest.mark.asyncio
    async def test_still_processing_returns_early(self):
        """Batch still processing — no result streaming attempted."""
        client = MagicMock()
        batch = _make_batch("msgbatch_test", status="in_progress", processing=5)
        client.messages.batches.retrieve = AsyncMock(return_value=batch)

        summary = await _check_and_recover_batch(client, "msgbatch_test", apply=False)

        assert summary["status"] == "in_progress"
        assert summary["saved"] == 0
        client.messages.batches.results.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_batch_with_no_results(self):
        """Ended batch where all entries expired — graceful, no crash."""
        client = MagicMock()
        batch = _make_batch("msgbatch_test", status="ended", succeeded=0, errored=0, expired=2)
        client.messages.batches.retrieve = AsyncMock(return_value=batch)

        # results() returns an async iterable of expired entries
        entry1 = _make_failed_entry("report-101", result_type="expired")
        entry2 = _make_failed_entry("report-102", result_type="expired")

        async def _aiter(*_):
            for e in [entry1, entry2]:
                yield e

        results_cm = MagicMock()
        results_cm.__aiter__ = _aiter
        client.messages.batches.results = AsyncMock(return_value=results_cm)

        summary = await _check_and_recover_batch(client, "msgbatch_test", apply=False)

        assert summary["status"] == "ended"
        assert summary["expired"] == 2
        assert summary["saved"] == 0
        assert 101 in summary["report_ids_failed"]
        assert 102 in summary["report_ids_failed"]

    @pytest.mark.asyncio
    async def test_dry_run_does_not_save(self):
        """Dry run: results are parsed but save_analysis is never called."""
        client = MagicMock()
        batch = _make_batch("msgbatch_test", status="ended", succeeded=1)
        client.messages.batches.retrieve = AsyncMock(return_value=batch)

        entry = _make_succeeded_entry("report-999", _VALID_TOOL_INPUT)

        async def _aiter(*_):
            yield entry

        results_cm = MagicMock()
        results_cm.__aiter__ = _aiter
        client.messages.batches.results = AsyncMock(return_value=results_cm)

        with patch("scripts.recover_batches.save_analysis") as mock_save, \
             patch("scripts.recover_batches.record_llm_usage", new_callable=AsyncMock):

            summary = await _check_and_recover_batch(client, "msgbatch_test", apply=False)

        mock_save.assert_not_called()
        assert summary["saved"] == 0
        assert 999 in summary["report_ids_succeeded"]

    @pytest.mark.asyncio
    async def test_apply_saves_succeeded_results(self):
        """With --apply, succeeded entries are saved to DB."""
        client = MagicMock()
        batch = _make_batch("msgbatch_test", status="ended", succeeded=1)
        client.messages.batches.retrieve = AsyncMock(return_value=batch)

        entry = _make_succeeded_entry("report-42", _VALID_TOOL_INPUT)

        async def _aiter(*_):
            yield entry

        results_cm = MagicMock()
        results_cm.__aiter__ = _aiter
        client.messages.batches.results = AsyncMock(return_value=results_cm)

        mock_report = MagicMock()
        mock_report.id = 42

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_report)
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        # begin_nested() context manager
        nested_cm = AsyncMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        nested_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin_nested = MagicMock(return_value=nested_cm)
        mock_session.execute = AsyncMock()

        # AsyncSessionLocal context manager
        session_cm = AsyncMock()
        session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=session_cm), \
             patch("scripts.recover_batches.save_analysis", new_callable=AsyncMock) as mock_save, \
             patch("scripts.recover_batches.record_llm_usage", new_callable=AsyncMock), \
             patch("scripts.recover_batches._apply_layer2_meta", return_value={}):

            summary = await _check_and_recover_batch(client, "msgbatch_test", apply=True)

        mock_save.assert_called_once()
        call_args = mock_save.call_args
        assert call_args[0][1] == 42  # report_id
        assert summary["saved"] == 1
        assert 42 in summary["report_ids_succeeded"]

    @pytest.mark.asyncio
    async def test_apply_marks_failed_entries_as_analysis_failed(self):
        """With --apply, errored/expired entries set pipeline_status=analysis_failed."""
        client = MagicMock()
        batch = _make_batch("msgbatch_test", status="ended", errored=1)
        client.messages.batches.retrieve = AsyncMock(return_value=batch)

        entry = _make_failed_entry("report-77", result_type="errored")

        async def _aiter(*_):
            yield entry

        results_cm = MagicMock()
        results_cm.__aiter__ = _aiter
        client.messages.batches.results = AsyncMock(return_value=results_cm)

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        session_cm = AsyncMock()
        session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=session_cm), \
             patch("scripts.recover_batches.update_pipeline_status", new_callable=AsyncMock) as mock_update:

            summary = await _check_and_recover_batch(client, "msgbatch_test", apply=True)

        mock_update.assert_called_once_with(mock_session, 77, "analysis_failed")
        assert 77 in summary["report_ids_failed"]

    @pytest.mark.asyncio
    async def test_unknown_custom_id_format_collected(self):
        """Custom IDs that don't match 'report-{int}' are flagged as unknown."""
        client = MagicMock()
        batch = _make_batch("msgbatch_test", status="ended", succeeded=1)
        client.messages.batches.retrieve = AsyncMock(return_value=batch)

        entry = _make_succeeded_entry("custom-weird-id", _VALID_TOOL_INPUT)

        async def _aiter(*_):
            yield entry

        results_cm = MagicMock()
        results_cm.__aiter__ = _aiter
        client.messages.batches.results = AsyncMock(return_value=results_cm)

        with patch("scripts.recover_batches.record_llm_usage", new_callable=AsyncMock):
            summary = await _check_and_recover_batch(client, "msgbatch_test", apply=False)

        assert "custom-weird-id" in summary["report_ids_unknown_custom_id"]
        assert summary["saved"] == 0

    @pytest.mark.asyncio
    async def test_results_stream_error_returns_results_error(self):
        """If streaming results raises, summary reflects results_error."""
        client = MagicMock()
        batch = _make_batch("msgbatch_test", status="ended", succeeded=2)
        client.messages.batches.retrieve = AsyncMock(return_value=batch)
        client.messages.batches.results = AsyncMock(side_effect=Exception("stream failed"))

        summary = await _check_and_recover_batch(client, "msgbatch_test", apply=False)

        assert summary["status"] == "results_error"
        assert "stream failed" in summary["error"]

    @pytest.mark.asyncio
    async def test_apply_handles_report_not_found(self):
        """If report_id not in DB, log warning and skip without crashing."""
        client = MagicMock()
        batch = _make_batch("msgbatch_test", status="ended", succeeded=1)
        client.messages.batches.retrieve = AsyncMock(return_value=batch)

        entry = _make_succeeded_entry("report-9999", _VALID_TOOL_INPUT)

        async def _aiter(*_):
            yield entry

        results_cm = MagicMock()
        results_cm.__aiter__ = _aiter
        client.messages.batches.results = AsyncMock(return_value=results_cm)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)  # report not found

        session_cm = AsyncMock()
        session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=session_cm), \
             patch("scripts.recover_batches.save_analysis", new_callable=AsyncMock) as mock_save, \
             patch("scripts.recover_batches.record_llm_usage", new_callable=AsyncMock):

            summary = await _check_and_recover_batch(client, "msgbatch_test", apply=True)

        mock_save.assert_not_called()
        assert summary["saved"] == 0


# ---------------------------------------------------------------------------
# Tests: _print_summary
# ---------------------------------------------------------------------------

class TestPrintSummary:

    def test_print_summary_dry_run(self, capsys):
        summary = {
            "batch_id": "msgbatch_abc",
            "status": "ended",
            "succeeded": 3,
            "errored": 1,
            "expired": 0,
            "total": 4,
            "report_ids_succeeded": [1, 2, 3],
            "report_ids_failed": [4],
            "report_ids_unknown_custom_id": [],
            "saved": 0,
            "error": None,
        }
        _print_summary(summary, dry_run=True)
        captured = capsys.readouterr()
        assert "msgbatch_abc" in captured.out
        assert "DRY RUN" in captured.out
        assert "3" in captured.out

    def test_print_summary_apply(self, capsys):
        summary = {
            "batch_id": "msgbatch_xyz",
            "status": "ended",
            "succeeded": 5,
            "errored": 0,
            "expired": 0,
            "total": 5,
            "report_ids_succeeded": [10, 11, 12, 13, 14],
            "report_ids_failed": [],
            "report_ids_unknown_custom_id": [],
            "saved": 5,
            "error": None,
        }
        _print_summary(summary, dry_run=False)
        captured = capsys.readouterr()
        assert "Saved to DB: 5" in captured.out

    def test_print_summary_error(self, capsys):
        summary = {
            "batch_id": "msgbatch_err",
            "status": "retrieve_error",
            "succeeded": 0,
            "errored": 0,
            "expired": 0,
            "total": 0,
            "report_ids_succeeded": [],
            "report_ids_failed": [],
            "report_ids_unknown_custom_id": [],
            "saved": 0,
            "error": "Connection refused",
        }
        _print_summary(summary, dry_run=True)
        captured = capsys.readouterr()
        assert "Connection refused" in captured.out


# ---------------------------------------------------------------------------
# Tests: _list_pending_reports
# ---------------------------------------------------------------------------

class TestListPendingReports:

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        """_list_pending_reports returns list of dicts with expected keys."""
        import datetime

        row = MagicMock()
        row.id = 55
        row.title = "Test Report"
        row.report_date = datetime.date(2024, 1, 15)
        row.source_channel = "@testchannel"

        mock_result = MagicMock()
        mock_result.all = MagicMock(return_value=[(row,)])

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        session_cm = AsyncMock()
        session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=session_cm):
            result = await _list_pending_reports()

        assert len(result) == 1
        assert result[0]["id"] == 55
        assert result[0]["title"] == "Test Report"
        assert result[0]["report_date"] == "2024-01-15"
        assert result[0]["channel"] == "@testchannel"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_pending(self):
        """Returns empty list when no reports are pending."""
        mock_result = MagicMock()
        mock_result.all = MagicMock(return_value=[])

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        session_cm = AsyncMock()
        session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=session_cm):
            result = await _list_pending_reports()

        assert result == []


# ---------------------------------------------------------------------------
# Tests: CLI argument parsing (via argparse)
# ---------------------------------------------------------------------------

class TestCLI:

    def test_help_exits_cleanly(self):
        """--help should print help and exit with code 0."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "scripts/recover_batches.py", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert result.returncode == 0
        assert "batch-ids" in result.stdout
        assert "list-pending" in result.stdout
        assert "apply" in result.stdout

    def test_no_args_exits_zero(self):
        """No arguments prints help and exits 0."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "scripts/recover_batches.py"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert result.returncode == 0

    def test_batch_ids_file_not_found(self):
        """--batch-ids-file with nonexistent path exits with error."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "scripts/recover_batches.py",
             "--batch-ids-file", "/nonexistent/path/batches.txt"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert result.returncode == 1
        assert "Cannot read" in result.stderr or "Cannot read" in result.stdout

    def test_recover_all_in_help(self):
        """--help output includes --recover-all flag."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "scripts/recover_batches.py", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert result.returncode == 0
        assert "recover-all" in result.stdout


# ---------------------------------------------------------------------------
# Tests: _analysis_exists
# ---------------------------------------------------------------------------

class TestAnalysisExists:

    @pytest.mark.asyncio
    async def test_returns_true_when_row_exists(self):
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=True)
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await _analysis_exists(mock_session, 42)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_row(self):
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=False)
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await _analysis_exists(mock_session, 99)
        assert result is False


# ---------------------------------------------------------------------------
# Tests: _recover_all_batches
# ---------------------------------------------------------------------------

def _make_async_iter(items):
    """Return an async-iterable wrapper around a list."""
    class AsyncIter:
        def __aiter__(self):
            return self._gen()
        async def _gen(self):
            for item in items:
                yield item
    return AsyncIter()


class TestRecoverAllBatches:

    @pytest.mark.asyncio
    async def test_no_eligible_batches(self, capsys):
        """When no batches are eligible (all processing), nothing is done."""
        client = MagicMock()

        batch_not_ended = _make_batch("msgbatch_a", status="in_progress", processing=5)
        batch_no_succeeded = _make_batch("msgbatch_b", status="ended", succeeded=0, errored=2)

        client.messages.batches.list = AsyncMock(
            return_value=_make_async_iter([batch_not_ended, batch_no_succeeded])
        )

        await _recover_all_batches(client, apply=False)

        captured = capsys.readouterr()
        assert "0 eligible" in captured.out
        client.messages.batches.results.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_skips_already_existing(self, capsys):
        """Dry run: entries already in DB are counted as already_exists, not saved."""
        client = MagicMock()

        batch = _make_batch("msgbatch_exists", status="ended", succeeded=1)
        client.messages.batches.list = AsyncMock(
            return_value=_make_async_iter([batch])
        )

        entry = _make_succeeded_entry("report-10", _VALID_TOOL_INPUT)
        client.messages.batches.results = AsyncMock(
            return_value=_make_async_iter([entry])
        )

        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=True)  # already exists
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        session_cm = AsyncMock()
        session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=session_cm):
            await _recover_all_batches(client, apply=False)

        captured = capsys.readouterr()
        assert "already_exists=1" in captured.out
        assert "would save=0" in captured.out

    @pytest.mark.asyncio
    async def test_dry_run_counts_pending_as_would_save(self, capsys):
        """Dry run: entries not in DB + analysis_pending are counted as would-save."""
        client = MagicMock()

        batch = _make_batch("msgbatch_pending", status="ended", succeeded=1)
        client.messages.batches.list = AsyncMock(
            return_value=_make_async_iter([batch])
        )

        entry = _make_succeeded_entry("report-20", _VALID_TOOL_INPUT)
        client.messages.batches.results = AsyncMock(
            return_value=_make_async_iter([entry])
        )

        mock_report = MagicMock()
        mock_report.pipeline_status = "analysis_pending"

        mock_session = AsyncMock()
        # _analysis_exists returns False
        mock_exists_result = MagicMock()
        mock_exists_result.scalar = MagicMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_exists_result)
        # session.get returns report with analysis_pending
        mock_session.get = AsyncMock(return_value=mock_report)

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_session)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=cm):
            await _recover_all_batches(client, apply=False)

        captured = capsys.readouterr()
        assert "would save=1" in captured.out
        assert "[DRY RUN] Would save: 1" in captured.out

    @pytest.mark.asyncio
    async def test_dry_run_skips_wrong_pipeline_status(self, capsys):
        """Dry run: entries where report is not analysis_pending are skipped."""
        client = MagicMock()

        batch = _make_batch("msgbatch_done", status="ended", succeeded=1)
        client.messages.batches.list = AsyncMock(
            return_value=_make_async_iter([batch])
        )

        entry = _make_succeeded_entry("report-30", _VALID_TOOL_INPUT)
        client.messages.batches.results = AsyncMock(
            return_value=_make_async_iter([entry])
        )

        mock_report = MagicMock()
        mock_report.pipeline_status = "done"  # already done, should skip

        mock_session = AsyncMock()
        # _analysis_exists returns False
        mock_exists_result = MagicMock()
        mock_exists_result.scalar = MagicMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_exists_result)
        # session.get returns report with wrong status
        mock_session.get = AsyncMock(return_value=mock_report)

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_session)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=cm):
            await _recover_all_batches(client, apply=False)

        captured = capsys.readouterr()
        assert "wrong_status=1" in captured.out
        assert "would save=0" in captured.out

    @pytest.mark.asyncio
    async def test_apply_saves_analysis_pending_entries(self, capsys):
        """With apply=True, entries not in DB with analysis_pending are saved."""
        client = MagicMock()

        batch = _make_batch("msgbatch_apply", status="ended", succeeded=1)
        client.messages.batches.list = AsyncMock(
            return_value=_make_async_iter([batch])
        )

        entry = _make_succeeded_entry("report-50", _VALID_TOOL_INPUT)
        client.messages.batches.results = AsyncMock(
            return_value=_make_async_iter([entry])
        )

        mock_report = MagicMock()
        mock_report.pipeline_status = "analysis_pending"
        mock_report.id = 50

        mock_session = AsyncMock()
        # _analysis_exists returns False
        mock_exists_result = MagicMock()
        mock_exists_result.scalar = MagicMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_exists_result)
        # session.get returns report with analysis_pending
        mock_session.get = AsyncMock(return_value=mock_report)
        mock_session.commit = AsyncMock()
        nested_cm = AsyncMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        nested_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin_nested = MagicMock(return_value=nested_cm)

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_session)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=cm), \
             patch("scripts.recover_batches.save_analysis", new_callable=AsyncMock) as mock_save, \
             patch("scripts.recover_batches.record_llm_usage", new_callable=AsyncMock), \
             patch("scripts.recover_batches._apply_layer2_meta", return_value={}):

            await _recover_all_batches(client, apply=True)

        mock_save.assert_called_once()
        call_args = mock_save.call_args
        assert call_args[0][1] == 50  # report_id
        captured = capsys.readouterr()
        assert "Total saved to DB:    1" in captured.out

    @pytest.mark.asyncio
    async def test_results_stream_error_handled_gracefully(self, capsys):
        """If streaming batch results fails, batch is counted as error and continues."""
        client = MagicMock()

        batch = _make_batch("msgbatch_err", status="ended", succeeded=3)
        client.messages.batches.list = AsyncMock(
            return_value=_make_async_iter([batch])
        )
        client.messages.batches.results = AsyncMock(side_effect=Exception("network timeout"))

        await _recover_all_batches(client, apply=False)

        captured = capsys.readouterr()
        assert "ERROR streaming results" in captured.out
        assert "Errors:               1" in captured.out

    @pytest.mark.asyncio
    async def test_unknown_custom_id_silently_skipped(self, capsys):
        """Entries with non-standard custom_id format are silently skipped."""
        client = MagicMock()

        batch = _make_batch("msgbatch_weird", status="ended", succeeded=1)
        client.messages.batches.list = AsyncMock(
            return_value=_make_async_iter([batch])
        )

        entry = _make_succeeded_entry("weird-format-99", _VALID_TOOL_INPUT)
        client.messages.batches.results = AsyncMock(
            return_value=_make_async_iter([entry])
        )

        # No DB calls should happen
        with patch("scripts.recover_batches.AsyncSessionLocal") as mock_session_local:
            await _recover_all_batches(client, apply=False)

        captured = capsys.readouterr()
        # No saves, no errors from skipping unknown IDs
        assert "[DRY RUN] Would save: 0" in captured.out

    @pytest.mark.asyncio
    async def test_non_succeeded_entries_ignored(self, capsys):
        """errored/expired entries in the stream are ignored by _recover_all_batches.

        The batch must have succeeded > 0 to be eligible. We use succeeded=2
        so the batch passes the filter, but the actual entries returned are all
        errored/expired — they should be silently skipped (no DB calls).
        """
        client = MagicMock()

        # succeeded=2 so the batch is eligible; the actual stream has no succeeded entries
        batch = _make_batch("msgbatch_mixed", status="ended", succeeded=2, errored=2)
        client.messages.batches.list = AsyncMock(
            return_value=_make_async_iter([batch])
        )

        # Stream only returns errored/expired entries
        e1 = _make_failed_entry("report-60", result_type="errored")
        e2 = _make_failed_entry("report-61", result_type="expired")
        client.messages.batches.results = AsyncMock(
            return_value=_make_async_iter([e1, e2])
        )

        mock_session = AsyncMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_session)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("scripts.recover_batches.AsyncSessionLocal", return_value=cm):
            await _recover_all_batches(client, apply=False)

        # Session opened per batch, but no queries executed for non-succeeded entries
        mock_session.execute.assert_not_called()
        mock_session.get.assert_not_called()
        captured = capsys.readouterr()
        assert "would save=0" in captured.out
