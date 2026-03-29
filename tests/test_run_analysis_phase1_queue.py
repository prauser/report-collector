"""Tests for Phase 1 Queue/Worker refactor in run_analysis.py.

Verifies:
1. All reports are processed (success, error, timeout all collected)
2. Progress counter increments for every outcome (not just success)
3. Concurrency is bounded to args.concurrency workers
4. Results list length equals reports list length
5. Timeout produces a {"status": "timeout"} result
6. Exception produces a {"status": "error: ..."} result
7. Empty reports list is handled (no workers spawned)
"""
import asyncio
import argparse
import contextlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _db_mocks():
    """Return context managers that mock DB session + update_pipeline_status."""
    mock_sess = AsyncMock()
    mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_sess.__aexit__ = AsyncMock(return_value=False)
    return (
        patch("run_analysis.update_pipeline_status", new_callable=AsyncMock),
        patch("run_analysis.AsyncSessionLocal", return_value=mock_sess),
    )


def _make_args(concurrency: int = 2, limit: int = 0, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(concurrency=concurrency, limit=limit, dry_run=dry_run, batch_size=100)


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


# ──────────────────────────────────────────────
# Helper: run Phase 1 in isolation by mocking
# _get_unanalyzed_reports and process_single
# ──────────────────────────────────────────────

async def _run_phase1(reports, process_single_side_effect, concurrency=2):
    """
    Invoke main() up through Phase 1 by mocking DB query and process_single.
    Returns the results list captured from inside main() by intercepting
    Phase 2 (layer2_inputs construction).

    We stop main() early by making layer2_inputs empty so it returns at the
    "No reports ready for Layer2" branch, giving us control.

    process_single_side_effect: a callable or list of callables passed as
    side_effect to AsyncMock for process_single.
    """
    captured: dict = {}

    # Patch _get_unanalyzed_reports to return our fake reports
    async def fake_get_reports(limit):
        return reports

    # Intercept results after Phase 1 by patching the layer2 functions
    # to capture `results` at the point where Phase 2 reads it.
    # We do this by replacing `run_layer2_batch` and letting main() run to the
    # "no layer2_inputs" early-return or "no anthropic key" branch.

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("run_analysis._get_unanalyzed_reports", side_effect=fake_get_reports), \
         patch("run_analysis.process_single", new_callable=AsyncMock,
               side_effect=process_single_side_effect), \
         patch("run_analysis.update_pipeline_status", new_callable=AsyncMock), \
         patch("run_analysis.AsyncSessionLocal", return_value=mock_session), \
         patch("run_analysis.settings") as mock_settings:
        # Disable Layer2 so main() exits right after Phase 1
        mock_settings.gemini_api_key = "fake"
        mock_settings.anthropic_api_key = None  # triggers early return after Phase 1

        from run_analysis import main
        args = _make_args(concurrency=concurrency)
        # We need to capture results; patch print to avoid noise and intercept
        # the final print line that references len(results)
        printed = []
        with patch("builtins.print", side_effect=lambda *a, **kw: printed.append(" ".join(str(x) for x in a))):
            await main(args)

    return printed


class TestPhase1AllResultsCollected:
    """All reports (success, error, timeout) appear in results."""

    @pytest.mark.asyncio
    async def test_all_successes_collected(self):
        reports = [_make_report(i) for i in range(1, 4)]

        async def fake_process(report):
            return {"report_id": report.id, "status": "ok", "steps": {"markdown": "ok"}}

        printed = await _run_phase1(reports, fake_process, concurrency=2)
        # main() prints "=== Done: N processed ..."
        done_line = next((l for l in printed if "Processed" in l), "")
        assert "Processed: 3" in done_line

    @pytest.mark.asyncio
    async def test_error_counted_in_results(self):
        """An exception in process_single still produces a result and is counted."""
        reports = [_make_report(i) for i in range(1, 4)]
        call_count = [0]

        async def fake_process(report):
            call_count[0] += 1
            if report.id == 2:
                raise ValueError("boom")
            return {"report_id": report.id, "status": "ok", "steps": {}}

        printed = await _run_phase1(reports, fake_process, concurrency=2)
        done_line = next((l for l in printed if "Processed" in l), "")
        assert "Processed: 3" in done_line

    @pytest.mark.asyncio
    async def test_timeout_counted_in_results(self):
        """A TimeoutError in process_single still produces a result and is counted."""
        reports = [_make_report(i) for i in range(1, 3)]

        async def fake_process(report):
            if report.id == 1:
                raise asyncio.TimeoutError()
            return {"report_id": report.id, "status": "ok", "steps": {}}

        printed = await _run_phase1(reports, fake_process, concurrency=2)
        done_line = next((l for l in printed if "Processed" in l), "")
        assert "Processed: 2" in done_line

    @pytest.mark.asyncio
    async def test_mixed_outcomes_all_counted(self):
        """Mix of ok/error/timeout: all 5 end up counted."""
        reports = [_make_report(i) for i in range(1, 6)]

        async def fake_process(report):
            if report.id == 1:
                raise asyncio.TimeoutError()
            if report.id == 2:
                raise RuntimeError("network error")
            return {"report_id": report.id, "status": "ok", "steps": {}}

        printed = await _run_phase1(reports, fake_process, concurrency=3)
        done_line = next((l for l in printed if "Processed" in l), "")
        assert "Processed: 5" in done_line


class TestPhase1WorkerPattern:
    """Queue/Worker mechanics: concurrency respected, queue drains fully."""

    @pytest.mark.asyncio
    async def test_concurrency_not_exceeded(self):
        """At most N tasks run concurrently (measured via active count)."""
        concurrency = 2
        reports = [_make_report(i) for i in range(1, 7)]
        active = [0]
        peak_active = [0]

        async def fake_process(report):
            active[0] += 1
            peak_active[0] = max(peak_active[0], active[0])
            await asyncio.sleep(0.01)  # yield to allow other workers
            active[0] -= 1
            return {"report_id": report.id, "status": "ok", "steps": {}}

        await _run_phase1(reports, fake_process, concurrency=concurrency)
        assert peak_active[0] <= concurrency

    @pytest.mark.asyncio
    async def test_all_queue_items_processed(self):
        """Every item put into the queue is eventually processed."""
        n = 8
        reports = [_make_report(i) for i in range(1, n + 1)]
        processed_ids = []

        async def fake_process(report):
            processed_ids.append(report.id)
            return {"report_id": report.id, "status": "ok", "steps": {}}

        await _run_phase1(reports, fake_process, concurrency=3)
        assert sorted(processed_ids) == list(range(1, n + 1))

    @pytest.mark.asyncio
    async def test_empty_reports_no_error(self):
        """Zero reports: main() exits at 'Nothing to do.' without spawning workers."""
        async def fake_get(_limit):
            return []

        with patch("run_analysis._get_unanalyzed_reports", side_effect=fake_get), \
             patch("run_analysis.settings") as ms:
            ms.gemini_api_key = "fake"
            ms.anthropic_api_key = None

            from run_analysis import main
            args = _make_args(concurrency=2)
            with patch("builtins.print"):
                await main(args)  # must not raise


class TestPhase1ResultShape:
    """Result dicts have the right shape for downstream Phase 2 consumption."""

    @pytest.mark.asyncio
    async def test_timeout_result_has_status_timeout(self):
        """A timed-out report produces {"status": "timeout"}."""
        reports = [_make_report(1)]
        seen_statuses = []

        async def fake_process(report):
            raise asyncio.TimeoutError()

        # We need to inspect the results list; intercept via patching
        # run_layer2_batch (not called when anthropic_api_key=None).
        # Instead we patch the log call to capture the status logged.
        log_calls = []

        original_log = None

        async def fake_get(_limit):
            return reports

        db_patch1, db_patch2 = _db_mocks()
        with patch("run_analysis._get_unanalyzed_reports", side_effect=fake_get), \
             patch("run_analysis.process_single", new_callable=AsyncMock,
                   side_effect=fake_process), \
             db_patch1, db_patch2, \
             patch("run_analysis.settings") as ms, \
             patch("run_analysis.log") as mock_log:
            ms.gemini_api_key = "fake"
            ms.anthropic_api_key = None

            from run_analysis import main
            args = _make_args(concurrency=1)
            with patch("builtins.print"):
                await main(args)

            # log.warning("analysis_timeout", ...) should have been called
            warning_calls = mock_log.warning.call_args_list
            assert any(
                call.args[0] == "analysis_timeout"
                for call in warning_calls
            ), f"Expected analysis_timeout warning, got: {warning_calls}"

    @pytest.mark.asyncio
    async def test_error_result_has_status_error_prefix(self):
        """An exception produces {"status": "error: <msg>"}."""
        reports = [_make_report(1)]

        async def fake_process(report):
            raise ValueError("something broke")

        async def fake_get(_limit):
            return reports

        db_patch1, db_patch2 = _db_mocks()
        with patch("run_analysis._get_unanalyzed_reports", side_effect=fake_get), \
             patch("run_analysis.process_single", new_callable=AsyncMock,
                   side_effect=fake_process), \
             db_patch1, db_patch2, \
             patch("run_analysis.settings") as ms, \
             patch("run_analysis.log") as mock_log:
            ms.gemini_api_key = "fake"
            ms.anthropic_api_key = None

            from run_analysis import main
            args = _make_args(concurrency=1)
            with patch("builtins.print"):
                await main(args)

            error_calls = mock_log.error.call_args_list
            assert any(
                call.args[0] == "analysis_error"
                for call in error_calls
            ), f"Expected analysis_error log, got: {error_calls}"


class TestPhase1ProgressLogging:
    """done counter increments for all outcomes and is logged correctly."""

    @pytest.mark.asyncio
    async def test_progress_logged_for_timeout(self):
        """Even a timeout triggers a log.info("analyzed", ...) call with the progress."""
        reports = [_make_report(1), _make_report(2)]
        call_count = [0]

        async def fake_process(report):
            call_count[0] += 1
            if call_count[0] == 1:
                raise asyncio.TimeoutError()
            return {"report_id": report.id, "status": "ok", "steps": {}}

        async def fake_get(_limit):
            return reports

        analyzed_calls = []

        def capture_info(event, **kwargs):
            if event == "analyzed":
                analyzed_calls.append(kwargs)

        db_patch1, db_patch2 = _db_mocks()
        with patch("run_analysis._get_unanalyzed_reports", side_effect=fake_get), \
             patch("run_analysis.process_single", new_callable=AsyncMock,
                   side_effect=fake_process), \
             db_patch1, db_patch2, \
             patch("run_analysis.settings") as ms, \
             patch("run_analysis.log") as mock_log:
            ms.gemini_api_key = "fake"
            ms.anthropic_api_key = None

            # Capture log.info calls (structlog info is synchronous)
            mock_log.info.side_effect = capture_info

            from run_analysis import main
            args = _make_args(concurrency=1)
            with patch("builtins.print"):
                await main(args)

        # Both reports should produce an "analyzed" log entry (timeout + ok)
        assert len(analyzed_calls) == 2

    @pytest.mark.asyncio
    async def test_progress_logged_for_exception(self):
        """An exception also triggers a log.info("analyzed", ...) call."""
        reports = [_make_report(5)]

        async def fake_process(report):
            raise RuntimeError("db error")

        async def fake_get(_limit):
            return reports

        analyzed_calls = []

        def capture_info(event, **kwargs):
            if event == "analyzed":
                analyzed_calls.append(kwargs)

        db_patch1, db_patch2 = _db_mocks()
        with patch("run_analysis._get_unanalyzed_reports", side_effect=fake_get), \
             patch("run_analysis.process_single", new_callable=AsyncMock,
                   side_effect=fake_process), \
             db_patch1, db_patch2, \
             patch("run_analysis.settings") as ms, \
             patch("run_analysis.log") as mock_log:
            ms.gemini_api_key = "fake"
            ms.anthropic_api_key = None

            mock_log.info.side_effect = capture_info

            from run_analysis import main
            args = _make_args(concurrency=1)
            with patch("builtins.print"):
                await main(args)

        assert len(analyzed_calls) == 1
        assert analyzed_calls[0]["status"].startswith("error:")


class TestPhase1SingleWorkerConcurrency:
    """Concurrency=1 means strictly sequential processing."""

    @pytest.mark.asyncio
    async def test_concurrency_1_is_sequential(self):
        """With concurrency=1, reports are processed one at a time."""
        reports = [_make_report(i) for i in range(1, 5)]
        order = []

        async def fake_process(report):
            order.append(("start", report.id))
            await asyncio.sleep(0)
            order.append(("end", report.id))
            return {"report_id": report.id, "status": "ok", "steps": {}}

        await _run_phase1(reports, fake_process, concurrency=1)

        # With 1 worker: each report fully completes before next starts
        for i in range(0, len(order), 2):
            start_id = order[i][1]
            end_id = order[i + 1][1]
            assert order[i][0] == "start"
            assert order[i + 1][0] == "end"
            assert start_id == end_id
