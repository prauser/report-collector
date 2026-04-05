"""
Tests for analysis_enabled guard in collector/backfill.py _process_single_report.

Covers:
- When analysis_enabled=False, key_data/markdown/chart steps are not called
- When analysis_enabled=False, layer2_input is None in result
- When analysis_enabled=True, analysis steps are called (mocked)
- save_markdown is not imported in backfill.py
- save_markdown is not imported in listener.py
"""
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import date, datetime, timezone
from contextlib import asynccontextmanager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_message(msg_id: int = 1):
    from telethon.tl.types import Message
    msg = MagicMock(spec=Message)
    msg.text = "삼성전자(005930) 리포트"
    msg.id = msg_id
    msg.date = datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)
    msg.media = None
    return msg


def make_mock_session():
    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=None)
    mock_session.scalars = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.begin_nested = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=False),
    ))

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return _ctx, mock_session


# ---------------------------------------------------------------------------
# Import guard tests
# ---------------------------------------------------------------------------

def test_save_markdown_not_imported_in_backfill():
    """backfill.py must not import save_markdown."""
    import collector.backfill as backfill_mod
    source = inspect.getsource(backfill_mod)
    assert "save_markdown" not in source, (
        "backfill.py still references save_markdown — it should be removed"
    )


def test_save_markdown_not_imported_in_listener():
    """listener.py must not import save_markdown."""
    import collector.listener as listener_mod
    source = inspect.getsource(listener_mod)
    assert "save_markdown" not in source, (
        "listener.py still imports save_markdown — remove it"
    )


def test_save_markdown_still_exists_in_analysis_repo():
    """save_markdown function still exists in analysis_repo.py (not deleted, just unused in backfill)."""
    from storage.analysis_repo import save_markdown
    assert callable(save_markdown)


# ---------------------------------------------------------------------------
# _process_single_report: analysis_enabled=False guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analysis_steps_skipped_when_disabled():
    """When analysis_enabled=False, extract_key_data/convert_pdf_to_markdown are not called."""
    from collector.backfill import _process_single_report, _ReportTask
    from parser.llm_parser import S2aResult

    mock_parsed = MagicMock()
    mock_parsed.report_date = date(2026, 1, 10)
    mock_parsed.stock_name = "삼성전자"
    mock_parsed.stock_code = "005930"
    mock_parsed.pdf_url = None
    mock_parsed.tme_message_links = None
    mock_parsed.raw_text = "리포트 텍스트"

    mock_report = MagicMock()
    mock_report.id = 42
    mock_report.pdf_url = None
    mock_report.pdf_path = "/some/path.pdf"

    mock_session_ctx, mock_session = make_mock_session()
    mock_session.scalar = AsyncMock(return_value=None)  # already_analyzed = None

    task = _ReportTask(
        parsed=mock_parsed,
        message=make_mock_message(1),
        pdf_fname=None,
        channel_username="@testchannel",
        client=AsyncMock(),
    )

    with patch("collector.backfill.classify_message", new_callable=AsyncMock,
               return_value=S2aResult("broker_report")), \
         patch("collector.backfill.stock_mapper") as mock_mapper, \
         patch("collector.backfill.assess_parse_quality", return_value="high"), \
         patch("collector.backfill.upsert_report", new_callable=AsyncMock,
               return_value=(mock_report, "inserted")), \
         patch("collector.backfill.update_pipeline_status", new_callable=AsyncMock), \
         patch("collector.backfill.AsyncSessionLocal", mock_session_ctx), \
         patch("collector.backfill.extract_key_data", new_callable=AsyncMock) as mock_key_data, \
         patch("collector.backfill.convert_pdf_to_markdown", new_callable=AsyncMock) as mock_md, \
         patch("collector.backfill.extract_images_from_pdf", new_callable=AsyncMock) as mock_images, \
         patch("collector.backfill.settings") as mock_settings:

        mock_settings.analysis_enabled = False
        mock_settings.pdf_base_path = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.gemini_api_key = None
        mock_mapper.get_code = AsyncMock(return_value="005930")

        result = await _process_single_report(task)

    # Analysis steps must NOT be called
    mock_key_data.assert_not_called()
    mock_md.assert_not_called()
    mock_images.assert_not_called()


@pytest.mark.asyncio
async def test_layer2_input_is_none_when_analysis_disabled():
    """When analysis_enabled=False, result.layer2_input must be None."""
    from collector.backfill import _process_single_report, _ReportTask
    from parser.llm_parser import S2aResult

    mock_parsed = MagicMock()
    mock_parsed.report_date = date(2026, 1, 10)
    mock_parsed.stock_name = "LG전자"
    mock_parsed.stock_code = "066570"
    mock_parsed.pdf_url = None
    mock_parsed.tme_message_links = None
    mock_parsed.raw_text = "LG전자 리포트"

    mock_report = MagicMock()
    mock_report.id = 55
    mock_report.pdf_url = None
    mock_report.pdf_path = None  # no PDF

    mock_session_ctx, mock_session = make_mock_session()
    mock_session.scalar = AsyncMock(return_value=None)

    task = _ReportTask(
        parsed=mock_parsed,
        message=make_mock_message(2),
        pdf_fname=None,
        channel_username="@testchannel",
        client=AsyncMock(),
    )

    with patch("collector.backfill.classify_message", new_callable=AsyncMock,
               return_value=S2aResult("broker_report")), \
         patch("collector.backfill.stock_mapper") as mock_mapper, \
         patch("collector.backfill.assess_parse_quality", return_value="medium"), \
         patch("collector.backfill.upsert_report", new_callable=AsyncMock,
               return_value=(mock_report, "inserted")), \
         patch("collector.backfill.update_pipeline_status", new_callable=AsyncMock), \
         patch("collector.backfill.AsyncSessionLocal", mock_session_ctx), \
         patch("collector.backfill.settings") as mock_settings:

        mock_settings.analysis_enabled = False
        mock_settings.pdf_base_path = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_mapper.get_code = AsyncMock(return_value="066570")

        result = await _process_single_report(task)

    assert result.layer2_input is None


@pytest.mark.asyncio
async def test_analysis_steps_called_when_enabled():
    """When analysis_enabled=True, extract_key_data and convert_pdf_to_markdown are called."""
    from collector.backfill import _process_single_report, _ReportTask
    from parser.llm_parser import S2aResult

    mock_parsed = MagicMock()
    mock_parsed.report_date = date(2026, 1, 10)
    mock_parsed.stock_name = "SK하이닉스"
    mock_parsed.stock_code = "000660"
    mock_parsed.pdf_url = None
    mock_parsed.tme_message_links = None
    mock_parsed.raw_text = "SK하이닉스 리포트"

    mock_report = MagicMock()
    mock_report.id = 77
    mock_report.pdf_url = None
    mock_report.pdf_path = "2026/01/test.pdf"

    mock_session_ctx, mock_session = make_mock_session()
    mock_session.scalar = AsyncMock(return_value=None)

    task = _ReportTask(
        parsed=mock_parsed,
        message=make_mock_message(3),
        pdf_fname=None,
        channel_username="@testchannel",
        client=AsyncMock(),
    )

    mock_abs_path = MagicMock()
    mock_abs_path.exists.return_value = True

    with patch("collector.backfill.classify_message", new_callable=AsyncMock,
               return_value=S2aResult("broker_report")), \
         patch("collector.backfill.stock_mapper") as mock_mapper, \
         patch("collector.backfill.assess_parse_quality", return_value="high"), \
         patch("collector.backfill.upsert_report", new_callable=AsyncMock,
               return_value=(mock_report, "inserted")), \
         patch("collector.backfill.update_pipeline_status", new_callable=AsyncMock), \
         patch("collector.backfill.AsyncSessionLocal", mock_session_ctx), \
         patch("collector.backfill.extract_key_data", new_callable=AsyncMock,
               return_value=None) as mock_key_data, \
         patch("collector.backfill.convert_pdf_to_markdown", new_callable=AsyncMock,
               return_value=(None, "pymupdf")) as mock_md, \
         patch("collector.backfill.settings") as mock_settings:

        mock_settings.analysis_enabled = True
        mock_pdf_base = MagicMock()
        mock_pdf_base.__truediv__ = MagicMock(return_value=mock_abs_path)
        mock_settings.pdf_base_path = mock_pdf_base
        mock_settings.anthropic_api_key = None
        mock_settings.gemini_api_key = None
        mock_mapper.get_code = AsyncMock(return_value="000660")

        result = await _process_single_report(task)

    # Analysis steps MUST be called when enabled
    mock_key_data.assert_called_once()
    mock_md.assert_called_once()


# ---------------------------------------------------------------------------
# Source code inspection: analysis_enabled guard exists in _process_single_report
# ---------------------------------------------------------------------------

def test_process_single_report_has_analysis_enabled_guard():
    """_process_single_report source must contain settings.analysis_enabled guard."""
    from collector.backfill import _process_single_report
    source = inspect.getsource(_process_single_report)
    assert "settings.analysis_enabled" in source, (
        "_process_single_report must check settings.analysis_enabled"
    )


def test_process_single_report_no_save_markdown_call():
    """_process_single_report source must not call save_markdown."""
    from collector.backfill import _process_single_report
    source = inspect.getsource(_process_single_report)
    assert "save_markdown" not in source, (
        "_process_single_report must not call save_markdown"
    )
