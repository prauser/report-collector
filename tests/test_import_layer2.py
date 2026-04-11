"""Tests for scripts/import_layer2.py.

Verifies:
1. Normal JSONL parsing + make_layer2_result call
2. status="failed" lines are skipped
3. Dry-run mode: no DB calls
4. Already-done reports are skipped
5. Batch commit behavior
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Minimal valid tool_input / JSONL record fixtures
# ---------------------------------------------------------------------------

_VALID_TOOL_INPUT = {
    "report_category": "stock",
    "category_confidence": 0.9,
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


def _make_success_record(report_id: int, tool_input: dict | None = None) -> dict:
    return {
        "report_id": report_id,
        "status": "success",
        "result": tool_input or _VALID_TOOL_INPUT,
    }


def _make_failed_record(report_id: int) -> dict:
    return {
        "report_id": report_id,
        "status": "failed",
        "result": None,
    }


def _make_error_record(report_id: int) -> dict:
    return {
        "report_id": report_id,
        "status": "error",
        "result": None,
        "error": "some error message",
    }


def _write_jsonl(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fp:
        for r in records:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

from scripts.import_layer2 import (
    _parse_jsonl,
    _is_already_done,
    _process_record,
    main,
)


# ---------------------------------------------------------------------------
# Tests: _parse_jsonl
# ---------------------------------------------------------------------------

class TestParseJsonl:

    def test_reads_valid_jsonl(self, tmp_path):
        records = [_make_success_record(1), _make_success_record(2)]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        result = _parse_jsonl(path)

        assert len(result) == 2
        assert result[0]["report_id"] == 1
        assert result[1]["report_id"] == 2

    def test_skips_empty_lines(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(_make_success_record(1)) + "\n")
            fp.write("\n")
            fp.write(json.dumps(_make_success_record(2)) + "\n")

        result = _parse_jsonl(path)

        assert len(result) == 2

    def test_handles_invalid_json_line_gracefully(self, tmp_path, capsys):
        path = str(tmp_path / "test.jsonl")
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(_make_success_record(1)) + "\n")
            fp.write("not-valid-json\n")
            fp.write(json.dumps(_make_success_record(3)) + "\n")

        result = _parse_jsonl(path)

        # Only valid lines returned
        assert len(result) == 2
        assert result[0]["report_id"] == 1
        assert result[1]["report_id"] == 3

        # Warning printed
        captured = capsys.readouterr()
        assert "WARN" in captured.out or "JSON parse error" in captured.out


# ---------------------------------------------------------------------------
# Tests: _is_already_done
# ---------------------------------------------------------------------------

class TestIsAlreadyDone:

    @pytest.mark.asyncio
    async def test_returns_true_when_done(self):
        session = AsyncMock()
        result = MagicMock()
        result.scalar.return_value = "done"
        session.execute = AsyncMock(return_value=result)

        assert await _is_already_done(session, 42) is True

    @pytest.mark.asyncio
    async def test_returns_false_when_pending(self):
        session = AsyncMock()
        result = MagicMock()
        result.scalar.return_value = "analysis_pending"
        session.execute = AsyncMock(return_value=result)

        assert await _is_already_done(session, 42) is False

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        session = AsyncMock()
        result = MagicMock()
        result.scalar.return_value = None
        session.execute = AsyncMock(return_value=result)

        assert await _is_already_done(session, 999) is False


# ---------------------------------------------------------------------------
# Tests: _process_record
# ---------------------------------------------------------------------------

class TestProcessRecord:

    def _make_session(self, pipeline_status: str = "analysis_pending", report_exists: bool = True):
        """Build a mock AsyncSession."""
        session = AsyncMock()

        # _is_already_done query
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = pipeline_status
        session.execute = AsyncMock(return_value=scalar_result)

        # session.get returns Report mock or None
        if report_exists:
            report = MagicMock()
            report.id = 1
            report.broker = "테스트증권"
            session.get = AsyncMock(return_value=report)
        else:
            session.get = AsyncMock(return_value=None)

        # begin_nested context manager
        nested = AsyncMock()
        nested.__aenter__ = AsyncMock(return_value=nested)
        nested.__aexit__ = AsyncMock(return_value=False)
        session.begin_nested = MagicMock(return_value=nested)

        return session

    @pytest.mark.asyncio
    async def test_skips_already_done_reports(self):
        """Reports with pipeline_status='done' are returned as 'skipped'."""
        session = self._make_session(pipeline_status="done")

        result = await _process_record(session, 1, _VALID_TOOL_INPUT, apply=True)

        assert result == "skipped"
        # save_analysis never called
        session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_save(self):
        """Dry-run returns 'imported' without touching DB."""
        session = self._make_session(pipeline_status="analysis_pending")

        with patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis") as mock_save:

            mock_layer2 = MagicMock()
            mock_layer2.meta = {}
            mock_make.return_value = mock_layer2

            result = await _process_record(session, 1, _VALID_TOOL_INPUT, apply=False)

        assert result == "imported"
        # Dry-run: session.get and save_analysis never called
        session.get.assert_not_called()
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_calls_make_layer2_result(self):
        """Dry-run still validates via make_layer2_result."""
        session = self._make_session(pipeline_status="analysis_pending")

        with patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis"):

            mock_layer2 = MagicMock()
            mock_layer2.meta = {}
            mock_make.return_value = mock_layer2

            await _process_record(session, 1, _VALID_TOOL_INPUT, apply=False)

        mock_make.assert_called_once_with(
            tool_input=_VALID_TOOL_INPUT,
            input_tokens=0,
            output_tokens=0,
            is_batch=False,
            report_id=1,
        )

    @pytest.mark.asyncio
    async def test_apply_calls_save_analysis(self):
        """Apply mode calls save_analysis and returns 'imported'."""
        session = self._make_session(pipeline_status="analysis_pending")

        with patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis") as mock_save, \
             patch("scripts.import_layer2.apply_layer2_meta") as mock_meta:

            mock_layer2 = MagicMock()
            mock_layer2.meta = {"broker": "테스트증권"}
            mock_make.return_value = mock_layer2
            mock_meta.return_value = {"broker": "테스트증권"}

            result = await _process_record(session, 1, _VALID_TOOL_INPUT, apply=True)

        assert result == "imported"
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_failed_when_make_layer2_result_returns_none(self):
        """If make_layer2_result returns None (validation failed), return 'failed'."""
        session = self._make_session(pipeline_status="analysis_pending")

        with patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis") as mock_save:

            mock_make.return_value = None

            result = await _process_record(session, 1, {}, apply=True)

        assert result == "failed"
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_failed_when_report_not_found_in_db(self):
        """If the report doesn't exist in DB, return 'failed'."""
        session = self._make_session(pipeline_status="analysis_pending", report_exists=False)

        with patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis") as mock_save:

            mock_layer2 = MagicMock()
            mock_layer2.meta = {}
            mock_make.return_value = mock_layer2

            result = await _process_record(session, 999, _VALID_TOOL_INPUT, apply=True)

        assert result == "failed"
        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: main() — high-level integration
# ---------------------------------------------------------------------------

class TestMain:

    def _make_args(
        self,
        input_path: str,
        apply: bool = False,
        batch_size: int = 50,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            input=input_path,
            apply=apply,
            batch_size=batch_size,
        )

    def _mock_session_ctx(self, pipeline_status: str = "analysis_pending"):
        """Return a mock AsyncSessionLocal context manager."""
        session = AsyncMock()

        scalar_result = MagicMock()
        scalar_result.scalar.return_value = pipeline_status
        session.execute = AsyncMock(return_value=scalar_result)

        report = MagicMock()
        report.id = 1
        session.get = AsyncMock(return_value=report)

        nested = AsyncMock()
        nested.__aenter__ = AsyncMock(return_value=nested)
        nested.__aexit__ = AsyncMock(return_value=False)
        session.begin_nested = MagicMock(return_value=nested)

        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    @pytest.mark.asyncio
    async def test_failed_status_records_are_skipped(self, tmp_path):
        """Records with status!='success' are never processed."""
        records = [
            _make_failed_record(1),
            _make_error_record(2),
            _make_success_record(3),
        ]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        session = self._mock_session_ctx("analysis_pending")

        with patch("scripts.import_layer2.AsyncSessionLocal", return_value=session), \
             patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis"), \
             patch("scripts.import_layer2.apply_layer2_meta", return_value={}):

            mock_layer2 = MagicMock()
            mock_layer2.meta = {}
            mock_make.return_value = mock_layer2

            args = self._make_args(str(path), apply=False)
            await main(args)

        # Only 1 eligible record (report_id=3)
        assert mock_make.call_count == 1
        call_kwargs = mock_make.call_args[1]
        assert call_kwargs["report_id"] == 3

    @pytest.mark.asyncio
    async def test_dry_run_no_db_writes(self, tmp_path):
        """Dry-run: save_analysis and session.commit never called."""
        records = [_make_success_record(1), _make_success_record(2)]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        session = self._mock_session_ctx("analysis_pending")

        with patch("scripts.import_layer2.AsyncSessionLocal", return_value=session), \
             patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis") as mock_save:

            mock_layer2 = MagicMock()
            mock_layer2.meta = {}
            mock_make.return_value = mock_layer2

            args = self._make_args(str(path), apply=False)
            await main(args)

        mock_save.assert_not_called()
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_done_reports_skipped(self, tmp_path):
        """Reports with pipeline_status='done' are skipped in apply mode."""
        records = [_make_success_record(1), _make_success_record(2)]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        # Both reports are already done
        session = self._mock_session_ctx("done")

        with patch("scripts.import_layer2.AsyncSessionLocal", return_value=session), \
             patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis") as mock_save:

            args = self._make_args(str(path), apply=True, batch_size=50)
            await main(args)

        # No processing — both already done
        mock_make.assert_not_called()
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_commit_at_batch_size(self, tmp_path):
        """Commit is called after every batch_size imports."""
        n = 6
        records = [_make_success_record(i) for i in range(1, n + 1)]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        session = self._mock_session_ctx("analysis_pending")

        with patch("scripts.import_layer2.AsyncSessionLocal", return_value=session), \
             patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis"), \
             patch("scripts.import_layer2.apply_layer2_meta", return_value={}):

            mock_layer2 = MagicMock()
            mock_layer2.meta = {}
            mock_make.return_value = mock_layer2

            # batch_size=3 → 6 records → 2 commits (at 3 and at the end)
            args = self._make_args(str(path), apply=True, batch_size=3)
            await main(args)

        # 2 commits: after batch 1 (count=3) and after batch 2 (remainder)
        assert session.commit.call_count == 2

    @pytest.mark.asyncio
    async def test_result_null_records_skipped(self, tmp_path):
        """Records with status='success' but result=null are not eligible."""
        records = [
            {"report_id": 1, "status": "success", "result": None},
            _make_success_record(2),
        ]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        session = self._mock_session_ctx("analysis_pending")

        with patch("scripts.import_layer2.AsyncSessionLocal", return_value=session), \
             patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis"), \
             patch("scripts.import_layer2.apply_layer2_meta", return_value={}):

            mock_layer2 = MagicMock()
            mock_layer2.meta = {}
            mock_make.return_value = mock_layer2

            args = self._make_args(str(path), apply=False)
            await main(args)

        # Only report_id=2 is processed
        assert mock_make.call_count == 1
        call_kwargs = mock_make.call_args[1]
        assert call_kwargs["report_id"] == 2

    @pytest.mark.asyncio
    async def test_missing_report_id_counted_as_failed(self, tmp_path):
        """Records without report_id are counted as failed."""
        records = [
            {"status": "success", "result": _VALID_TOOL_INPUT},  # no report_id
            _make_success_record(2),
        ]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        session = self._mock_session_ctx("analysis_pending")

        with patch("scripts.import_layer2.AsyncSessionLocal", return_value=session), \
             patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis"), \
             patch("scripts.import_layer2.apply_layer2_meta", return_value={}):

            mock_layer2 = MagicMock()
            mock_layer2.meta = {}
            mock_make.return_value = mock_layer2

            args = self._make_args(str(path), apply=False)
            await main(args)

        # Only report_id=2 is processed (id-less record counted as failed)
        assert mock_make.call_count == 1

    @pytest.mark.asyncio
    async def test_file_not_found_exits_gracefully(self, capsys):
        """Non-existent input file prints error and returns."""
        args = argparse.Namespace(
            input="/nonexistent/path/file.jsonl",
            apply=False,
            batch_size=50,
        )
        # Should not raise
        await main(args)

        captured = capsys.readouterr()
        assert "ERROR" in captured.out or "not found" in captured.out.lower()

    @pytest.mark.asyncio
    async def test_no_eligible_records_exits_early(self, tmp_path):
        """All failed/error records — no processing attempted."""
        records = [_make_failed_record(1), _make_error_record(2)]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        with patch("scripts.import_layer2.AsyncSessionLocal") as mock_session_cls, \
             patch("scripts.import_layer2.make_layer2_result") as mock_make:

            args = self._make_args(str(path), apply=True)
            await main(args)

        # No DB session opened (returned early)
        mock_session_cls.assert_not_called()
        mock_make.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_meta_updates_called(self, tmp_path):
        """apply_layer2_meta is called with report and layer2.meta in apply mode."""
        records = [_make_success_record(10)]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        session = self._mock_session_ctx("analysis_pending")

        with patch("scripts.import_layer2.AsyncSessionLocal", return_value=session), \
             patch("scripts.import_layer2.make_layer2_result") as mock_make, \
             patch("scripts.import_layer2.save_analysis"), \
             patch("scripts.import_layer2.apply_layer2_meta") as mock_meta:

            mock_layer2 = MagicMock()
            mock_layer2.meta = {"broker": "테스트증권"}
            mock_make.return_value = mock_layer2
            mock_meta.return_value = {}  # no updates

            args = self._make_args(str(path), apply=True)
            await main(args)

        mock_meta.assert_called_once()
        call_args = mock_meta.call_args[0]
        assert call_args[1] == {"broker": "테스트증권"}


# ---------------------------------------------------------------------------
# Fix 1: double-count n_failed regression test
# ---------------------------------------------------------------------------

class TestNFailedNotDoubleCount:
    """Regression: when _process_record raises, n_failed must be incremented
    exactly once (not twice — once in except block AND once via else branch)."""

    def _mock_session_ctx(self, pipeline_status: str = "analysis_pending"):
        session = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = pipeline_status
        session.execute = AsyncMock(return_value=scalar_result)
        report = MagicMock()
        report.id = 1
        session.get = AsyncMock(return_value=report)
        nested = AsyncMock()
        nested.__aenter__ = AsyncMock(return_value=nested)
        nested.__aexit__ = AsyncMock(return_value=False)
        session.begin_nested = MagicMock(return_value=nested)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    @pytest.mark.asyncio
    async def test_exception_in_process_record_counts_once(self, tmp_path, capsys):
        """If _process_record raises, n_failed is incremented exactly once."""
        records = [_make_success_record(1), _make_success_record(2)]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        session = self._mock_session_ctx("analysis_pending")

        call_count = 0

        async def _raise_on_first(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated DB error")
            return "imported"

        with patch("scripts.import_layer2.AsyncSessionLocal", return_value=session), \
             patch("scripts.import_layer2._process_record", side_effect=_raise_on_first), \
             patch("scripts.import_layer2.save_analysis"):

            args = argparse.Namespace(input=str(path), apply=False, batch_size=50)
            await main(args)

        captured = capsys.readouterr()
        # Summary should show Failed: 1 (not 2)
        assert "Failed: 1" in captured.out or "Would fail: 1" in captured.out

    @pytest.mark.asyncio
    async def test_exception_n_failed_exact_count(self, tmp_path, capsys):
        """Three records, two raise exceptions — n_failed must be 2, n_imported must be 1."""
        records = [_make_success_record(i) for i in range(1, 4)]
        path = str(tmp_path / "test.jsonl")
        _write_jsonl(records, path)

        session = self._mock_session_ctx("analysis_pending")

        call_num = 0

        async def _raise_on_first_two(*args, **kwargs):
            nonlocal call_num
            call_num += 1
            if call_num <= 2:
                raise RuntimeError("simulated error")
            return "imported"

        with patch("scripts.import_layer2.AsyncSessionLocal", return_value=session), \
             patch("scripts.import_layer2._process_record", side_effect=_raise_on_first_two):

            args = argparse.Namespace(input=str(path), apply=False, batch_size=50)
            await main(args)

        captured = capsys.readouterr()
        # 2 failures and 1 import — verify summary counts are correct
        assert "Would fail: 2" in captured.out
        assert "Would import: 1" in captured.out
