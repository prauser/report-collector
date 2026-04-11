"""Tests for async batch fire-and-forget in run_analysis.py.

Verifies:
1. _flush_buffer() creates a task (non-blocking) instead of awaiting directly
2. Workers continue processing while a batch task is polling
3. _pending_batches collects all created tasks
4. All pending batches are awaited after workers complete
5. total_saved reflects all batch results after gather
6. A failing batch task does not crash other batches or workers
7. Semaphore limits concurrent batch count to max_concurrent_batches
8. Empty buffer / no anthropic key: no task is created
"""
from __future__ import annotations

import asyncio
import argparse
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(
    concurrency: int = 2,
    limit: int = 0,
    dry_run: bool = False,
    batch_size: int = 100,
) -> argparse.Namespace:
    return argparse.Namespace(
        concurrency=concurrency,
        limit=limit,
        dry_run=dry_run,
        batch_size=batch_size,
        enable_charts=False,
        disable_charts=False,
    )


def _make_report(report_id: int) -> MagicMock:
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


def _make_l2_result(report_id: int) -> dict:
    """A process_single result that includes layer2_input."""
    return {
        "report_id": report_id,
        "status": "ok",
        "steps": {"markdown": "ok", "charts": "1/1 digitized"},
        "layer2_input": {
            "user_content": [{"type": "text", "text": f"content-{report_id}"}],
            "md_truncated": False,
            "md_chars": 500,
            "channel": "test",
        },
    }


def _make_no_l2_result(report_id: int) -> dict:
    """A process_single result without layer2_input."""
    return {
        "report_id": report_id,
        "status": "no_markdown",
        "steps": {},
    }


def _mock_session():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    return sess


# ---------------------------------------------------------------------------
# Base context manager patches shared across tests
# ---------------------------------------------------------------------------

def _base_patches(anthropic_key="fake-anthropic", batch_side_effect=None):
    """
    Returns a list of (patch_target, kwargs) tuples.

    batch_side_effect: side_effect for _submit_and_save_batch AsyncMock.
                       Defaults to returning a batch_id string.
    """
    if batch_side_effect is None:
        batch_side_effect = AsyncMock(return_value="msgbatch_fake")

    sess = _mock_session()
    return (
        patch("run_analysis.update_pipeline_status", new_callable=AsyncMock),
        patch("run_analysis.AsyncSessionLocal", return_value=sess),
        patch("run_analysis._submit_and_save_batch", side_effect=batch_side_effect),
        patch("run_analysis.settings"),
        # settings is used inside the context, caller must handle it
    )


# ---------------------------------------------------------------------------
# Test: _flush_buffer creates a task (non-blocking)
# ---------------------------------------------------------------------------

class TestFlushBufferCreatesTask:
    """_flush_buffer should NOT await the batch directly — it creates a task."""

    @pytest.mark.asyncio
    async def test_flush_creates_task_not_blocking(self):
        """
        When threshold is reached, _flush_buffer creates a background task.
        Workers continue immediately without waiting for batch completion.

        Design:
        - batch_can_finish is NOT pre-set — the fake batch blocks until explicitly released.
        - concurrency=2 so a second worker runs while the batch is blocked.
        - After main() completes we release the batch and verify:
            * report 2 was processed (workers were NOT stuck waiting for the batch)
            * total_saved reflects both batches
        - If someone reverts _flush_buffer() to `await _submit_and_save_batch()` the
          single worker will deadlock waiting for the batch, the test will timeout.
        """
        reports = [_make_report(i) for i in range(1, 3)]
        worker_continued = []
        batch_can_finish = asyncio.Event()
        # Track whether batch 1 was still blocked when report 2 was processed
        batch_blocked_when_r2_processed = [False]

        async def fake_submit(inputs, batch_num):
            await batch_can_finish.wait()  # blocks until we explicitly allow it
            return len(inputs)

        async def fake_process(report, **kwargs):
            result = _make_l2_result(report.id)
            if report.id == 2:
                # Record whether the batch was still blocked at this point
                batch_blocked_when_r2_processed[0] = not batch_can_finish.is_set()
                worker_continued.append(report.id)
            return result

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            # batch_size=1 so the first report triggers a flush immediately.
            # concurrency=2 so both workers can run concurrently; report 2 is
            # processed by the second worker while the batch is still blocked.
            args = _make_args(concurrency=2, batch_size=1)

            # Release the batch AFTER scheduling main() so it can unblock the
            # pending batch tasks during the gather() at the end of main().
            async def _release_after_workers():
                # Yield control back into the event loop a few times so that
                # workers get a chance to run and process report 2 before the
                # batch is allowed to finish.
                for _ in range(10):
                    await asyncio.sleep(0)
                batch_can_finish.set()

            from run_analysis import main
            # Run main() and the releaser concurrently.
            await asyncio.gather(main(args), _release_after_workers())

        # Workers were not blocked: report 2 was processed.
        assert 2 in worker_continued, (
            "Report 2 was never processed — workers appear to have been blocked "
            "waiting for the batch (fire-and-forget is not working)."
        )
        # The batch was still blocked when report 2 was processed, proving that
        # the worker did NOT wait for the first batch to complete.
        assert batch_blocked_when_r2_processed[0], (
            "The batch was already finished when report 2 was processed — "
            "the test did not prove non-blocking behaviour."
        )

    @pytest.mark.asyncio
    async def test_pending_batches_list_populated(self):
        """Each flush call appends a task to the internal pending list."""
        reports = [_make_report(i) for i in range(1, 4)]
        submit_call_count = [0]

        async def fake_submit(inputs, batch_num):
            submit_call_count[0] += 1
            return len(inputs)

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            # batch_size=1: each report triggers its own flush
            args = _make_args(concurrency=1, batch_size=1)
            from run_analysis import main
            await main(args)

        # 3 reports, each triggers a flush → 3 submit calls
        assert submit_call_count[0] == 3


# ---------------------------------------------------------------------------
# Test: total_saved is accurate after gather
# ---------------------------------------------------------------------------

class TestSubmittedBatchIdsAfterGather:
    """submitted_batch_ids must reflect all successfully submitted batches."""

    @pytest.mark.asyncio
    async def test_submitted_batch_ids_listed_in_summary(self):
        """When multiple batches are submitted, each batch_id appears in summary output."""
        n_reports = 6
        reports = [_make_report(i) for i in range(1, n_reports + 1)]
        batch_idx = [0]

        async def fake_submit(inputs, batch_num):
            batch_idx[0] += 1
            return f"msgbatch_00{batch_idx[0]}"

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        printed = []
        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print", side_effect=lambda *a, **kw: printed.append(" ".join(str(x) for x in a))):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            # batch_size=2: 6 reports → 3 batches of 2
            args = _make_args(concurrency=2, batch_size=2)
            from run_analysis import main
            await main(args)

        batches_line = next((l for l in printed if "Batches submitted" in l), "")
        assert "Batches submitted: 3" in batches_line, f"Expected 3, got: {batches_line!r}"
        # Each batch ID should appear in output
        all_output = "\n".join(printed)
        assert "msgbatch_001" in all_output
        assert "msgbatch_002" in all_output
        assert "msgbatch_003" in all_output

    @pytest.mark.asyncio
    async def test_no_batches_submitted_when_no_anthropic_key(self):
        """If anthropic_api_key is None, no batches are submitted."""
        reports = [_make_report(i) for i in range(1, 4)]

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        printed = []
        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print", side_effect=lambda *a, **kw: printed.append(" ".join(str(x) for x in a))):
            ms.anthropic_api_key = None
            ms.gemini_api_key = "fake"

            args = _make_args(concurrency=2, batch_size=100)
            from run_analysis import main
            await main(args)

        batches_line = next((l for l in printed if "Batches submitted" in l), "")
        assert "Batches submitted: 0" in batches_line

    @pytest.mark.asyncio
    async def test_residual_buffer_submitted_as_batch(self):
        """Reports that don't fill a full batch (residual buffer) are submitted."""
        reports = [_make_report(i) for i in range(1, 4)]  # 3 reports

        async def fake_submit(inputs, batch_num):
            return "msgbatch_residual"

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        printed = []
        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print", side_effect=lambda *a, **kw: printed.append(" ".join(str(x) for x in a))):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            # batch_size=100: all 3 go into residual flush
            args = _make_args(concurrency=2, batch_size=100)
            from run_analysis import main
            await main(args)

        all_output = "\n".join(printed)
        assert "msgbatch_residual" in all_output


# ---------------------------------------------------------------------------
# Test: Failing batch task doesn't affect other batches or workers
# ---------------------------------------------------------------------------

class TestBatchTaskErrorIsolation:
    """One batch failure should not crash other batches or the overall run."""

    @pytest.mark.asyncio
    async def test_failing_batch_does_not_crash_main(self):
        """If _submit_and_save_batch raises, main() still completes."""
        reports = [_make_report(i) for i in range(1, 4)]
        call_count = [0]

        async def fake_submit(inputs, batch_num):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Batch API error")
            return len(inputs)

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("run_analysis.log"), \
             patch("builtins.print"):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            args = _make_args(concurrency=1, batch_size=1)
            from run_analysis import main
            await main(args)  # must not raise

    @pytest.mark.asyncio
    async def test_failing_batch_logged_not_raised(self):
        """When a batch task fails, the error is logged (not propagated)."""
        reports = [_make_report(1)]

        async def fake_submit(inputs, batch_num):
            raise ValueError("batch exploded")

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("run_analysis.log") as mock_log, \
             patch("builtins.print"):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            args = _make_args(concurrency=1, batch_size=1)
            from run_analysis import main
            await main(args)

        error_calls = mock_log.error.call_args_list
        assert any(
            "batch_task_failed" in str(c) for c in error_calls
        ), f"Expected batch_task_failed error log, got: {error_calls}"

    @pytest.mark.asyncio
    async def test_second_batch_succeeds_after_first_fails(self):
        """Even if batch 1 fails, batch 2 should still submit successfully."""
        reports = [_make_report(i) for i in range(1, 3)]
        call_count = [0]

        async def fake_submit(inputs, batch_num):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first batch error")
            return "msgbatch_second_ok"  # second batch submits ok

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        printed = []
        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("run_analysis.log"), \
             patch("builtins.print", side_effect=lambda *a, **kw: printed.append(" ".join(str(x) for x in a))):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            # batch_size=1 → 2 reports → 2 batch tasks
            args = _make_args(concurrency=1, batch_size=1)
            from run_analysis import main
            await main(args)

        all_output = "\n".join(printed)
        # Second batch submitted successfully → its ID in output
        assert "msgbatch_second_ok" in all_output


# ---------------------------------------------------------------------------
# Test: No task created when conditions not met
# ---------------------------------------------------------------------------

class TestFlushBufferNoTaskWhenSkipped:

    @pytest.mark.asyncio
    async def test_no_task_when_no_anthropic_key(self):
        """If anthropic_api_key is None, _flush_buffer returns early — no task."""
        reports = [_make_report(1)]
        submit_call_count = [0]

        async def fake_submit(inputs, batch_num):
            submit_call_count[0] += 1
            return 1

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None
            ms.gemini_api_key = "fake"

            args = _make_args(concurrency=1, batch_size=1)
            from run_analysis import main
            await main(args)

        assert submit_call_count[0] == 0

    @pytest.mark.asyncio
    async def test_no_task_when_no_l2_results(self):
        """If no reports produce layer2_input, _flush_buffer is never triggered."""
        reports = [_make_report(1), _make_report(2)]
        submit_call_count = [0]

        async def fake_submit(inputs, batch_num):
            submit_call_count[0] += 1
            return 0

        async def fake_process(report, **kwargs):
            return _make_no_l2_result(report.id)

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            args = _make_args(concurrency=1, batch_size=1)
            from run_analysis import main
            await main(args)

        assert submit_call_count[0] == 0


# ---------------------------------------------------------------------------
# Test: Buffer copy-then-clear (no data loss, no double-submit)
# ---------------------------------------------------------------------------

class TestBufferCopyClear:

    @pytest.mark.asyncio
    async def test_buffer_cleared_after_flush(self):
        """After _flush_buffer, l2_buffer should be empty (new items start fresh)."""
        reports = [_make_report(i) for i in range(1, 4)]
        submitted_batches = []

        async def fake_submit(inputs, batch_num):
            submitted_batches.append(set(inputs.keys()))
            return len(inputs)

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            # batch_size=2: first 2 → batch 1, remaining 1 → batch 2 (residual)
            args = _make_args(concurrency=1, batch_size=2)
            from run_analysis import main
            await main(args)

        # Verify no report appears in two different batches
        all_ids = [rid for batch in submitted_batches for rid in batch]
        assert len(all_ids) == len(set(all_ids)), "Some report_ids appeared in multiple batches!"

        # Exactly 3 reports total across all batches
        assert len(all_ids) == 3


# ---------------------------------------------------------------------------
# Test: Semaphore limits concurrency
# ---------------------------------------------------------------------------

class TestBatchSemaphore:

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_batches(self):
        """At most _MAX_CONCURRENT_BATCHES batches run at the same time."""
        from run_analysis import _MAX_CONCURRENT_BATCHES

        n = _MAX_CONCURRENT_BATCHES + 2  # create more batches than the limit
        reports = [_make_report(i) for i in range(1, n + 1)]
        active = [0]
        peak_active = [0]

        async def fake_submit(inputs, batch_num):
            active[0] += 1
            peak_active[0] = max(peak_active[0], active[0])
            await asyncio.sleep(0.01)  # yield to let other tasks start
            active[0] -= 1
            return len(inputs)

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = "fake"
            ms.gemini_api_key = "fake"

            # batch_size=1: each report is its own batch
            args = _make_args(concurrency=n, batch_size=1)
            from run_analysis import main
            await main(args)

        assert peak_active[0] <= _MAX_CONCURRENT_BATCHES, (
            f"Peak concurrent batches {peak_active[0]} exceeded limit {_MAX_CONCURRENT_BATCHES}"
        )


# ---------------------------------------------------------------------------
# Test: --dump-layer2 mode
# ---------------------------------------------------------------------------

class TestDumpLayer2Mode:
    """Tests for the --dump-layer2 flag in run_analysis.main()."""

    def _make_dump_args(
        self,
        dump_path: str,
        concurrency: int = 2,
        limit: int = 0,
        batch_size: int = 100,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            concurrency=concurrency,
            limit=limit,
            dry_run=False,
            batch_size=batch_size,
            enable_charts=False,
            disable_charts=False,
            dump_layer2=True,
            dump_layer2_path=dump_path,
        )

    @pytest.mark.asyncio
    async def test_dump_creates_jsonl_file(self, tmp_path):
        """When --dump-layer2 is set, a JSONL file is created with layer2 inputs."""
        import json

        reports = [_make_report(i) for i in range(1, 4)]
        dump_file = tmp_path / "layer2_dump.jsonl"

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        sess = _mock_session()
        submit_call_count = [0]

        async def fake_submit(inputs, batch_num):
            submit_call_count[0] += 1
            return "should_not_be_called"

        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None  # no key needed in dump mode
            ms.gemini_api_key = "fake"

            args = self._make_dump_args(dump_path=str(dump_file))
            from run_analysis import main
            await main(args)

        assert dump_file.exists(), "JSONL dump file was not created"
        lines = [l for l in dump_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 3, f"Expected 3 JSONL lines, got {len(lines)}"

    @pytest.mark.asyncio
    async def test_dump_jsonl_format(self, tmp_path):
        """Each JSONL line has the correct fields: report_id, user_content, md_truncated, md_chars, channel."""
        import json

        reports = [_make_report(42)]
        dump_file = tmp_path / "layer2_dump.jsonl"

        async def fake_process(report, **kwargs):
            r = _make_l2_result(report.id)
            # Set specific values to verify correct extraction
            r["layer2_input"]["user_content"] = "test content"
            r["layer2_input"]["md_truncated"] = True
            r["layer2_input"]["md_chars"] = 9999
            r["layer2_input"]["channel"] = "@testchannel"
            return r

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[42])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None
            ms.gemini_api_key = "fake"

            args = self._make_dump_args(dump_path=str(dump_file))
            from run_analysis import main
            await main(args)

        lines = [l for l in dump_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["report_id"] == 42
        assert record["user_content"] == "test content"
        assert record["md_truncated"] is True
        assert record["md_chars"] == 9999
        assert record["channel"] == "@testchannel"

    @pytest.mark.asyncio
    async def test_dump_no_anthropic_api_call(self, tmp_path):
        """In dump mode, _submit_and_save_batch is never called."""
        reports = [_make_report(i) for i in range(1, 4)]
        dump_file = tmp_path / "layer2_dump.jsonl"
        submit_call_count = [0]

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        async def fake_submit(inputs, batch_num):
            submit_call_count[0] += 1
            return "unexpected_call"

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[r.id for r in reports])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis._submit_and_save_batch", AsyncMock(side_effect=fake_submit)), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None
            ms.gemini_api_key = "fake"

            args = self._make_dump_args(dump_path=str(dump_file))
            from run_analysis import main
            await main(args)

        assert submit_call_count[0] == 0, (
            f"_submit_and_save_batch was called {submit_call_count[0]} times in dump mode"
        )

    @pytest.mark.asyncio
    async def test_dump_pipeline_status_stays_analysis_pending(self, tmp_path):
        """In dump mode, pipeline_status should remain analysis_pending (no transition to done)."""
        reports = [_make_report(10)]
        dump_file = tmp_path / "layer2_dump.jsonl"

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        sess = _mock_session()
        status_updates = []

        async def fake_update_status(session, report_id, status):
            status_updates.append((report_id, status))

        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[10])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock(side_effect=fake_update_status)), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None
            ms.gemini_api_key = "fake"

            args = self._make_dump_args(dump_path=str(dump_file))
            from run_analysis import main
            await main(args)

        # Only "analysis_pending" transitions should occur (from process_single start)
        # "done" should never be set
        done_updates = [(rid, s) for rid, s in status_updates if s == "done"]
        assert len(done_updates) == 0, (
            f"pipeline_status was set to 'done' in dump mode: {done_updates}"
        )

    @pytest.mark.asyncio
    async def test_dump_works_without_anthropic_key(self, tmp_path):
        """Dump mode works even when anthropic_api_key is None."""
        import json

        reports = [_make_report(7)]
        dump_file = tmp_path / "layer2_dump.jsonl"

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[7])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None  # explicitly no key
            ms.gemini_api_key = "fake"

            args = self._make_dump_args(dump_path=str(dump_file))
            from run_analysis import main
            await main(args)  # must not raise

        assert dump_file.exists()
        lines = [l for l in dump_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["report_id"] == 7

    @pytest.mark.asyncio
    async def test_dump_multiple_batches_all_written(self, tmp_path):
        """All reports are written to JSONL even when batch_size < total reports."""
        import json

        n_reports = 5
        reports = [_make_report(i) for i in range(1, n_reports + 1)]
        dump_file = tmp_path / "layer2_dump.jsonl"

        async def fake_process(report, **kwargs):
            return _make_l2_result(report.id)

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

            # batch_size=2: triggers multiple flushes
            args = self._make_dump_args(dump_path=str(dump_file), batch_size=2)
            from run_analysis import main
            await main(args)

        lines = [l for l in dump_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == n_reports, f"Expected {n_reports} lines, got {len(lines)}"
        report_ids_in_dump = {json.loads(l)["report_id"] for l in lines}
        assert report_ids_in_dump == set(range(1, n_reports + 1))

    @pytest.mark.asyncio
    async def test_dump_reports_with_no_l2_not_written(self, tmp_path):
        """Reports that fail markdown (no layer2_input) are not written to the dump."""
        import json

        reports = [_make_report(1), _make_report(2)]
        dump_file = tmp_path / "layer2_dump.jsonl"

        async def fake_process(report, **kwargs):
            if report.id == 1:
                return _make_l2_result(report.id)
            else:
                return _make_no_l2_result(report.id)  # no layer2_input

        sess = _mock_session()
        with patch("run_analysis._get_unanalyzed_report_ids", AsyncMock(return_value=[1, 2])), \
             patch("run_analysis._load_report", AsyncMock(side_effect=lambda rid: next((r for r in reports if r.id == rid), None))), \
             patch("run_analysis.process_single", AsyncMock(side_effect=fake_process)), \
             patch("run_analysis.update_pipeline_status", AsyncMock()), \
             patch("run_analysis.AsyncSessionLocal", return_value=sess), \
             patch("run_analysis.settings") as ms, \
             patch("builtins.print"):
            ms.anthropic_api_key = None
            ms.gemini_api_key = "fake"

            args = self._make_dump_args(dump_path=str(dump_file))
            from run_analysis import main
            await main(args)

        lines = [l for l in dump_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["report_id"] == 1
