"""수집기 테스트 - Telethon mock 사용."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, datetime, timezone


def make_mock_message(text: str, msg_id: int = 1, dt: datetime = None):
    from telethon.tl.types import Message

    msg = MagicMock(spec=Message)
    msg.text = text
    msg.id = msg_id
    msg.date = dt or datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc)
    return msg


@pytest.mark.asyncio
async def test_backfill_saves_parsed_messages():
    """백필 시 파싱된 메시지가 DB에 저장되는지."""
    sample_text = (
        "▶ 삼성전자(005930) 반도체 업황 개선 - 미래에셋증권\n"
        "https://example.com/report.pdf\n"
        "- 목표가: 85,000원 (매수)"
    )
    mock_messages = [make_mock_message(sample_text, msg_id=100)]

    async def fake_iter(*args, **kwargs):
        for m in mock_messages:
            yield m

    with patch("collector.backfill.get_client") as mock_get_client, \
         patch("collector.backfill.upsert_report", new_callable=AsyncMock) as mock_upsert, \
         patch("collector.backfill.stock_mapper") as mock_mapper:

        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        mock_upsert.return_value = (MagicMock(id=1, pdf_url=None), "inserted")
        mock_mapper.get_code = AsyncMock(return_value="005930")

        from collector.backfill import backfill_channel
        saved = await backfill_channel("@repostory123", limit=10)

    assert saved == 1
    assert mock_upsert.called


@pytest.mark.asyncio
async def test_backfill_skips_empty_messages():
    """텍스트 없는 메시지는 건너뜀."""
    msg = make_mock_message("")

    async def fake_iter(*args, **kwargs):
        for m in [msg]:
            yield m

    with patch("collector.backfill.get_client") as mock_get_client, \
         patch("collector.backfill.upsert_report", new_callable=AsyncMock) as mock_upsert:

        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        from collector.backfill import backfill_channel
        saved = await backfill_channel("@repostory123", limit=10)

    assert saved == 0
    assert not mock_upsert.called


@pytest.mark.asyncio
async def test_flood_wait_handled():
    """FloodWaitError 발생 시 sleep 후 계속."""
    from telethon.errors import FloodWaitError

    async def fake_iter(*args, **kwargs):
        raise FloodWaitError(request=None, capture=1)
        yield  # make it a generator

    with patch("collector.backfill.get_client") as mock_get_client, \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        from collector.backfill import backfill_channel
        saved = await backfill_channel("@repostory123", limit=10)

    assert saved == 0
