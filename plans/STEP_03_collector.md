# STEP 03 — Telethon 수집기

## 목표
- Telethon 클라이언트 인증 및 세션 관리
- @repostory123 히스토리 백필 동작 확인
- 실시간 리스너 동작 확인

## 사전 조건
- STEP 01, 02 완료
- `config/.env`에 TELEGRAM_API_ID, TELEGRAM_API_HASH 설정 완료
- Telegram 계정 최초 인증 1회 필요

## 최초 인증 절차

```bash
python -c "
from telethon import TelegramClient
from config.settings import settings
import asyncio

async def auth():
    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.start()   # 전화번호/인증코드 입력 프롬프트
    print('인증 완료. 세션 파일 생성됨.')
    await client.disconnect()

asyncio.run(auth())
"
```

→ `report_collector.session` 파일 생성됨. 이후 재인증 불필요.

## 구현 대상

### collector/backfill.py 보완

현재 코드에서 수정 포인트:
1. `message.date` (Telethon UTC datetime)를 parsed.report_date로 전달
2. FloodWaitError 처리
3. channels 테이블 last_message_id 업데이트 로직 버그 수정 (루프 변수 범위 문제)

```python
import asyncio
from telethon.errors import FloodWaitError
from telethon.tl.types import Message

async def backfill_channel(channel_username: str, limit: int | None = None) -> int:
    client = get_client()
    saved = 0
    last_id = 0

    async with AsyncSessionLocal() as session:
        channel_row = await session.scalar(
            select(Channel).where(Channel.channel_username == channel_username)
        )
        min_id = channel_row.last_message_id if channel_row else 0

    effective_limit = limit or settings.backfill_limit or None

    try:
        async for message in client.iter_messages(
            channel_username,
            limit=effective_limit,
            min_id=min_id or 0,
            reverse=True,
        ):
            if not isinstance(message, Message) or not message.text:
                continue

            parsed = parse_message(message.text, channel_username, message_id=message.id)
            if parsed is None:
                continue

            # message.date (UTC aware datetime) → report_date로 사용
            if parsed.report_date is None or parsed.report_date == date.today():
                parsed.report_date = message.date.date()

            # stock_code 보완
            if parsed.stock_name and not parsed.stock_code:
                parsed.stock_code = await stock_mapper.get_code(parsed.stock_name)

            async with AsyncSessionLocal() as session:
                _, action = await upsert_report(session, parsed)
                if action == "inserted":
                    saved += 1

            last_id = message.id

    except FloodWaitError as e:
        log.warning("flood_wait", seconds=e.seconds, channel=channel_username)
        await asyncio.sleep(e.seconds)

    # last_message_id 업데이트
    if last_id:
        async with AsyncSessionLocal() as session:
            if channel_row:
                channel_row.last_message_id = last_id
                session.add(channel_row)
            else:
                session.add(Channel(
                    channel_username=channel_username,
                    last_message_id=last_id,
                ))
            await session.commit()

    log.info("backfill_done", channel=channel_username, saved=saved)
    return saved
```

### collector/listener.py 보완

- stock_code 보완 추가
- message.date → report_date 사용

## 테스트 코드

### tests/test_collector.py

```python
"""수집기 테스트 - Telethon mock 사용."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, AsyncIterator
from datetime import date, datetime, timezone


def make_mock_message(text: str, msg_id: int = 1, dt: datetime = None):
    msg = MagicMock()
    msg.text = text
    msg.id = msg_id
    msg.date = dt or datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc)
    return msg


@pytest.mark.asyncio
async def test_backfill_saves_parsed_messages():
    """백필 시 파싱된 메시지가 DB에 저장되는지."""
    sample_text = "▶ 삼성전자(005930) 반도체 업황 개선 - 미래에셋증권\nhttps://example.com/report.pdf\n- 목표가: 85,000원 (매수)"

    mock_messages = [make_mock_message(sample_text, msg_id=100)]

    async def fake_iter(*args, **kwargs):
        for m in mock_messages:
            yield m

    with patch("collector.backfill.get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        with patch("collector.backfill.upsert_report", new_callable=AsyncMock) as mock_upsert:
            mock_upsert.return_value = (MagicMock(id=1, pdf_url=None), "inserted")

            from collector.backfill import backfill_channel
            saved = await backfill_channel("@repostory123", limit=10)

    assert saved == 1
    assert mock_upsert.called


@pytest.mark.asyncio
async def test_backfill_skips_empty_messages():
    """텍스트 없는 메시지(사진 등)는 건너뜀."""
    msg = make_mock_message("")

    async def fake_iter(*args, **kwargs):
        yield msg

    with patch("collector.backfill.get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        with patch("collector.backfill.upsert_report", new_callable=AsyncMock) as mock_upsert:
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
        yield  # generator

    with patch("collector.backfill.get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.iter_messages = fake_iter
        mock_get_client.return_value = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock):
            from collector.backfill import backfill_channel
            saved = await backfill_channel("@repostory123", limit=10)

    assert saved == 0  # 에러 없이 0 반환
```

### 실행

```bash
pytest tests/test_collector.py -v

# 실제 동작 확인 (소량만)
python -c "
import asyncio
from collector.backfill import backfill_channel
from collector.telegram_client import get_client

async def main():
    client = get_client()
    await client.start()
    saved = await backfill_channel('@repostory123', limit=20)
    print(f'saved: {saved}')
    await client.disconnect()

asyncio.run(main())
"
```

## 검증 체크리스트

- [ ] `.session` 파일 생성 (최초 인증 완료)
- [ ] `limit=20` 백필 실행 시 reports 테이블에 데이터 적재
- [ ] 이미 수집한 메시지 재실행 시 중복 저장 안 됨
- [ ] FloodWaitError mock 테스트 PASS
- [ ] pytest 모두 PASS

## 완료 기준 → STEP 04 진입

체크리스트 통과 시.

## 이슈/메모

- Telethon `iter_messages`는 기본적으로 최신 → 오래된 순. `reverse=True`로 오래된 것부터 받아야 last_message_id 업데이트가 안전
- `min_id=0`이면 전체 히스토리. 처음엔 `limit=100` 정도로 테스트 권장
- 세션 파일이 없으면 인증 프롬프트 뜸 → headless 서버 운영 시 StringSession 전환 필요 (추후)
