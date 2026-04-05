"""Tests for task-6: pdf_filename and attempt_pdf_download extraction.

Verifies:
- pdf_filename() in storage.pdf_archiver works identically to old _pdf_filename()
  in listener.py and backfill.py
- attempt_pdf_download() implements the 3-stage fallback correctly:
    Stage 1: direct Telegram document download (message param)
    Stage 2: t.me link resolution
    Stage 3: HTTP download from pdf_url
- Semaphore throttling (telegram_sem, http_sem) is respected
- DB updates happen when session is provided, skipped when session=None
- backfill.py uses text.strip() (not text)
"""
from __future__ import annotations

import asyncio
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch


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


def make_pdf_document_message(filename="report.pdf"):
    """Create a mock Telethon message with a PDF document attachment."""
    from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

    attr = MagicMock(spec=DocumentAttributeFilename)
    attr.file_name = filename

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


def make_text_only_message(text="some text"):
    msg = MagicMock()
    msg.text = text
    msg.id = 998
    msg.media = None
    return msg


# ──────────────────────────────────────────────
# Tests: pdf_filename
# ──────────────────────────────────────────────

class TestPdfFilename:
    """storage.pdf_archiver.pdf_filename — shared helper replacing _pdf_filename in listener/backfill."""

    def test_returns_filename_for_pdf_document(self):
        from storage.pdf_archiver import pdf_filename

        msg = make_pdf_document_message("samsung.pdf")
        assert pdf_filename(msg) == "samsung.pdf"

    def test_returns_none_for_non_pdf_document(self):
        from telethon.tl.types import MessageMediaDocument
        from storage.pdf_archiver import pdf_filename

        doc = MagicMock()
        doc.mime_type = "image/jpeg"
        doc.attributes = []

        media = MagicMock(spec=MessageMediaDocument)
        media.document = doc

        msg = MagicMock()
        msg.media = media
        assert pdf_filename(msg) is None

    def test_returns_none_when_no_media(self):
        from storage.pdf_archiver import pdf_filename

        msg = make_text_only_message()
        assert pdf_filename(msg) is None

    def test_returns_none_when_no_filename_attribute(self):
        """PDF mime_type but no DocumentAttributeFilename → None."""
        from telethon.tl.types import MessageMediaDocument
        from storage.pdf_archiver import pdf_filename

        doc = MagicMock()
        doc.mime_type = "application/pdf"
        doc.attributes = []  # no DocumentAttributeFilename

        media = MagicMock(spec=MessageMediaDocument)
        media.document = doc

        msg = MagicMock()
        msg.media = media
        assert pdf_filename(msg) is None

    def test_matches_listener_behavior(self):
        """pdf_filename result must equal collector.listener._pdf_filename result."""
        from storage.pdf_archiver import pdf_filename
        from collector.listener import _pdf_filename

        msg = make_pdf_document_message("test_report.pdf")
        assert pdf_filename(msg) == _pdf_filename(msg)

    def test_matches_backfill_behavior(self):
        """pdf_filename result must equal collector.backfill._pdf_filename result."""
        from storage.pdf_archiver import pdf_filename
        from collector.backfill import _pdf_filename

        msg = make_pdf_document_message("backfill_report.pdf")
        assert pdf_filename(msg) == _pdf_filename(msg)


# ──────────────────────────────────────────────
# Tests: attempt_pdf_download — Stage 1 (Telegram document)
# ──────────────────────────────────────────────

class TestAttemptPdfDownloadStage1:
    """Stage 1: direct Telegram document download when message is provided."""

    @pytest.mark.asyncio
    async def test_stage1_success_returns_true_and_path(self, tmp_path):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report()
        msg = make_pdf_document_message()
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document", new_callable=AsyncMock,
                   return_value=("2026/03/report.pdf", 120)) as mock_dl:
            success, path, size_kb, reason, retryable = await attempt_pdf_download(
                client=mock_client,
                report=report,
                message=msg,
            )

        assert success is True
        assert path == "2026/03/report.pdf"
        assert size_kb == 120
        assert reason is None
        assert retryable is None
        mock_dl.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stage1_failure_falls_through_to_stage3(self, tmp_path):
        """When Stage 1 fails, fall through to Stage 3 (pdf_url download)."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        msg = make_pdf_document_message()
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document", new_callable=AsyncMock,
                   return_value=(None, None)):
            with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                       return_value=("2026/03/report.pdf", 50, None)) as mock_http:
                success, path, size_kb, reason, _ = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    message=msg,
                )

        assert success is True
        mock_http.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stage1_skipped_when_no_message(self):
        """When message=None, Stage 1 is skipped entirely."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_telegram_document", new_callable=AsyncMock) as mock_tg:
            with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                       return_value=("2026/03/report.pdf", 50, None)):
                success, path, size_kb, reason, _ = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    message=None,
                )

        mock_tg.assert_not_awaited()
        assert success is True


# ──────────────────────────────────────────────
# Tests: attempt_pdf_download — Stage 2 (t.me resolve)
# ──────────────────────────────────────────────

class TestAttemptPdfDownloadStage2:
    """Stage 2: t.me link resolution when tme_links is provided."""

    @pytest.mark.asyncio
    async def test_stage2_tme_url_resolved_then_http_download(self):
        """t.me resolves to a PDF URL → HTTP download in Stage 3."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report()  # no pdf_url initially
        mock_client = MagicMock()
        tme_links = ["https://t.me/testchannel/123"]

        with patch("storage.pdf_archiver.resolve_tme_links", new_callable=AsyncMock,
                   return_value=("https://example.com/resolved.pdf", None)):
            with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                       return_value=("2026/03/report.pdf", 80, None)) as mock_http:
                success, path, size_kb, reason, _ = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    tme_links=tme_links,
                )

        assert success is True
        # pdf_url should be set on report object
        assert report.pdf_url == "https://example.com/resolved.pdf"
        mock_http.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stage2_tme_document_downloaded_directly(self):
        """t.me resolves to a document → download document directly."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report()
        mock_client = MagicMock()
        tme_links = ["https://t.me/testchannel/456"]
        tme_msg = MagicMock()

        with patch("storage.pdf_archiver.resolve_tme_links", new_callable=AsyncMock,
                   return_value=(None, tme_msg)):
            with patch("storage.pdf_archiver.download_telegram_document", new_callable=AsyncMock,
                       return_value=("2026/03/report.pdf", 90)) as mock_tg:
                success, path, size_kb, reason, _ = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    tme_links=tme_links,
                )

        assert success is True
        mock_tg.assert_awaited_once_with(mock_client, tme_msg, report)

    @pytest.mark.asyncio
    async def test_stage2_skipped_when_no_tme_links(self):
        """When tme_links=None, Stage 2 is skipped."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.resolve_tme_links", new_callable=AsyncMock) as mock_resolve:
            with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                       return_value=("2026/03/report.pdf", 50, None)):
                await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    tme_links=None,
                )

        mock_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stage2_skipped_when_report_already_has_pdf_url(self):
        """Stage 2 is skipped when report.pdf_url already set (nothing to resolve)."""
        from storage.pdf_archiver import attempt_pdf_download

        # report already has pdf_url set
        report = make_report(pdf_url="https://existing.com/report.pdf")
        mock_client = MagicMock()
        tme_links = ["https://t.me/testchannel/789"]

        with patch("storage.pdf_archiver.resolve_tme_links", new_callable=AsyncMock) as mock_resolve:
            with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                       return_value=("2026/03/report.pdf", 50, None)):
                await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    tme_links=tme_links,
                )

        # resolve_tme_links should NOT be called since pdf_url already exists
        mock_resolve.assert_not_awaited()


# ──────────────────────────────────────────────
# Tests: attempt_pdf_download — Stage 3 (HTTP)
# ──────────────────────────────────────────────

class TestAttemptPdfDownloadStage3:
    """Stage 3: HTTP download from report.pdf_url."""

    @pytest.mark.asyncio
    async def test_stage3_success(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=("2026/03/report.pdf", 100, None)):
            success, path, size_kb, reason, _ = await attempt_pdf_download(
                client=mock_client,
                report=report,
            )

        assert success is True
        assert path == "2026/03/report.pdf"
        assert size_kb == 100
        assert reason is None

    @pytest.mark.asyncio
    async def test_stage3_failure_returns_false(self):
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
        assert path is None
        assert size_kb is None
        assert "http_404" in reason

    @pytest.mark.asyncio
    async def test_stage3_skipped_when_no_pdf_url(self):
        """When report has no pdf_url, Stage 3 is skipped."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url=None)
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock) as mock_dl:
            success, path, size_kb, reason, _ = await attempt_pdf_download(
                client=mock_client,
                report=report,
            )

        mock_dl.assert_not_awaited()
        assert success is False
        assert reason == "no_source"


# ──────────────────────────────────────────────
# Tests: attempt_pdf_download — no_source
# ──────────────────────────────────────────────

class TestAttemptPdfDownloadNoSource:
    """no_source case: no message, no tme_links, no pdf_url."""

    @pytest.mark.asyncio
    async def test_no_source_returns_false_with_no_source_reason(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url=None)
        mock_client = MagicMock()

        success, path, size_kb, reason, retryable = await attempt_pdf_download(
            client=mock_client,
            report=report,
            message=None,
            tme_links=None,
        )

        assert success is False
        assert reason == "no_source"
        assert retryable is False


# ──────────────────────────────────────────────
# Tests: attempt_pdf_download — semaphore throttling
# ──────────────────────────────────────────────

class TestAttemptPdfDownloadSemaphore:
    """Semaphore throttling: telegram_sem and http_sem."""

    @pytest.mark.asyncio
    async def test_telegram_sem_acquired_for_stage1(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report()
        msg = make_pdf_document_message()
        mock_client = MagicMock()
        sem = asyncio.Semaphore(1)
        acquired = []

        original_sem_aenter = sem.__class__.__aenter__

        async def track_acquire(self):
            acquired.append(True)
            return await original_sem_aenter(self)

        with patch("storage.pdf_archiver.download_telegram_document", new_callable=AsyncMock,
                   return_value=("2026/03/r.pdf", 10)):
            with patch.object(type(sem), "__aenter__", track_acquire):
                await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    message=msg,
                    telegram_sem=sem,
                )

        assert len(acquired) >= 1

    @pytest.mark.asyncio
    async def test_http_sem_acquired_for_stage3(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()
        sem = asyncio.Semaphore(1)
        acquired = []

        original_sem_aenter = sem.__class__.__aenter__

        async def track_acquire(self):
            acquired.append(True)
            return await original_sem_aenter(self)

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=("2026/03/r.pdf", 10, None)):
            with patch.object(type(sem), "__aenter__", track_acquire):
                await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    http_sem=sem,
                )

        assert len(acquired) >= 1

    @pytest.mark.asyncio
    async def test_no_sem_does_not_raise(self):
        """When sem=None, no throttling, no error."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=("2026/03/r.pdf", 10, None)):
            success, path, size_kb, reason, _ = await attempt_pdf_download(
                client=mock_client,
                report=report,
                telegram_sem=None,
                http_sem=None,
            )

        assert success is True


# ──────────────────────────────────────────────
# Tests: attempt_pdf_download — DB updates when session provided
# ──────────────────────────────────────────────

class TestAttemptPdfDownloadDbUpdates:
    """When session is provided, DB updates happen inside attempt_pdf_download."""

    @pytest.mark.asyncio
    async def test_success_calls_update_pdf_info_and_pipeline_status(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()
        mock_session = AsyncMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=("2026/03/r.pdf", 50, None)):
            with patch("storage.report_repo.update_pdf_info", new_callable=AsyncMock) as mock_upd:
                with patch("storage.report_repo.update_pipeline_status", new_callable=AsyncMock) as mock_status:
                    success, path, size_kb, reason, _ = await attempt_pdf_download(
                        client=mock_client,
                        report=report,
                        session=mock_session,
                    )

        assert success is True
        mock_upd.assert_awaited_once_with(mock_session, report.id, "2026/03/r.pdf", 50, None)
        mock_status.assert_awaited_once_with(mock_session, report.id, "pdf_done")
        mock_session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_failure_calls_mark_pdf_failed(self):
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()
        mock_session = AsyncMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=(None, None, "http_404")):
            with patch("storage.report_repo.mark_pdf_failed", new_callable=AsyncMock) as mock_fail:
                success, path, size_kb, reason, _ = await attempt_pdf_download(
                    client=mock_client,
                    report=report,
                    session=mock_session,
                )

        assert success is False
        mock_fail.assert_awaited_once()
        mock_session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_session_no_db_calls(self):
        """When session=None, no DB functions are called."""
        from storage.pdf_archiver import attempt_pdf_download

        report = make_report(pdf_url="https://example.com/report.pdf")
        mock_client = MagicMock()

        with patch("storage.pdf_archiver.download_pdf", new_callable=AsyncMock,
                   return_value=("2026/03/r.pdf", 50, None)):
            with patch("storage.report_repo.update_pdf_info", new_callable=AsyncMock) as mock_upd:
                with patch("storage.report_repo.update_pipeline_status", new_callable=AsyncMock) as mock_status:
                    success, _, _, _, _ = await attempt_pdf_download(
                        client=mock_client,
                        report=report,
                        session=None,
                    )

        assert success is True
        mock_upd.assert_not_awaited()
        mock_status.assert_not_awaited()


# ──────────────────────────────────────────────
# Tests: backfill text.strip() fix
# ──────────────────────────────────────────────

class TestBackfillTextStripFix:
    """backfill.py:~320 must use text.strip() to skip whitespace-only messages."""

    def test_backfill_source_uses_text_strip(self):
        """Verify backfill.py uses text.strip() not bare text check."""
        import ast
        import pathlib

        src = pathlib.Path(
            __file__
        ).parent.parent / "collector" / "backfill.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))

        # Find the `if not text.strip():` node
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test = node.test
                # Match: `not text.strip()`
                if (
                    isinstance(test, ast.UnaryOp)
                    and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Call)
                    and isinstance(test.operand.func, ast.Attribute)
                    and test.operand.func.attr == "strip"
                    and isinstance(test.operand.func.value, ast.Name)
                    and test.operand.func.value.id == "text"
                ):
                    found = True
                    break

        assert found, "backfill.py should use `if not text.strip():` not `if not text:`"

    def test_backfill_no_bare_text_check_near_pdf_fname(self):
        """Ensure `if not text:` (bare) does NOT appear next to pdf_fname check."""
        import pathlib
        src = pathlib.Path(
            __file__
        ).parent.parent / "collector" / "backfill.py"
        lines = src.read_text(encoding="utf-8").splitlines()

        # Find lines that have bare `if not text:` pattern
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "if not text:":
                # Check if a nearby line references pdf_fname
                context = "\n".join(lines[max(0, i - 3):i + 4])
                assert "pdf_fname" not in context, (
                    f"Found bare `if not text:` near pdf_fname at line {i + 1}. "
                    f"Should be `if not text.strip():`"
                )


# ──────────────────────────────────────────────
# Tests: _pdf_filename only defined in pdf_archiver
# ──────────────────────────────────────────────

class TestPdfFilenameOnlyInArchiver:
    """pdf_filename logic lives in pdf_archiver; listener/backfill delegate."""

    def test_listener_pdf_filename_delegates_to_archiver(self):
        """collector.listener._pdf_filename delegates to storage.pdf_archiver.pdf_filename."""
        from collector.listener import _pdf_filename
        from storage.pdf_archiver import pdf_filename

        msg = make_pdf_document_message("check.pdf")
        assert _pdf_filename(msg) == pdf_filename(msg)

    def test_backfill_pdf_filename_delegates_to_archiver(self):
        """collector.backfill._pdf_filename delegates to storage.pdf_archiver.pdf_filename."""
        from collector.backfill import _pdf_filename
        from storage.pdf_archiver import pdf_filename

        msg = make_pdf_document_message("check2.pdf")
        assert _pdf_filename(msg) == pdf_filename(msg)

    def test_listener_does_not_define_own_pdf_filename_logic(self):
        """listener._pdf_filename should not contain its own telethon type checks."""
        import inspect
        from collector import listener

        src = inspect.getsource(listener._pdf_filename)
        # The function should be a thin delegation, not contain DocumentAttributeFilename logic
        assert "DocumentAttributeFilename" not in src, (
            "listener._pdf_filename should delegate to pdf_archiver.pdf_filename, "
            "not contain its own DocumentAttributeFilename check"
        )

    def test_backfill_does_not_define_own_pdf_filename_logic(self):
        """backfill._pdf_filename should not contain its own telethon type checks."""
        import inspect
        from collector import backfill

        src = inspect.getsource(backfill._pdf_filename)
        assert "DocumentAttributeFilename" not in src, (
            "backfill._pdf_filename should delegate to pdf_archiver.pdf_filename, "
            "not contain its own DocumentAttributeFilename check"
        )
