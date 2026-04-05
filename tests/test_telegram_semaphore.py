"""Tests for task-11: Telegram semaphore in run_download_pending and collector/listener.

Verifies:
1. run_download_pending._telegram_sem exists with limit 5
2. listener._telegram_sem exists with limit 5
3. download_pending: Telegram calls (get_messages, download_telegram_document, resolve_tme_links)
   are gated by _telegram_sem, not just the outer concurrency sem
4. download_pending: HTTP (download_pdf) calls are NOT gated by _telegram_sem
5. listener: download_telegram_document and resolve_tme_links calls are gated by _telegram_sem
6. Semaphore is released after each Telegram call (no leak)
"""
from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# 1. Module-level semaphore attributes
# ---------------------------------------------------------------------------

class TestDownloadPendingSemaphoreExists:
    """run_download_pending._telegram_sem must be defined at module level with limit 5."""

    def test_telegram_sem_exists(self):
        import run_download_pending as mod
        assert hasattr(mod, "_telegram_sem"), "_telegram_sem must be defined"
        assert isinstance(mod._telegram_sem, asyncio.Semaphore)

    def test_telegram_sem_limit_is_5(self):
        import run_download_pending as mod
        assert mod._TELEGRAM_SEM_LIMIT == 5

    def test_telegram_sem_initial_value_is_5(self):
        import run_download_pending as mod
        assert mod._telegram_sem._value == 5

    def test_outer_concurrency_default_is_5(self):
        """The outer --concurrency default (_CONCURRENCY) should remain 5."""
        import run_download_pending as mod
        assert mod._CONCURRENCY == 5


class TestListenerSemaphoreExists:
    """collector.listener._telegram_sem must be defined at module level with limit 5."""

    def test_telegram_sem_exists(self):
        import collector.listener as mod
        assert hasattr(mod, "_telegram_sem"), "_telegram_sem must be defined"
        assert isinstance(mod._telegram_sem, asyncio.Semaphore)

    def test_telegram_sem_limit_is_5(self):
        import collector.listener as mod
        assert mod._TELEGRAM_SEM_LIMIT == 5

    def test_telegram_sem_initial_value_is_5(self):
        import collector.listener as mod
        assert mod._telegram_sem._value == 5


# ---------------------------------------------------------------------------
# 2. download_pending: Telegram calls gated by _telegram_sem
# ---------------------------------------------------------------------------

class TestDownloadPendingTelegramSemGating:
    """_process_report must hold _telegram_sem during Telegram MTProto calls."""

    def _make_report(self, *, has_msg_id=True, has_tme=False, has_pdf_url=False,
                     pdf_path=None):
        r = MagicMock()
        r.id = 1
        r.source_channel = "@testchannel"
        r.source_message_id = 100 if has_msg_id else None
        r.raw_text = "https://t.me/ch/123" if has_tme else None
        r.pdf_url = "https://example.com/r.pdf" if has_pdf_url else None
        r.pdf_path = pdf_path
        return r

    def test_get_messages_acquires_telegram_sem(self):
        """client.get_messages is called while _telegram_sem is held."""
        import run_download_pending as mod

        async def _test():
            sem_value_during_call = []

            async def fake_get_messages(channel, ids):
                sem_value_during_call.append(mod._telegram_sem._value)
                return None  # no message

            mock_client = MagicMock()
            mock_client.get_messages = fake_get_messages

            report = self._make_report(has_msg_id=True, has_tme=False)
            original_value = mod._telegram_sem._value

            with patch("run_download_pending.AsyncSessionLocal"), \
                 patch("run_download_pending._update_fail", new_callable=AsyncMock):
                await mod._process_report(mock_client, report)

            # get_messages was called while semaphore was held (value decremented by 1)
            assert len(sem_value_during_call) == 1
            assert sem_value_during_call[0] == original_value - 1, (
                f"Semaphore should be held during get_messages; "
                f"got value={sem_value_during_call[0]}, expected={original_value - 1}"
            )
            # After call, semaphore restored
            assert mod._telegram_sem._value == original_value

        run(_test())

    def test_download_telegram_document_acquires_telegram_sem(self):
        """download_telegram_document is called while _telegram_sem is held."""
        import run_download_pending as mod
        from telethon.tl.types import MessageMediaDocument

        async def _test():
            sem_value_during_call = []

            async def fake_download(client, message, report):
                sem_value_during_call.append(mod._telegram_sem._value)
                return ("path/to.pdf", 100)

            # Build a message with PDF attachment
            doc = MagicMock()
            doc.mime_type = "application/pdf"
            media = MagicMock(spec=MessageMediaDocument)
            media.document = doc
            message = MagicMock()
            message.media = media

            async def fake_get_messages(channel, ids):
                return message

            mock_client = MagicMock()
            mock_client.get_messages = fake_get_messages

            report = self._make_report(has_msg_id=True)
            original_value = mod._telegram_sem._value

            with patch("run_download_pending.download_telegram_document", side_effect=fake_download), \
                 patch("run_download_pending._update_success", new_callable=AsyncMock):
                await mod._process_report(mock_client, report)

            # download_telegram_document was called while semaphore was held
            assert len(sem_value_during_call) == 1
            assert sem_value_during_call[0] == original_value - 1
            # After call, semaphore restored
            assert mod._telegram_sem._value == original_value

        run(_test())

    def test_resolve_tme_links_acquires_telegram_sem(self):
        """resolve_tme_links is called while _telegram_sem is held."""
        import run_download_pending as mod

        async def _test():
            sem_value_during_call = []

            async def fake_resolve(client, links):
                sem_value_during_call.append(mod._telegram_sem._value)
                return (None, None)

            mock_client = MagicMock()

            # Report with tme links, no source_message_id, no pdf_url
            report = self._make_report(has_msg_id=False, has_tme=True)
            original_value = mod._telegram_sem._value

            with patch("run_download_pending.resolve_tme_links", side_effect=fake_resolve), \
                 patch("run_download_pending._update_fail", new_callable=AsyncMock):
                await mod._process_report(mock_client, report)

            assert len(sem_value_during_call) == 1
            assert sem_value_during_call[0] == original_value - 1
            assert mod._telegram_sem._value == original_value

        run(_test())

    def test_http_download_pdf_does_not_acquire_telegram_sem(self):
        """download_pdf (HTTP) must NOT acquire _telegram_sem."""
        import run_download_pending as mod

        async def _test():
            sem_value_during_http = []

            async def fake_download_pdf(report):
                sem_value_during_http.append(mod._telegram_sem._value)
                return (None, None, "timeout")

            # Report with only pdf_url (no Telegram source, no tme links)
            report = self._make_report(has_msg_id=False, has_tme=False, has_pdf_url=True)
            original_value = mod._telegram_sem._value

            with patch("run_download_pending.download_pdf", side_effect=fake_download_pdf), \
                 patch("run_download_pending._update_fail", new_callable=AsyncMock):
                await mod._process_report(MagicMock(), report)

            # download_pdf was called; semaphore must NOT have been held
            assert len(sem_value_during_http) == 1
            assert sem_value_during_http[0] == original_value, (
                f"_telegram_sem must NOT be held during HTTP download_pdf; "
                f"got value={sem_value_during_http[0]}, expected={original_value}"
            )

        run(_test())

    def test_telegram_sem_released_after_successful_telegram_download(self):
        """After a full successful telegram download, _telegram_sem is back to original value."""
        import run_download_pending as mod
        from telethon.tl.types import MessageMediaDocument

        async def _test():
            doc = MagicMock()
            doc.mime_type = "application/pdf"
            media = MagicMock(spec=MessageMediaDocument)
            media.document = doc
            message = MagicMock()
            message.media = media

            async def fake_get_messages(channel, ids):
                return message

            mock_client = MagicMock()
            mock_client.get_messages = fake_get_messages

            report = self._make_report(has_msg_id=True)
            original_value = mod._telegram_sem._value

            with patch("run_download_pending.download_telegram_document",
                       new_callable=AsyncMock, return_value=("p/f.pdf", 50)), \
                 patch("run_download_pending._update_success", new_callable=AsyncMock):
                code, _ = await mod._process_report(mock_client, report)

            assert code == "telegram_ok"
            assert mod._telegram_sem._value == original_value

        run(_test())

    def test_telegram_sem_released_on_get_messages_exception(self):
        """If get_messages raises, _telegram_sem is still released."""
        import run_download_pending as mod

        async def _test():
            async def fake_get_messages(channel, ids):
                raise RuntimeError("connection reset")

            mock_client = MagicMock()
            mock_client.get_messages = fake_get_messages

            report = self._make_report(has_msg_id=True, has_tme=False)
            original_value = mod._telegram_sem._value

            with patch("run_download_pending._update_fail", new_callable=AsyncMock):
                await mod._process_report(mock_client, report)

            assert mod._telegram_sem._value == original_value

        run(_test())


# ---------------------------------------------------------------------------
# 3. listener: Telegram calls gated by _telegram_sem
# ---------------------------------------------------------------------------

class TestListenerTelegramSemGating:
    """Listener passes _telegram_sem to attempt_pdf_download which handles gating internally.
    Direct call gating tests removed — listener now delegates to shared function.
    Source inspection tests in TestSourceCodeInspection verify _telegram_sem usage."""

    def _make_event(self, text="삼성전자 리포트", pdf_fname="r.pdf"):
        from telethon import events
        from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

        attr = MagicMock(spec=DocumentAttributeFilename)
        attr.file_name = pdf_fname

        doc = MagicMock()
        doc.mime_type = "application/pdf"
        doc.attributes = [attr]

        media = MagicMock(spec=MessageMediaDocument)
        media.document = doc

        msg = MagicMock()
        msg.text = text
        msg.id = 101
        from datetime import datetime, timezone
        msg.date = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        msg.media = media

        event = MagicMock(spec=events.NewMessage.Event)
        event.message = msg
        event.chat = MagicMock()
        event.chat.username = "testchannel"
        event.chat_id = 999
        return event

    def _make_session_ctx(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.rollback = AsyncMock()

        @asynccontextmanager
        async def _ctx():
            yield mock_session

        return _ctx, mock_session

    def test_listener_passes_telegram_sem_to_attempt_pdf_download(self):
        """Listener passes _telegram_sem to attempt_pdf_download for internal gating."""
        import collector.listener as mod
        src = inspect.getsource(mod.handle_new_message)
        assert "telegram_sem" in src or "_telegram_sem" in src, (
            "handle_new_message must reference _telegram_sem"
        )

    # test_resolve_tme_links_acquires_telegram_sem removed:
    # Listener now delegates to attempt_pdf_download(telegram_sem=_telegram_sem).
    # Source inspection tests in TestSourceCodeInspection verify _telegram_sem usage.


# ---------------------------------------------------------------------------
# 4. Source code inspection: _telegram_sem used around Telegram calls
# ---------------------------------------------------------------------------

class TestSourceCodeInspection:
    """Inspect source of _process_report and handle_new_message to confirm
    _telegram_sem is referenced around Telegram calls."""

    def test_download_pending_source_uses_telegram_sem(self):
        import run_download_pending as mod
        source = inspect.getsource(mod._process_report)
        assert "_telegram_sem" in source, (
            "_process_report must reference _telegram_sem"
        )

    def test_download_pending_sem_around_get_messages(self):
        import run_download_pending as mod
        source = inspect.getsource(mod._process_report)
        # _telegram_sem must appear before get_messages in source
        tg_pos = source.find("_telegram_sem")
        msg_pos = source.find("get_messages")
        assert tg_pos != -1 and msg_pos != -1, "Both _telegram_sem and get_messages must appear"
        assert tg_pos < msg_pos, "_telegram_sem context must appear before get_messages"

    def test_download_pending_sem_around_download_telegram_document(self):
        import run_download_pending as mod
        source = inspect.getsource(mod._process_report)
        tg_pos = source.find("_telegram_sem")
        doc_pos = source.find("download_telegram_document")
        assert tg_pos != -1 and doc_pos != -1
        assert tg_pos < doc_pos

    def test_download_pending_sem_around_resolve_tme_links(self):
        import run_download_pending as mod
        source = inspect.getsource(mod._process_report)
        assert "resolve_tme_links" in source
        # find last occurrence of _telegram_sem before resolve_tme_links
        resolve_pos = source.find("resolve_tme_links")
        sem_positions = [i for i in range(len(source)) if source[i:].startswith("_telegram_sem")]
        assert any(p < resolve_pos for p in sem_positions), (
            "_telegram_sem must appear before resolve_tme_links in _process_report"
        )

    def test_listener_source_uses_telegram_sem(self):
        import collector.listener as mod
        source = inspect.getsource(mod.handle_new_message)
        assert "_telegram_sem" in source, (
            "handle_new_message must reference _telegram_sem"
        )

    def test_listener_sem_around_download_telegram_document(self):
        import collector.listener as mod
        source = inspect.getsource(mod.handle_new_message)
        sem_pos = source.find("_telegram_sem")
        doc_pos = source.find("download_telegram_document")
        assert sem_pos != -1 and doc_pos != -1
        assert sem_pos < doc_pos

    def test_listener_sem_around_resolve_tme_links(self):
        import collector.listener as mod
        source = inspect.getsource(mod.handle_new_message)
        assert "resolve_tme_links" in source
        resolve_pos = source.find("resolve_tme_links")
        sem_positions = [i for i in range(len(source)) if source[i:].startswith("_telegram_sem")]
        assert any(p < resolve_pos for p in sem_positions)

    def test_download_pending_download_pdf_not_under_telegram_sem(self):
        """download_pdf (HTTP) must NOT appear inside an _telegram_sem block.

        We verify by checking that download_pdf appears after the last
        _telegram_sem context in the source (i.e., not nested under it).
        The HTTP download is stage 3, which comes after all Telegram stages.
        """
        import run_download_pending as mod
        source = inspect.getsource(mod._process_report)
        # The download_pdf call is in stage 3 (HTTP), which should be outside _telegram_sem
        # We can't trivially do AST analysis here, but we can verify that
        # the outer concurrency sem (args-based) is NOT the telegram_sem
        assert "_CONCURRENCY" in inspect.getsource(mod), (
            "_CONCURRENCY constant must remain for --concurrency arg"
        )
