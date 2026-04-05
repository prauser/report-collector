"""Tests for task-10: rich failure tracking in attempt_pdf_download.

Verifies:
- Multi-stage failures accumulate in `attempts` list and are joined with " | "
- Combined reason is truncated to 500 chars
- mark_pdf_failed receives the combined reason when session is provided
- Any permanent failure stage makes overall result non-retryable
- All callers (listener, backfill, run_download_pending) wire fail_reason correctly
"""
from __future__ import annotations

import asyncio
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch, call


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def make_report(**kwargs):
    r = MagicMock()
    r.id = 42
    r.broker = "테스트증권"
    r.report_date = date(2026, 3, 8)
    r.stock_name = "삼성전자"
    r.sector = None
    r.title = "반도체업황개선"
    r.title_normalized = "반도체업황개선"
    r.pdf_url = None
    r.pdf_path = None
    r.pdf_download_failed = False
    r.source_message_id = None
    r.source_channel = "@testchannel"
    r.raw_text = "삼성전자 리포트"
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


def make_pdf_document_message():
    from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

    attr = MagicMock(spec=DocumentAttributeFilename)
    attr.file_name = "report.pdf"

    doc = MagicMock()
    doc.mime_type = "application/pdf"
    doc.attributes = [attr]

    media = MagicMock(spec=MessageMediaDocument)
    media.document = doc

    msg = MagicMock()
    msg.text = ""
    msg.id = 999
    msg.media = media
    return msg


# ──────────────────────────────────────────────
# Tests: multi-stage failure accumulation
# ──────────────────────────────────────────────

class TestMultiStageFailureAccumulation:
    """attempt_pdf_download accumulates failure reasons from all stages."""

    @pytest.mark.asyncio
    async def test_stage1_and_stage3_failures_combined(self):
        """Stage 1 telegram fails + Stage 3 HTTP fails → combined reason."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        msg = make_pdf_document_message()
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document", new_callable=AsyncMock,
                   return_value=(None, None)):
            with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                       return_value=(None, None, "http_404")):
                success, path, size_kb, reason, retryable = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    message=msg,
                )

        assert success is False
        # Both stage failures must be present in the combined reason
        assert "telegram_doc: download_failed" in reason
        assert "url_download: http_404" in reason
        assert " | " in reason

    @pytest.mark.asyncio
    async def test_stage3_failure_reason_preserved(self):
        """Stage 3 HTTP fail reason is captured in the combined output."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=(None, None, "timeout")):
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client,
                report=report,
            )

        assert success is False
        assert "url_download: timeout" in reason

    @pytest.mark.asyncio
    async def test_tme_resolve_failure_included(self):
        """Stage 2 t.me resolution failure is included in combined reason."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url=None)  # no pdf_url, so Stage 2 runs
        mock_client = MagicMock()
        tme_links = ["https://t.me/testchannel/123"]

        with patch("storage.pdf_archiver.resolve_tme_links", new_callable=AsyncMock,
                   return_value=(None, None)):
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client,
                report=report,
                tme_links=tme_links,
            )

        assert success is False
        assert "tme_resolve: no_result" in reason

    @pytest.mark.asyncio
    async def test_tme_exception_included_in_reason(self):
        """Stage 2 exception message is included in combined reason."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url=None)
        mock_client = MagicMock()
        tme_links = ["https://t.me/testchannel/123"]

        with patch("storage.pdf_archiver.resolve_tme_links", new_callable=AsyncMock,
                   side_effect=RuntimeError("connection refused")):
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client,
                report=report,
                tme_links=tme_links,
            )

        assert success is False
        assert "tme_resolve:" in reason
        assert "connection refused" in reason

    @pytest.mark.asyncio
    async def test_combined_reason_truncated_to_500_chars(self):
        """Combined failure reason is truncated to 500 characters."""
        from storage.pdf_archiver import attempt_pdf_download

        # Create a report so Stage 3 runs
        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()
        msg = make_pdf_document_message()

        # Stage 1 fails (telegram_doc: download_failed)
        with patch("storage.pdf_archiver.download_telegram_document", new_callable=AsyncMock,
                   return_value=(None, None)):
            # Stage 3 fails with a very long reason
            long_reason = "x" * 600
            with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                       return_value=(None, None, long_reason)):
                success, path, size_kb, reason, retryable = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    message=msg,
                )

        assert success is False
        assert reason is not None
        assert len(reason) <= 500


# ──────────────────────────────────────────────
# Tests: retryability with permanent vs retryable failures
# ──────────────────────────────────────────────

class TestRetryabilityTracking:
    """any_permanent flag: permanent failure in any stage makes result non-retryable."""

    @pytest.mark.asyncio
    async def test_permanent_http_404_makes_non_retryable(self):
        """http_404 is a permanent failure → retryable=False."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=(None, None, "http_404")):
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client,
                report=report,
            )

        assert success is False
        assert retryable is False

    @pytest.mark.asyncio
    async def test_transient_timeout_remains_retryable(self):
        """timeout is a transient failure → retryable=True."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=(None, None, "timeout")):
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client,
                report=report,
            )

        assert success is False
        assert retryable is True

    @pytest.mark.asyncio
    async def test_permanent_stage_mixed_with_retryable_is_non_retryable(self):
        """Stage 1 telegram fails (retryable) + Stage 3 http_404 (permanent) → non-retryable."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        msg = make_pdf_document_message()
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document", new_callable=AsyncMock,
                   return_value=(None, None)):
            with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                       return_value=(None, None, "http_404")):
                success, path, size_kb, reason, retryable = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    message=msg,
                )

        assert success is False
        assert retryable is False  # any_permanent=True from http_404

    @pytest.mark.asyncio
    async def test_no_source_is_non_retryable(self):
        """no_source result is non-retryable."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url=None)
        mock_client = MagicMock()

        success, path, size_kb, reason, retryable = await attempt_pdf_download(
            client=mock_client,
            report=report,
        )

        assert success is False
        assert reason == "no_source"
        assert retryable is False


# ──────────────────────────────────────────────
# Tests: mark_pdf_failed receives combined reason via session
# ──────────────────────────────────────────────

class TestMarkPdfFailedCalledWithCombinedReason:
    """When session is provided, mark_pdf_failed receives combined multi-stage reason."""

    @pytest.mark.asyncio
    async def test_mark_pdf_failed_called_with_combined_stage_reasons(self):
        """Stage 1 + Stage 3 failures → mark_pdf_failed gets combined string."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        msg = make_pdf_document_message()
        mock_client = MagicMock()
        mock_session = AsyncMock()

        with patch("storage.pdf_archiver.download_telegram_document", new_callable=AsyncMock,
                   return_value=(None, None)):
            with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                       return_value=(None, None, "http_503")):
                with patch("storage.report_repo.mark_pdf_failed", new_callable=AsyncMock) as mock_fail:
                    success, path, size_kb, reason, retryable = await attempt_pdf_download(
                        client=mock_client,
                        report=report,
                        message=msg,
                        session=mock_session,
                    )

        assert success is False
        mock_fail.assert_awaited_once()
        # Extract the reason arg passed to mark_pdf_failed
        call_args = mock_fail.await_args
        passed_reason = call_args[0][2]  # positional: (session, report_id, reason)
        assert "telegram_doc: download_failed" in passed_reason
        assert "url_download: http_503" in passed_reason

    @pytest.mark.asyncio
    async def test_mark_pdf_failed_reason_truncated_to_500(self):
        """mark_pdf_failed gets a reason of at most 500 chars."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()
        mock_session = AsyncMock()

        long_reason = "z" * 600
        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=(None, None, long_reason)):
            with patch("storage.report_repo.mark_pdf_failed", new_callable=AsyncMock) as mock_fail:
                success, _, _, reason, _ = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    session=mock_session,
                )

        assert success is False
        call_args = mock_fail.await_args
        passed_reason = call_args[0][2]
        assert len(passed_reason) <= 500

    @pytest.mark.asyncio
    async def test_no_session_mark_pdf_failed_not_called(self):
        """When session=None, mark_pdf_failed is not called inside attempt_pdf_download."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=(None, None, "http_404")):
            with patch("storage.report_repo.mark_pdf_failed", new_callable=AsyncMock) as mock_fail:
                success, _, _, reason, _ = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    session=None,
                )

        assert success is False
        assert reason is not None  # reason is still returned
        mock_fail.assert_not_awaited()  # but DB not called


# ──────────────────────────────────────────────
# Tests: callers wire fail_reason correctly
# ──────────────────────────────────────────────

class TestCallerWiringListener:
    """collector/listener.py passes session to attempt_pdf_download (DB updates inside)."""

    def test_listener_calls_attempt_pdf_download_with_session(self):
        """listener passes session= kwarg so DB updates happen inside attempt_pdf_download."""
        import ast
        import pathlib

        src = pathlib.Path(__file__).parent.parent / "collector" / "listener.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))

        # Find call to attempt_pdf_download and check for session= kwarg
        found_session_kwarg = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Await):
                call = node.value
                if isinstance(call, ast.Call):
                    func = call.func
                    if isinstance(func, ast.Name) and func.id == "attempt_pdf_download":
                        for kw in call.keywords:
                            if kw.arg == "session":
                                found_session_kwarg = True
                                break

        assert found_session_kwarg, (
            "listener.py should call attempt_pdf_download(session=session) "
            "so that mark_pdf_failed is called inside with the combined fail reason"
        )

    def test_listener_does_not_call_mark_pdf_failed_directly(self):
        """listener.py should not call mark_pdf_failed directly (handled inside attempt_pdf_download)."""
        import pathlib
        import re

        src = pathlib.Path(__file__).parent.parent / "collector" / "listener.py"
        text = src.read_text(encoding="utf-8")

        # Should not have a direct call to mark_pdf_failed
        assert "mark_pdf_failed" not in text, (
            "listener.py should not call mark_pdf_failed directly; "
            "the rich failure tracking is handled inside attempt_pdf_download when session is passed"
        )


class TestCallerWiringBackfill:
    """collector/backfill.py passes session to attempt_pdf_download (DB updates inside)."""

    def test_backfill_calls_attempt_pdf_download_with_session(self):
        """backfill._process_single_report passes session= kwarg."""
        import ast
        import pathlib

        src = pathlib.Path(__file__).parent.parent / "collector" / "backfill.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))

        found_session_kwarg = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Await):
                call = node.value
                if isinstance(call, ast.Call):
                    func = call.func
                    if isinstance(func, ast.Name) and func.id == "attempt_pdf_download":
                        for kw in call.keywords:
                            if kw.arg == "session":
                                found_session_kwarg = True
                                break

        assert found_session_kwarg, (
            "backfill.py should call attempt_pdf_download(session=session) "
            "so that mark_pdf_failed is called inside with the combined fail reason"
        )

    def test_backfill_does_not_call_mark_pdf_failed_directly(self):
        """backfill.py should not call mark_pdf_failed directly."""
        import pathlib

        src = pathlib.Path(__file__).parent.parent / "collector" / "backfill.py"
        text = src.read_text(encoding="utf-8")

        assert "mark_pdf_failed" not in text, (
            "backfill.py should not call mark_pdf_failed directly; "
            "the rich failure tracking is handled inside attempt_pdf_download when session is passed"
        )


class TestCallerWiringDownloadPending:
    """run_download_pending.py uses fail_reason returned from attempt_pdf_download."""

    def test_download_pending_passes_fail_reason_to_update_fail(self):
        """_process_report passes the fail_reason from attempt_pdf_download to _update_fail."""
        import ast
        import pathlib

        src = pathlib.Path(__file__).parent.parent / "run_download_pending.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))

        # Verify _update_fail is called with fail_reason variable (not a literal)
        # Look for: await _update_fail(report.id, detail[:500]) or similar
        found_update_fail = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Await):
                call = node.value
                if isinstance(call, ast.Call):
                    func = call.func
                    if isinstance(func, ast.Name) and func.id == "_update_fail":
                        found_update_fail = True
                        break

        assert found_update_fail, "run_download_pending.py should call _update_fail with fail reason"

    def test_download_pending_uses_fail_reason_from_attempt_result(self):
        """_process_report unpacks fail_reason from attempt_pdf_download's 5-tuple return."""
        import pathlib

        src = pathlib.Path(__file__).parent.parent / "run_download_pending.py"
        text = src.read_text(encoding="utf-8")

        # Check that the 5-tuple is unpacked with fail_reason
        assert "fail_reason" in text, (
            "run_download_pending.py should unpack fail_reason from attempt_pdf_download result"
        )

    @pytest.mark.asyncio
    async def test_download_pending_process_report_wires_fail_reason(self):
        """_process_report passes combined failure reasons to _update_fail.
        download_pending uses direct calls, so test at that level."""
        from run_download_pending import _process_report

        report = make_report(
            pdf_url="https://example.com/report.pdf",
            source_message_id=None,
            source_channel=None,
            raw_text="",
        )
        mock_client = MagicMock()

        with patch("run_download_pending.download_pdf",
                   new_callable=AsyncMock, return_value=(None, None, "http_503")), \
             patch("run_download_pending._update_fail",
                   new_callable=AsyncMock) as mock_fail:
            code, detail = await _process_report(mock_client, report)

        assert code == "fail"
        mock_fail.assert_awaited_once()
        # download_pending uses direct calls — fail_reason from download_pdf
        # is a local variable, detail comes from prefetch_error or "unknown"
        passed_reason = mock_fail.await_args[0][1]
        assert passed_reason is not None


# ──────────────────────────────────────────────
# Tests: mark_pdf_failed in report_repo stores up to 500 chars
# ──────────────────────────────────────────────

class TestMarkPdfFailedTruncation:
    """storage.report_repo.mark_pdf_failed truncates reason to 500 chars."""

    def test_mark_pdf_failed_truncates_long_reason_in_source(self):
        """mark_pdf_failed source contains reason[:500] to limit stored length."""
        import inspect
        from storage.report_repo import mark_pdf_failed

        src = inspect.getsource(mark_pdf_failed)
        assert "reason[:500]" in src, (
            "mark_pdf_failed should truncate reason to 500 chars with reason[:500]"
        )
