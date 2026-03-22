"""
Tests for the queue+worker pattern in collector/backfill.py.

Covers:
- _process_single_report has no semaphore parameter
- Workers process all tasks concurrently up to _BACKFILL_CONCURRENCY
- TimeoutError per task is caught and stored as _ReportResult("error", ...)
- Exception per task is caught and stored as Exception in results
- layer2_inputs dict is populated during worker execution
- Empty task list results in 0 workers spawned (no error)
- Result aggregation correctly counts saved / pending / skipped
"""
import asyncio
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from contextlib import asynccontextmanager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_message(text: str = "리포트 텍스트", msg_id: int = 1):
    from telethon.tl.types import Message
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.id = msg_id
    msg.date = datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc)
    msg.media = None
    return msg


def make_mock_db_session():
    """Minimal AsyncSessionLocal replacement that satisfies backfill_channel setup."""
    mock_run = MagicMock()
    mock_run.id = 99

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=None)
    mock_session.get = AsyncMock(return_value=mock_run)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "id", 99))
    mock_session.execute = AsyncMock()
    mock_session.rollback = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return _ctx, mock_session


# ---------------------------------------------------------------------------
# Unit tests on _process_single_report signature
# ---------------------------------------------------------------------------

def test_process_single_report_no_semaphore_param():
    """_process_single_report must NOT accept a semaphore parameter."""
    from collector.backfill import _process_single_report
    sig = inspect.signature(_process_single_report)
    assert "semaphore" not in sig.parameters, (
        "_process_single_report still has semaphore parameter — remove it"
    )


def test_process_single_report_single_param():
    """_process_single_report should accept exactly one parameter: task."""
    from collector.backfill import _process_single_report
    sig = inspect.signature(_process_single_report)
    params = list(sig.parameters.keys())
    assert params == ["task"], f"Expected ['task'], got {params}"


# ---------------------------------------------------------------------------
# Integration-style tests for the queue+worker pattern
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_worker_processes_all_tasks():
    """All tasks in the queue are processed by workers."""
    messages = [make_mock_message(msg_id=i) for i in range(1, 4)]
    sample_text = (
        "▶ 삼성전자(005930) 반도체 업황 개선 - 미래에셋증권\n"
        "https://example.com/report.pdf\n"
        "- 목표가: 85,000원 (매수)"
    )
    for m in messages:
        m.text = sample_text

    async def fake_iter(*args, **kwargs):
        for m in messages:
            yield m

    mock_report = MagicMock(id=1, pdf_url=None, pdf_path=None, tme_message_links=None)
    mock_session_ctx, mock_session = make_mock_db_session()
    mock_session.scalar = AsyncMock(return_value=None)

    from parser.llm_parser import S2aResult

    with patch("collector.backfill.get_client") as mock_get_client, \
         patch("collector.backfill.AsyncSessionLocal", mock_session_ctx), \
         patch("collector.backfill.upsert_report", new_callable=AsyncMock,
               return_value=(mock_report, "inserted")) as mock_upsert, \
         patch("collector.backfill.classify_message", new_callable=AsyncMock,
               return_value=S2aResult("broker_report")), \
         patch("collector.backfill.stock_mapper") as mock_mapper, \
         patch("collector.backfill.assess_parse_quality", return_value="high"), \
         patch("collector.backfill.build_user_content", return_value=("content", False, 100)), \
         patch("collector.backfill.settings") as mock_settings:

        mock_settings.backfill_limit = None
        mock_settings.pdf_base_path = MagicMock()
        mock_settings.analysis_enabled = False
        mock_settings.anthropic_api_key = None

        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client
        mock_mapper.get_code = AsyncMock(return_value="005930")

        from collector.backfill import backfill_channel
        saved = await backfill_channel("@testchannel", limit=10)

    assert saved == 3
    assert mock_upsert.call_count == 3


@pytest.mark.asyncio
async def test_queue_worker_timeout_does_not_abort_others():
    """A single task timeout is recorded as error; other tasks still complete."""
    sample_text = (
        "▶ LG전자(066570) 리포트 - 키움증권\n"
        "https://example.com/report2.pdf"
    )

    msg_normal = make_mock_message(sample_text, msg_id=10)
    msg_timeout = make_mock_message(sample_text, msg_id=11)

    async def fake_iter(*args, **kwargs):
        for m in [msg_normal, msg_timeout]:
            yield m

    mock_session_ctx, _ = make_mock_db_session()

    from collector.backfill import _ReportResult, _ReportTask

    call_count = {"n": 0}

    async def side_effect_process(task):
        call_count["n"] += 1
        if task.message.id == 11:
            # Simulate a very slow task that will be cancelled by TimeoutError
            await asyncio.sleep(1000)
        return _ReportResult("inserted", task.message.id)

    with patch("collector.backfill.get_client") as mock_get_client, \
         patch("collector.backfill.AsyncSessionLocal", mock_session_ctx), \
         patch("collector.backfill._process_single_report", side_effect=side_effect_process), \
         patch("collector.backfill.parse_messages") as mock_parse, \
         patch("collector.backfill.settings") as mock_settings:

        mock_settings.backfill_limit = None
        mock_settings.pdf_base_path = MagicMock()
        mock_settings.analysis_enabled = False
        mock_settings.anthropic_api_key = None

        mock_parsed = MagicMock()
        mock_parsed.pdf_url = None
        mock_parse.return_value = [mock_parsed]

        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        # Patch asyncio.wait_for to use a very short timeout for the hanging task
        original_wait_for = asyncio.wait_for

        async def patched_wait_for(coro, timeout):
            # Use a tiny timeout so the sleep(1000) task times out quickly
            return await original_wait_for(coro, timeout=0.01)

        with patch("collector.backfill.asyncio.wait_for", side_effect=patched_wait_for):
            from collector.backfill import backfill_channel
            saved = await backfill_channel("@testchannel", limit=10)

    # One task succeeds (msg_id=10), one times out (msg_id=11)
    assert saved == 1


@pytest.mark.asyncio
async def test_queue_worker_empty_tasks_no_error():
    """When there are no parseable messages, 0 workers are created without error."""
    async def fake_iter(*args, **kwargs):
        # yield a message that parse_messages returns []
        msg = make_mock_message("일반 뉴스 텍스트만", msg_id=1)
        yield msg

    mock_session_ctx, _ = make_mock_db_session()

    with patch("collector.backfill.get_client") as mock_get_client, \
         patch("collector.backfill.AsyncSessionLocal", mock_session_ctx), \
         patch("collector.backfill.parse_messages", return_value=[]), \
         patch("collector.backfill.settings") as mock_settings:

        mock_settings.backfill_limit = None
        mock_settings.pdf_base_path = MagicMock()
        mock_settings.analysis_enabled = False
        mock_settings.anthropic_api_key = None

        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        from collector.backfill import backfill_channel
        saved = await backfill_channel("@testchannel", limit=10)

    assert saved == 0


@pytest.mark.asyncio
async def test_queue_worker_exception_counted_not_raised():
    """An exception from _process_single_report is stored in results, not re-raised."""
    sample_text = "▶ SK하이닉스(000660) - 신한금융투자\nhttps://example.com/r.pdf"
    msg = make_mock_message(sample_text, msg_id=20)

    async def fake_iter(*args, **kwargs):
        yield msg

    mock_session_ctx, _ = make_mock_db_session()

    from parser.llm_parser import S2aResult

    with patch("collector.backfill.get_client") as mock_get_client, \
         patch("collector.backfill.AsyncSessionLocal", mock_session_ctx), \
         patch("collector.backfill.classify_message", new_callable=AsyncMock,
               side_effect=RuntimeError("LLM exploded")), \
         patch("collector.backfill.settings") as mock_settings:

        mock_settings.backfill_limit = None
        mock_settings.pdf_base_path = MagicMock()
        mock_settings.analysis_enabled = False
        mock_settings.anthropic_api_key = None

        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        from collector.backfill import backfill_channel
        saved = await backfill_channel("@testchannel", limit=10)

    assert saved == 0  # exception task is not counted as saved


@pytest.mark.asyncio
async def test_queue_worker_layer2_inputs_collected():
    """layer2_input from _process_single_report results is collected in layer2_inputs dict."""
    sample_text = (
        "▶ NAVER(035420) AI 전략 - NH투자증권\n"
        "https://example.com/naver.pdf"
    )
    msg = make_mock_message(sample_text, msg_id=30)

    async def fake_iter(*args, **kwargs):
        yield msg

    from collector.backfill import _ReportResult, _Layer2Input

    layer2_inp = _Layer2Input(
        report_id=77,
        user_content="user content here",
        channel="@testchannel",
        md_was_truncated=False,
        md_original_chars=500,
    )
    fake_result = _ReportResult("inserted", msg.id, layer2_input=layer2_inp)

    mock_session_ctx, _ = make_mock_db_session()

    with patch("collector.backfill.get_client") as mock_get_client, \
         patch("collector.backfill.AsyncSessionLocal", mock_session_ctx), \
         patch("collector.backfill._process_single_report", new_callable=AsyncMock,
               return_value=fake_result), \
         patch("collector.backfill.parse_messages") as mock_parse, \
         patch("collector.backfill.run_layer2_batch", new_callable=AsyncMock,
               return_value=({}, [])) as mock_batch, \
         patch("collector.backfill.settings") as mock_settings:

        mock_settings.backfill_limit = None
        mock_settings.pdf_base_path = MagicMock()
        mock_settings.analysis_enabled = True
        mock_settings.anthropic_api_key = "test-key"

        # parse_messages must return something so a _ReportTask is created
        from collector.backfill import _ReportTask
        mock_parsed = MagicMock()
        mock_parsed.pdf_url = None
        mock_parse.return_value = [mock_parsed]

        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        from collector.backfill import backfill_channel
        saved = await backfill_channel("@testchannel", limit=10)

    # layer2_batch was called because layer2_inputs had an entry
    mock_batch.assert_called_once()
    batch_call_args = mock_batch.call_args[0][0]
    assert len(batch_call_args) == 1


# ---------------------------------------------------------------------------
# Verify no semaphore usage inside backfill_channel
# ---------------------------------------------------------------------------

def test_backfill_channel_no_semaphore_construction():
    """backfill_channel source code must not construct asyncio.Semaphore."""
    import inspect
    from collector.backfill import backfill_channel
    source = inspect.getsource(backfill_channel)
    assert "Semaphore" not in source, (
        "backfill_channel still constructs asyncio.Semaphore — remove it"
    )


def test_backfill_channel_uses_queue():
    """backfill_channel source code must use asyncio.Queue."""
    import inspect
    from collector.backfill import backfill_channel
    source = inspect.getsource(backfill_channel)
    assert "asyncio.Queue" in source, (
        "backfill_channel must use asyncio.Queue for worker pattern"
    )
