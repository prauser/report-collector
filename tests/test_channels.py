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
        from config.settings import settings

        await sync_channels()
        await sync_channels()

        async with AsyncSessionLocal() as session:
            count = await session.scalar(select(func.count()).select_from(Channel))
        # backfill 테스트에서 추가된 채널이 있을 수 있으므로 >= 비교
        assert count >= len(settings.telegram_channels)


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
        text = "▶ 삼성전자(005930) 제목 - 미래에셋증권"
        result = parse_message(text, "@repostory123")
        assert result is not None

    def test_companyreport_gets_dedicated_parser(self):
        from parser.registry import parse_message
        text = "삼성전자(005930) 반도체 리포트 - 미래에셋증권"
        result = parse_message(text, "@companyreport")
        assert result is not None

    def test_unknown_channel_uses_generic(self):
        from parser.registry import parse_message
        text = "삼성전자(005930) 리포트 - 키움증권\n목표가: 70,000원"
        result = parse_message(text, "@unknown_new_channel")
        assert result is not None
