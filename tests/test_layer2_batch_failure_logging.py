"""Tests for Layer2 batch failure file logging in run_analysis.py.

Verifies:
1. When run_layer2_batch raises, a line is written to logs/layer2_batch_failures.log
2. The log line contains: timestamp, batch_num, report_ids, error message
3. pipeline_status is NOT changed (remains analysis_pending)
4. Return value is 0 on failure
5. logs/ directory is created if it does not exist
6. Multiple failures are appended (not overwritten)
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_layer2_inputs(*report_ids: int) -> dict:
    """Build a minimal layer2_inputs dict for the given report_ids."""
    inputs = {}
    for rid in report_ids:
        cid = f"report-{rid}"
        inputs[cid] = {
            "report_id": rid,
            "layer2_input": {
                "user_content": [{"type": "text", "text": f"content-{rid}"}],
                "md_truncated": False,
                "md_chars": 100,
                "channel": "test",
            },
        }
    return inputs


def _reset_batch_failure_logger():
    """Remove all handlers from the batch failure logger so tests start clean."""
    logger = logging.getLogger("layer2_batch_failures")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


# ---------------------------------------------------------------------------
# Tests for _submit_and_save_batch failure logging
# ---------------------------------------------------------------------------

class TestBatchFailureFileLogging:

    @pytest.mark.asyncio
    async def test_failure_writes_to_log_file(self, tmp_path):
        """On run_layer2_batch exception, a line is appended to the failure log."""
        log_file = tmp_path / "layer2_batch_failures.log"
        _reset_batch_failure_logger()

        with patch("run_analysis._BATCH_FAILURE_LOG_PATH", log_file), \
             patch("run_analysis.submit_layer2_batch", AsyncMock(side_effect=RuntimeError("API down"))), \
             patch("run_analysis.log"):
            from run_analysis import _submit_and_save_batch
            result = await _submit_and_save_batch(_make_layer2_inputs(1, 2), batch_num=1)

        assert result is None
        assert log_file.exists(), "Log file should have been created"
        content = log_file.read_text(encoding="utf-8")
        assert "API down" in content

    @pytest.mark.asyncio
    async def test_log_line_contains_batch_num(self, tmp_path):
        """The log line includes the batch attempt number."""
        log_file = tmp_path / "layer2_batch_failures.log"
        _reset_batch_failure_logger()

        with patch("run_analysis._BATCH_FAILURE_LOG_PATH", log_file), \
             patch("run_analysis.submit_layer2_batch", AsyncMock(side_effect=ValueError("timeout"))), \
             patch("run_analysis.log"):
            from run_analysis import _submit_and_save_batch
            await _submit_and_save_batch(_make_layer2_inputs(10), batch_num=7)

        content = log_file.read_text(encoding="utf-8")
        assert "batch_attempt=7" in content

    @pytest.mark.asyncio
    async def test_log_line_contains_report_ids(self, tmp_path):
        """The log line contains the list of report_ids in the failed batch."""
        log_file = tmp_path / "layer2_batch_failures.log"
        _reset_batch_failure_logger()

        with patch("run_analysis._BATCH_FAILURE_LOG_PATH", log_file), \
             patch("run_analysis.submit_layer2_batch", AsyncMock(side_effect=RuntimeError("err"))), \
             patch("run_analysis.log"):
            from run_analysis import _submit_and_save_batch
            await _submit_and_save_batch(_make_layer2_inputs(42, 99), batch_num=3)

        content = log_file.read_text(encoding="utf-8")
        assert "42" in content
        assert "99" in content

    @pytest.mark.asyncio
    async def test_log_line_contains_timestamp(self, tmp_path):
        """The log line contains an ISO-format UTC timestamp."""
        log_file = tmp_path / "layer2_batch_failures.log"
        _reset_batch_failure_logger()

        with patch("run_analysis._BATCH_FAILURE_LOG_PATH", log_file), \
             patch("run_analysis.submit_layer2_batch", AsyncMock(side_effect=RuntimeError("err"))), \
             patch("run_analysis.log"):
            from run_analysis import _submit_and_save_batch
            await _submit_and_save_batch(_make_layer2_inputs(5), batch_num=1)

        content = log_file.read_text(encoding="utf-8")
        # Timestamp looks like 2026-04-05T12:34:56Z
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", content), \
            f"No UTC timestamp found in log: {content!r}"

    @pytest.mark.asyncio
    async def test_multiple_failures_appended(self, tmp_path):
        """Multiple batch failures are appended to the same file (not overwritten)."""
        log_file = tmp_path / "layer2_batch_failures.log"
        _reset_batch_failure_logger()

        with patch("run_analysis._BATCH_FAILURE_LOG_PATH", log_file), \
             patch("run_analysis.submit_layer2_batch", AsyncMock(side_effect=RuntimeError("err"))), \
             patch("run_analysis.log"):
            from run_analysis import _submit_and_save_batch
            await _submit_and_save_batch(_make_layer2_inputs(1), batch_num=1)
            await _submit_and_save_batch(_make_layer2_inputs(2), batch_num=2)

        lines = [l for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) >= 2, f"Expected at least 2 log lines, got: {lines}"

    @pytest.mark.asyncio
    async def test_logs_dir_created_if_missing(self, tmp_path):
        """The logs/ directory is created automatically if it does not exist."""
        log_file = tmp_path / "nested" / "layer2_batch_failures.log"
        assert not log_file.parent.exists()
        _reset_batch_failure_logger()

        with patch("run_analysis._BATCH_FAILURE_LOG_PATH", log_file), \
             patch("run_analysis.submit_layer2_batch", AsyncMock(side_effect=RuntimeError("err"))), \
             patch("run_analysis.log"):
            from run_analysis import _submit_and_save_batch
            await _submit_and_save_batch(_make_layer2_inputs(1), batch_num=1)

        assert log_file.parent.exists()
        assert log_file.exists()

    @pytest.mark.asyncio
    async def test_pipeline_status_not_changed_on_failure(self, tmp_path):
        """When batch submission fails, pipeline_status is NOT updated."""
        log_file = tmp_path / "layer2_batch_failures.log"
        _reset_batch_failure_logger()

        mock_update = AsyncMock()
        with patch("run_analysis._BATCH_FAILURE_LOG_PATH", log_file), \
             patch("run_analysis.submit_layer2_batch", AsyncMock(side_effect=RuntimeError("err"))), \
             patch("run_analysis.update_pipeline_status", mock_update), \
             patch("run_analysis.log"):
            from run_analysis import _submit_and_save_batch
            await _submit_and_save_batch(_make_layer2_inputs(10, 20), batch_num=2)

        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self, tmp_path):
        """_submit_and_save_batch returns None when submit_layer2_batch raises."""
        log_file = tmp_path / "layer2_batch_failures.log"
        _reset_batch_failure_logger()

        with patch("run_analysis._BATCH_FAILURE_LOG_PATH", log_file), \
             patch("run_analysis.submit_layer2_batch", AsyncMock(side_effect=Exception("bad"))), \
             patch("run_analysis.log"):
            from run_analysis import _submit_and_save_batch
            result = await _submit_and_save_batch(_make_layer2_inputs(1), batch_num=1)

        assert result is None

    @pytest.mark.asyncio
    async def test_no_file_written_on_success(self, tmp_path):
        """When submit_layer2_batch succeeds, no failure log file is written."""
        log_file = tmp_path / "layer2_batch_failures.log"
        _reset_batch_failure_logger()

        with patch("run_analysis._BATCH_FAILURE_LOG_PATH", log_file), \
             patch("run_analysis.submit_layer2_batch", AsyncMock(return_value="msgbatch_ok")), \
             patch("run_analysis.log"):
            from run_analysis import _submit_and_save_batch
            await _submit_and_save_batch(_make_layer2_inputs(1), batch_num=1)

        assert not log_file.exists(), "Failure log should NOT be written on success"
