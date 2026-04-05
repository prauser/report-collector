"""Tests for T6 critical bug fixes.

Issue 1: size_kb always None — attempt_pdf_download now returns 5-tuple
         (success, rel_path, size_kb, fail_reason, retryable)
Issue 2: is_retryable_failure on multi-stage join string gives wrong result
         — per-stage retryability is now tracked; any permanent failure -> non-retryable
Issue 3: mark_pdf_failed truncates to 50 chars
         — now truncates to 500 chars
"""
from __future__ import annotations

import asyncio
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch


def make_report(**kwargs):
    r = MagicMock()
    r.id = 99
    r.broker = "테스트증권"
    r.report_date = date(2026, 3, 8)
    r.stock_name = "삼성전자"
    r.sector = None
    r.title = "반도체 전망"
    r.title_normalized = "반도체 전망"
    r.pdf_url = None
    r.pdf_path = None
    r.pdf_download_failed = False
    r.source_message_id = None
    r.source_channel = "@testchannel"
    r.raw_text = "샘플 텍스트"
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Issue 1: size_kb is preserved in the return tuple
# ─────────────────────────────────────────────────────────────────────────────

class TestSizeKbInReturnTuple:
    """attempt_pdf_download returns size_kb as 3rd element of 5-tuple."""

    @pytest.mark.asyncio
    async def test_stage1_success_size_kb_preserved(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report()
        mock_client = MagicMock()
        msg = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document",
                   new_callable=AsyncMock, return_value=("2026/03/r.pdf", 250)):
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client, report=report, message=msg
            )

        assert success is True
        assert size_kb == 250
        assert reason is None
        assert retryable is None

    @pytest.mark.asyncio
    async def test_stage3_success_size_kb_preserved(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/r.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf",
                   new_callable=AsyncMock, return_value=("2026/03/r.pdf", 512, None)):
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client, report=report
            )

        assert success is True
        assert size_kb == 512

    @pytest.mark.asyncio
    async def test_stage2_tme_doc_success_size_kb_preserved(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report()
        mock_client = MagicMock()
        tme_msg = MagicMock()

        with patch("storage.pdf_archiver.resolve_tme_links",
                   new_callable=AsyncMock, return_value=(None, tme_msg)):
            with patch("storage.pdf_archiver.download_telegram_document",
                       new_callable=AsyncMock, return_value=("2026/03/r.pdf", 99)):
                success, path, size_kb, reason, retryable = await attempt_pdf_download(
                    client=mock_client, report=report,
                    tme_links=["https://t.me/ch/1"]
                )

        assert success is True
        assert size_kb == 99

    @pytest.mark.asyncio
    async def test_failure_size_kb_is_none(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/r.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf",
                   new_callable=AsyncMock, return_value=(None, None, "timeout")):
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client, report=report
            )

        assert success is False
        assert size_kb is None

    @pytest.mark.asyncio
    async def test_no_source_size_kb_is_none(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url=None)
        mock_client = MagicMock()

        success, path, size_kb, reason, retryable = await attempt_pdf_download(
            client=mock_client, report=report, message=None, tme_links=None
        )

        assert success is False
        assert size_kb is None
        assert reason == "no_source"


class TestRunDownloadPendingSizeKb:
    """run_download_pending._process_report passes size_kb to _update_success.
    download_pending uses direct calls (not attempt_pdf_download), so test at that level."""

    @pytest.mark.asyncio
    async def test_process_report_passes_size_kb_from_telegram(self):
        """Telegram doc download returns (rel_path, size_kb), both passed to _update_success."""
        from run_download_pending import _process_report

        report = make_report(
            source_message_id=100,
            source_channel="@ch",
            raw_text="",
            pdf_url=None,
        )
        mock_client = MagicMock()

        mock_msg = MagicMock()
        mock_msg.media = MagicMock()
        mock_msg.media.document = MagicMock()
        mock_msg.media.document.mime_type = "application/pdf"
        mock_msg.media.document.attributes = []

        with patch("run_download_pending._telegram_sem", asyncio.Semaphore(5)), \
             patch("run_download_pending.download_telegram_document",
                   new_callable=AsyncMock, return_value=("2026/03/r.pdf", 300)), \
             patch("run_download_pending._update_success",
                   new_callable=AsyncMock) as mock_update, \
             patch("run_download_pending._has_pdf_attachment", return_value=True):
            mock_client.get_messages = AsyncMock(return_value=mock_msg)
            await _process_report(mock_client, report)

        mock_update.assert_awaited_once_with(report.id, "2026/03/r.pdf", 300)

    @pytest.mark.asyncio
    async def test_process_report_passes_size_kb_from_http(self):
        """HTTP download returns (rel_path, size_kb, fail_reason), size_kb passed through."""
        from run_download_pending import _process_report

        report = make_report(
            source_message_id=None,
            source_channel=None,
            raw_text="",
            pdf_url="https://example.com/r.pdf",
        )
        mock_client = MagicMock()

        with patch("run_download_pending.download_pdf",
                   new_callable=AsyncMock, return_value=("2026/03/r.pdf", 150, None)), \
             patch("run_download_pending._update_success",
                   new_callable=AsyncMock) as mock_update:
            await _process_report(mock_client, report)

        mock_update.assert_awaited_once_with(report.id, "2026/03/r.pdf", 150)


# ─────────────────────────────────────────────────────────────────────────────
# Issue 2: retryable flag on multi-stage failures
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryableMultiStageFailure:
    """When any stage has a permanent failure, overall retryable must be False."""

    @pytest.mark.asyncio
    async def test_telegram_fail_then_http_404_is_non_retryable(self):
        """Stage 1 telegram fail + Stage 3 http_404 → non-retryable (404 is permanent)."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/r.pdf")
        mock_client = MagicMock()
        msg = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document",
                   new_callable=AsyncMock, return_value=(None, None)):
            with patch("storage.pdf_archiver.download_pdf",
                       new_callable=AsyncMock, return_value=(None, None, "http_404")):
                success, path, size_kb, reason, retryable = await attempt_pdf_download(
                    client=mock_client, report=report, message=msg
                )

        assert success is False
        assert retryable is False, (
            f"Expected non-retryable because http_404 is permanent, got retryable={retryable}. "
            f"reason={reason!r}"
        )
        assert "http_404" in reason

    @pytest.mark.asyncio
    async def test_telegram_fail_then_http_410_is_non_retryable(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/r.pdf")
        mock_client = MagicMock()
        msg = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document",
                   new_callable=AsyncMock, return_value=(None, None)):
            with patch("storage.pdf_archiver.download_pdf",
                       new_callable=AsyncMock, return_value=(None, None, "http_410")):
                success, path, size_kb, reason, retryable = await attempt_pdf_download(
                    client=mock_client, report=report, message=msg
                )

        assert retryable is False

    @pytest.mark.asyncio
    async def test_telegram_fail_then_not_pdf_html_is_non_retryable(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/r.pdf")
        mock_client = MagicMock()
        msg = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document",
                   new_callable=AsyncMock, return_value=(None, None)):
            with patch("storage.pdf_archiver.download_pdf",
                       new_callable=AsyncMock, return_value=(None, None, "not_pdf:html_response")):
                success, path, size_kb, reason, retryable = await attempt_pdf_download(
                    client=mock_client, report=report, message=msg
                )

        assert retryable is False

    @pytest.mark.asyncio
    async def test_telegram_fail_then_timeout_is_retryable(self):
        """Stage 1 telegram fail + Stage 3 timeout → retryable (both retryable)."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/r.pdf")
        mock_client = MagicMock()
        msg = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document",
                   new_callable=AsyncMock, return_value=(None, None)):
            with patch("storage.pdf_archiver.download_pdf",
                       new_callable=AsyncMock, return_value=(None, None, "timeout")):
                success, path, size_kb, reason, retryable = await attempt_pdf_download(
                    client=mock_client, report=report, message=msg
                )

        assert success is False
        assert retryable is True, (
            f"Expected retryable because both stages had transient failures, got {retryable}. "
            f"reason={reason!r}"
        )

    @pytest.mark.asyncio
    async def test_only_http_404_is_non_retryable(self):
        """Single stage (HTTP only) with http_404 → non-retryable."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/r.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf",
                   new_callable=AsyncMock, return_value=(None, None, "http_404")):
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client, report=report
            )

        assert retryable is False

    @pytest.mark.asyncio
    async def test_joined_reason_string_does_not_confuse_retryable_check(self):
        """Verify the fix: joined string 'telegram_doc: download_failed | url_download: http_404'
        is correctly identified as non-retryable even though the joined string itself
        is not in _PERMANENT_FAILURES."""
        from storage.pdf_archiver import is_retryable_failure, attempt_pdf_download

        # The old bug: this joined string would be retryable=True because it's not
        # in _PERMANENT_FAILURES (which only has bare "http_404")
        joined = "telegram_doc: download_failed | url_download: http_404"
        # The is_retryable_failure function alone returns True for joined string
        # (by design — it only checks bare strings). The fix is in attempt_pdf_download,
        # which now tracks per-stage retryability.
        # This test documents the behavior of is_retryable_failure on joined strings:
        assert is_retryable_failure(joined) is True  # bare function still returns True

        # But attempt_pdf_download now correctly tracks per-stage permanence:
        report = make_report(pdf_url="https://example.com/r.pdf")
        mock_client = MagicMock()
        msg = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document",
                   new_callable=AsyncMock, return_value=(None, None)):
            with patch("storage.pdf_archiver.download_pdf",
                       new_callable=AsyncMock, return_value=(None, None, "http_404")):
                success, path, size_kb, reason, retryable = await attempt_pdf_download(
                    client=mock_client, report=report, message=msg
                )

        assert "http_404" in reason
        assert retryable is False  # fixed: per-stage tracking catches the permanent failure


# ─────────────────────────────────────────────────────────────────────────────
# Issue 3: mark_pdf_failed truncation is 500, not 50
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkPdfFailedTruncation:
    """mark_pdf_failed stores up to 500 chars, not 50."""

    @pytest.mark.asyncio
    async def test_long_reason_not_truncated_to_50(self):
        from storage.report_repo import mark_pdf_failed

        long_reason = "telegram_doc: download_failed | url_download: http_404 | extra: " + "x" * 450
        assert len(long_reason) > 50

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        # is_retryable_failure is locally imported inside mark_pdf_failed from
        # storage.pdf_archiver — patch it there.
        with patch("storage.pdf_archiver.is_retryable_failure", return_value=False):
            await mark_pdf_failed(mock_session, 1, long_reason)

        # Extract the values passed to session.execute
        call_args = mock_session.execute.call_args
        stmt = call_args[0][0]
        # The compile + string check: look at the bound params
        compiled = stmt.compile(compile_kwargs={"literal_binds": True})
        sql_str = str(compiled)
        # The stored value should be long_reason[:500], which is > 50 chars
        expected_stored = long_reason[:500]
        assert expected_stored in sql_str, (
            f"Expected the stored reason to be up to 500 chars. SQL: {sql_str[:200]}"
        )

    @pytest.mark.asyncio
    async def test_reason_at_exactly_50_chars_stored_fully(self):
        """A reason of exactly 50 chars must be stored without truncation."""
        from storage.report_repo import mark_pdf_failed

        reason_50 = "a" * 50
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        with patch("storage.pdf_archiver.is_retryable_failure", return_value=True):
            await mark_pdf_failed(mock_session, 2, reason_50)

        call_args = mock_session.execute.call_args
        stmt = call_args[0][0]
        compiled = stmt.compile(compile_kwargs={"literal_binds": True})
        sql_str = str(compiled)
        assert reason_50 in sql_str

    @pytest.mark.asyncio
    async def test_reason_at_300_chars_stored_fully(self):
        """A reason of 300 chars must not be truncated (300 < 500)."""
        from storage.report_repo import mark_pdf_failed

        reason_300 = "x" * 300
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        with patch("storage.pdf_archiver.is_retryable_failure", return_value=False):
            await mark_pdf_failed(mock_session, 3, reason_300)

        call_args = mock_session.execute.call_args
        stmt = call_args[0][0]
        compiled = stmt.compile(compile_kwargs={"literal_binds": True})
        sql_str = str(compiled)
        assert reason_300 in sql_str

    def test_truncation_limit_in_source(self):
        """Verify that the source code uses [:500] not [:50]."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "storage" / "report_repo.py").read_text(encoding="utf-8")
        assert "reason[:500]" in src, "mark_pdf_failed should use reason[:500]"
        assert "reason[:50]" not in src, "mark_pdf_failed must not use reason[:50]"
