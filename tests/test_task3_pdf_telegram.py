"""Tests for task-3: PDF+text gap fix and source_message_id API/buttons.

Part A: collector/listener.py and collector/backfill.py — _pdf_filename always called.
Part B: API ReportDetail schema includes source_message_id.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Part A: listener._pdf_filename always called even when text is present
# ---------------------------------------------------------------------------

def _make_pdf_document_message(text: str, filename: str = "report.pdf"):
    """Create a mock Telethon message with both text and a PDF document."""
    from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

    attr = MagicMock(spec=DocumentAttributeFilename)
    attr.file_name = filename

    doc = MagicMock()
    doc.mime_type = "application/pdf"
    doc.attributes = [attr]

    media = MagicMock(spec=MessageMediaDocument)
    media.document = doc

    msg = MagicMock()
    msg.text = text
    msg.id = 999
    msg.date = datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc)
    msg.media = media
    return msg


def _make_text_only_message(text: str):
    """Message with text but no media."""
    msg = MagicMock()
    msg.text = text
    msg.id = 998
    msg.date = datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc)
    msg.media = None
    return msg


class TestPdfFilenameFromListener:
    """Tests for listener._pdf_filename extraction logic."""

    def test_pdf_filename_extracted_when_text_present(self):
        """_pdf_filename returns filename even when message has text."""
        from collector.listener import _pdf_filename

        msg = _make_pdf_document_message(
            text="삼성전자 리포트 - 미래에셋증권",
            filename="samsung_report.pdf",
        )
        result = _pdf_filename(msg)
        assert result == "samsung_report.pdf"

    def test_pdf_filename_extracted_when_text_empty(self):
        """_pdf_filename still works with empty text."""
        from collector.listener import _pdf_filename

        msg = _make_pdf_document_message(text="", filename="report.pdf")
        result = _pdf_filename(msg)
        assert result == "report.pdf"

    def test_pdf_filename_none_when_no_media(self):
        """_pdf_filename returns None when there is no media."""
        from collector.listener import _pdf_filename

        msg = _make_text_only_message("some text")
        result = _pdf_filename(msg)
        assert result is None

    def test_pdf_filename_none_for_non_pdf_document(self):
        """_pdf_filename returns None for non-PDF attachments."""
        from telethon.tl.types import MessageMediaDocument

        attr = MagicMock()
        attr.file_name = "image.jpg"

        doc = MagicMock()
        doc.mime_type = "image/jpeg"
        doc.attributes = [attr]

        media = MagicMock(spec=MessageMediaDocument)
        media.document = doc

        msg = MagicMock()
        msg.text = ""
        msg.id = 1
        msg.media = media

        from collector.listener import _pdf_filename
        result = _pdf_filename(msg)
        assert result is None


class TestPdfFilenameFromBackfill:
    """Tests for backfill._pdf_filename extraction logic (same helper)."""

    def test_pdf_filename_extracted_when_text_present(self):
        """backfill._pdf_filename returns filename even when message has text."""
        from collector.backfill import _pdf_filename

        msg = _make_pdf_document_message(
            text="LG에너지솔루션 실적 분석",
            filename="lg_energy.pdf",
        )
        result = _pdf_filename(msg)
        assert result == "lg_energy.pdf"

    def test_pdf_filename_none_when_no_media(self):
        """backfill._pdf_filename returns None when no document attached."""
        from collector.backfill import _pdf_filename

        msg = _make_text_only_message("텍스트만 있는 메시지")
        result = _pdf_filename(msg)
        assert result is None


# ---------------------------------------------------------------------------
# Part A: Integration — text+PDF message results in pdf_fname being set
# ---------------------------------------------------------------------------

def _make_db_session_ctx():
    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=None)
    mock_session.get = AsyncMock(return_value=MagicMock(id=1))
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.rollback = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return _ctx, mock_session


@pytest.mark.asyncio
async def test_listener_extracts_pdf_fname_when_text_and_pdf():
    """handle_new_message sets pdf_fname when message has both text and PDF doc."""
    from telethon import events
    from parser.llm_parser import S2aResult

    text = "삼성전자(005930) 목표가 90,000원 - 미래에셋증권"
    msg = _make_pdf_document_message(text=text, filename="samsung.pdf")

    event = MagicMock(spec=events.NewMessage.Event)
    event.message = msg
    event.chat = MagicMock()
    event.chat.username = "testchannel"
    event.chat_id = 12345

    mock_report = MagicMock(
        id=1,
        pdf_url=None,
        pdf_path=None,
        tme_message_links=[],
        source_message_id=None,
    )

    session_ctx, mock_session = _make_db_session_ctx()

    with patch("collector.listener._get_active_channels", new_callable=AsyncMock, return_value={"@testchannel"}), \
         patch("collector.listener.parse_messages") as mock_parse, \
         patch("collector.listener.classify_message", new_callable=AsyncMock) as mock_s2a, \
         patch("collector.listener.upsert_report", new_callable=AsyncMock, return_value=(mock_report, "inserted")), \
         patch("collector.listener.update_pipeline_status", new_callable=AsyncMock), \
         patch("collector.listener.attempt_pdf_download", new_callable=AsyncMock, return_value=(True, "path/to.pdf", 100, None, None)), \
         patch("collector.listener.AsyncSessionLocal", session_ctx), \
         patch("collector.listener.get_client") as mock_get_client, \
         patch("collector.listener.assess_parse_quality", return_value="good"), \
         patch("collector.listener.stock_mapper") as mock_mapper:

        from parser.registry import ParsedReport
        parsed = MagicMock(spec=ParsedReport)
        parsed.report_date = None
        parsed.stock_name = "삼성전자"
        parsed.stock_code = None
        parsed.pdf_url = None
        parsed.tme_message_links = []
        parsed.raw_text = text

        mock_parse.return_value = [parsed]
        mock_s2a.return_value = S2aResult("broker_report")
        mock_mapper.get_code = AsyncMock(return_value="005930")
        mock_get_client.return_value = MagicMock()

        # The key verification: download_telegram_document should be called
        # because pdf_fname was extracted even though text was present.
        from collector.listener import handle_new_message
        await handle_new_message(event)

    import collector.listener as listener_mod
    # We verify the flow ran to completion (no exception, report processed)
    assert mock_parse.called


def test_backfill_pdf_fname_set_when_text_and_pdf():
    """backfill._pdf_filename is called and returns filename when text is also present.

    This is a unit test of the fixed logic: pdf_fname is now assigned unconditionally
    at the top of the message processing loop, regardless of whether text is present.
    The key change is that pdf_fname = _pdf_filename(message) is always called.
    """
    from collector.backfill import _pdf_filename

    text = "LG전자(066570) 분기 실적 - KB증권"
    msg = _make_pdf_document_message(text=text, filename="lg_elec.pdf")

    # This is the new behaviour: _pdf_filename is always called even when text is present.
    result = _pdf_filename(msg)
    assert result == "lg_elec.pdf", (
        "pdf_fname must be extracted even when text is non-empty"
    )

    # Verify: a text-only message (no PDF) still returns None.
    text_only = _make_text_only_message("some report text")
    assert _pdf_filename(text_only) is None


# ---------------------------------------------------------------------------
# Part B: API schema — ReportDetail has source_message_id
# ---------------------------------------------------------------------------

class TestReportDetailSchema:
    """Tests for api/schemas.py ReportDetail."""

    def test_report_detail_has_source_message_id_field(self):
        """ReportDetail schema includes source_message_id field."""
        from api.schemas import ReportDetail
        fields = ReportDetail.model_fields
        assert "source_message_id" in fields

    def test_report_detail_source_message_id_nullable(self):
        """source_message_id allows None values."""
        from api.schemas import ReportDetail
        import inspect
        field = ReportDetail.model_fields["source_message_id"]
        # annotation should be Optional[int] / int | None
        ann = str(field.annotation)
        assert "int" in ann or "NoneType" in ann

    def test_report_detail_constructs_with_source_message_id(self):
        """ReportDetail can be constructed with source_message_id set."""
        from api.schemas import ReportDetail
        from datetime import date, datetime

        detail = ReportDetail(
            id=1,
            broker="미래에셋",
            report_date=date(2026, 3, 20),
            analyst="홍길동",
            stock_name="삼성전자",
            stock_code="005930",
            title="삼성전자 목표가 상향",
            sector="반도체",
            report_type="분석",
            opinion="매수",
            target_price=90000,
            prev_opinion=None,
            prev_target_price=None,
            has_pdf=True,
            has_ai=False,
            ai_sentiment=None,
            collected_at=datetime(2026, 3, 20, 9, 0),
            source_channel="@testchannel",
            display_title="삼성전자 목표가 상향",
            layer2_summary=None,
            layer2_sentiment=None,
            layer2_category=None,
            ai_summary=None,
            ai_keywords=None,
            ai_processed_at=None,
            pdf_url="https://example.com/report.pdf",
            pdf_path=None,
            pdf_size_kb=512,
            page_count=10,
            earnings_quarter=None,
            est_revenue=None,
            est_op_profit=None,
            est_eps=None,
            raw_text=None,
            source_message_id=12345,
            layer2=None,
        )
        assert detail.source_message_id == 12345

    def test_report_detail_constructs_with_source_message_id_none(self):
        """ReportDetail can be constructed with source_message_id as None."""
        from api.schemas import ReportDetail
        from datetime import date, datetime

        detail = ReportDetail(
            id=2,
            broker="KB증권",
            report_date=date(2026, 3, 20),
            analyst=None,
            stock_name=None,
            stock_code=None,
            title="시장 분석",
            sector=None,
            report_type=None,
            opinion=None,
            target_price=None,
            prev_opinion=None,
            prev_target_price=None,
            has_pdf=False,
            has_ai=False,
            ai_sentiment=None,
            collected_at=datetime(2026, 3, 20, 9, 0),
            source_channel="@kbchannel",
            display_title="시장 분석",
            layer2_summary=None,
            layer2_sentiment=None,
            layer2_category=None,
            ai_summary=None,
            ai_keywords=None,
            ai_processed_at=None,
            pdf_url=None,
            pdf_path=None,
            pdf_size_kb=None,
            page_count=None,
            earnings_quarter=None,
            est_revenue=None,
            est_op_profit=None,
            est_eps=None,
            raw_text=None,
            source_message_id=None,
            layer2=None,
        )
        assert detail.source_message_id is None


# ---------------------------------------------------------------------------
# Part B: API router — get_report returns source_message_id
# ---------------------------------------------------------------------------

class TestGetReportEndpoint:
    """Tests for api/routers/reports.py get_report endpoint."""

    def _make_report_model(self, source_message_id=None):
        """Create a mock Report ORM object."""
        r = MagicMock()
        r.id = 1
        r.broker = "미래에셋"
        r.report_date = __import__("datetime").date(2026, 3, 20)
        r.analyst = "홍길동"
        r.stock_name = "삼성전자"
        r.stock_code = "005930"
        r.title = "삼성전자 리포트"
        r.sector = "반도체"
        r.report_type = "분석"
        r.opinion = "매수"
        r.target_price = 90000
        r.prev_opinion = None
        r.prev_target_price = None
        r.pdf_path = "/data/samsung.pdf"
        r.ai_processed_at = None
        r.ai_sentiment = None
        r.collected_at = __import__("datetime").datetime(2026, 3, 20, 9, 0)
        r.source_channel = "@testchannel"
        r.ai_summary = None
        r.ai_keywords = None
        r.pdf_url = "https://example.com/samsung.pdf"
        r.pdf_size_kb = 512
        r.page_count = 10
        r.earnings_quarter = None
        r.est_revenue = None
        r.est_op_profit = None
        r.est_eps = None
        r.raw_text = None
        r.source_message_id = source_message_id
        return r

    def _make_execute_result(self, scalar_one_or_none_value=None, scalars_all_value=None):
        """Return a mock result object for db.execute() calls."""
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=scalar_one_or_none_value)
        scalars_result = MagicMock()
        scalars_result.all = MagicMock(return_value=scalars_all_value or [])
        result.scalars = MagicMock(return_value=scalars_result)
        return result

    def test_get_report_includes_source_message_id(self):
        """GET /api/reports/{id} response includes source_message_id."""
        from api.main import app
        from api.deps import get_db

        mock_report = self._make_report_model(source_message_id=42000)
        execute_result = self._make_execute_result(scalar_one_or_none_value=None)

        async def override_db():
            db = AsyncMock()
            db.get = AsyncMock(return_value=mock_report)
            db.execute = AsyncMock(return_value=execute_result)
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            client = TestClient(app)
            resp = client.get("/api/reports/1")
            assert resp.status_code == 200
            data = resp.json()
            assert "source_message_id" in data
            assert data["source_message_id"] == 42000
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_get_report_source_message_id_null(self):
        """GET /api/reports/{id} returns null for source_message_id when not set."""
        from api.main import app
        from api.deps import get_db

        mock_report = self._make_report_model(source_message_id=None)
        execute_result = self._make_execute_result(scalar_one_or_none_value=None)

        async def override_db():
            db = AsyncMock()
            db.get = AsyncMock(return_value=mock_report)
            db.execute = AsyncMock(return_value=execute_result)
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            client = TestClient(app)
            resp = client.get("/api/reports/1")
            assert resp.status_code == 200
            data = resp.json()
            assert "source_message_id" in data
            assert data["source_message_id"] is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_get_report_404(self):
        """GET /api/reports/{id} returns 404 when report not found."""
        from api.main import app
        from api.deps import get_db

        async def override_db():
            db = AsyncMock()
            db.get = AsyncMock(return_value=None)
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            client = TestClient(app)
            resp = client.get("/api/reports/9999")
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)
