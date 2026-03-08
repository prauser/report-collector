# STEP 07 — 추가 채널 확장

## 목표
- @companyreport, @searfin, @cb_eq_research 파서 추가
- channels 테이블 동기화 (config → DB)
- 파서 레지스트리에 등록

## 사전 조건
- STEP 06 완료
- 각 채널의 실제 메시지 샘플 수집 필요

## 채널별 샘플 수집

```python
# STEP 03의 dump_samples 함수를 채널별로 실행
for ch in ["@companyreport", "@searfin", "@cb_eq_research"]:
    await dump_samples(ch, f"tests/fixtures/{ch[1:]}_samples.json")
```

## 구현 대상

### parser/companyreport.py (신규)
샘플 분석 후 구현. 기본 구조:

```python
class CompanyReportParser(BaseParser):
    CHANNEL = "@companyreport"

    def can_parse(self, channel: str) -> bool:
        return channel.lower() == self.CHANNEL.lower()

    def parse(self, message_text: str, channel: str, message_id: int | None = None) -> ParsedReport | None:
        # 실제 샘플 기반으로 패턴 구현
        ...
```

### scripts/sync_channels.py (신규)

```python
"""config의 채널 목록을 channels 테이블과 동기화."""
import asyncio
from sqlalchemy.dialects.postgresql import insert
from db.models import Channel
from db.session import AsyncSessionLocal
from config.settings import settings

async def sync_channels() -> None:
    async with AsyncSessionLocal() as session:
        for username in settings.telegram_channels:
            stmt = insert(Channel).values(
                channel_username=username,
                is_active=True,
            ).on_conflict_do_nothing(index_elements=["channel_username"])
            await session.execute(stmt)
        await session.commit()
    print(f"채널 {len(settings.telegram_channels)}개 동기화 완료")

if __name__ == "__main__":
    asyncio.run(sync_channels())
```

### parser/registry.py 업데이트

```python
from parser.companyreport import CompanyReportParser
# from parser.searfin import SearfinParser

_PARSERS: list[BaseParser] = [
    RepostoryParser(),
    CompanyReportParser(),
    # SearfinParser(),
    GenericParser(),
]
```

## 테스트 코드

### tests/test_channels.py

```python
"""채널 파서 및 channels 테이블 테스트."""
import pytest
import json
from pathlib import Path


class TestChannelSync:

    @pytest.mark.asyncio
    async def test_sync_channels_creates_rows(self):
        from scripts.sync_channels import sync_channels
        from db.session import AsyncSessionLocal
        from db.models import Channel
        from sqlalchemy import select, func
        from config.settings import settings

        await sync_channels()

        async with AsyncSessionLocal() as session:
            count = await session.scalar(
                select(func.count()).select_from(Channel)
                .where(Channel.is_active.is_(True))
            )
        assert count >= len(settings.telegram_channels)

    @pytest.mark.asyncio
    async def test_sync_idempotent(self):
        """두 번 실행해도 중복 생성 없음."""
        from scripts.sync_channels import sync_channels
        from db.session import AsyncSessionLocal
        from db.models import Channel
        from sqlalchemy import select, func

        await sync_channels()
        await sync_channels()

        async with AsyncSessionLocal() as session:
            count = await session.scalar(select(func.count()).select_from(Channel))
        # 중복 없음 확인 (exact count)
        from config.settings import settings
        assert count == len(settings.telegram_channels)


class TestCompanyReportParser:
    """샘플 파일 있을 때만 실행."""

    @pytest.fixture
    def samples(self):
        path = Path("tests/fixtures/companyreport_samples.json")
        if not path.exists():
            pytest.skip("companyreport 샘플 없음")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_parse_rate(self, samples):
        from parser.companyreport import CompanyReportParser
        parser = CompanyReportParser()
        success = sum(
            1 for s in samples
            if parser.parse(s["text"], "@companyreport", s["id"]) is not None
        )
        rate = success / len(samples)
        print(f"\n@companyreport 파싱률: {rate:.1%}")
        assert rate >= 0.6


class TestRegistryRouting:
    """레지스트리가 채널에 맞는 파서를 선택하는지."""

    def test_repostory_gets_dedicated_parser(self):
        from parser.registry import parse_message
        from parser.repostory import RepostoryParser
        text = "▶ 삼성전자(005930) 제목 - 미래에셋증권"
        result = parse_message(text, "@repostory123")
        assert result is not None

    def test_unknown_channel_uses_generic(self):
        from parser.registry import parse_message
        text = "삼성전자(005930) 리포트 - 키움증권\n목표가: 70,000원"
        result = parse_message(text, "@unknown_new_channel")
        assert result is not None  # generic이 잡아줌
```

## 검증 체크리스트

- [ ] channels 테이블에 4개 채널 동기화
- [ ] sync 두 번 실행해도 중복 없음
- [ ] @companyreport 파싱률 60% 이상
- [ ] 레지스트리 라우팅 정상
- [ ] pytest 모두 PASS
- [ ] 전체 파이프라인 end-to-end 테스트: 4개 채널 backfill(limit=10) → reports 테이블 데이터 적재 확인

## 완료 기준 → 1차 완료

모든 체크리스트 통과 시 1차 목표 완성.

## 이슈/메모

- 채널별 메시지 형식이 샘플 없이는 파서 구현 불가. STEP 03 완료 후 샘플 수집이 선행 조건
- @searfin, @cb_eq_research는 공시 포함 가능 → 증권사 리포트만 필터링하는 로직 필요
- 최종 E2E 테스트: `python main.py`로 실시간 리스너 실행 후 새 메시지 수신 확인

---

## 1차 완료 후 체크

```bash
# 전체 테스트 한번에
pytest tests/ -v --tb=short

# DB 적재 현황
python -c "
import asyncio
from db.session import AsyncSessionLocal
from db.models import Report
from sqlalchemy import select, func

async def stats():
    async with AsyncSessionLocal() as s:
        total = await s.scalar(select(func.count()).select_from(Report))
        with_pdf = await s.scalar(select(func.count()).where(Report.pdf_path.isnot(None)))
        print(f'전체: {total}건, PDF 저장: {with_pdf}건')

asyncio.run(stats())
"
```
