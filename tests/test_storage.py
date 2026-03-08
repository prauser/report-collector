"""storage/report_repo.py 통합 테스트."""
import pytest
import uuid
from datetime import date
from parser.base import ParsedReport


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def make_parsed(**kwargs) -> ParsedReport:
    defaults = dict(
        title="테스트리포트",
        title_normalized=f"테스트리포트_{_uid()}",
        broker="테스트증권",
        report_date=date(2026, 3, 8),
        source_channel="@test",
        raw_text="raw text",
    )
    defaults.update(kwargs)
    return ParsedReport(**defaults)


@pytest.mark.asyncio
async def test_insert_new_report():
    from db.session import AsyncSessionLocal
    from storage.report_repo import upsert_report

    async with AsyncSessionLocal() as session:
        report, action = await upsert_report(session, make_parsed(title_normalized=f"신규리포트_{_uid()}"))
    assert action == "inserted"
    assert report is not None


@pytest.mark.asyncio
async def test_duplicate_is_skipped():
    from db.session import AsyncSessionLocal
    from storage.report_repo import upsert_report

    key = f"중복테스트_{_uid()}"
    parsed = make_parsed(title_normalized=key, pdf_url=None, opinion=None)

    async with AsyncSessionLocal() as session:
        _, a1 = await upsert_report(session, parsed)
        _, a2 = await upsert_report(session, parsed)

    assert a1 == "inserted"
    assert a2 in ("updated", "skipped")


@pytest.mark.asyncio
async def test_cross_channel_updates_pdf_url():
    """두 번째 채널에서 같은 리포트 + PDF URL 있으면 업데이트."""
    from db.session import AsyncSessionLocal
    from storage.report_repo import upsert_report
    from db.models import Report
    from sqlalchemy import select

    key = f"crosschannel_{_uid()}"

    async with AsyncSessionLocal() as session:
        _, _ = await upsert_report(session, make_parsed(
            title_normalized=key,
            source_channel="@channel_a",
            pdf_url=None,
        ))
        _, action = await upsert_report(session, make_parsed(
            title_normalized=key,
            source_channel="@channel_b",
            pdf_url="https://example.com/report.pdf",
        ))

    assert action == "updated"

    async with AsyncSessionLocal() as session:
        report = await session.scalar(
            select(Report).where(Report.title_normalized == key)
        )
    assert report.pdf_url == "https://example.com/report.pdf"


@pytest.mark.asyncio
async def test_null_analyst_null_stock_dedup():
    """analyst=None, stock_name=None 인 산업 리포트 중복 처리."""
    from db.session import AsyncSessionLocal
    from storage.report_repo import upsert_report

    key = f"산업리포트_{_uid()}"

    async with AsyncSessionLocal() as session:
        _, a1 = await upsert_report(session, make_parsed(
            title_normalized=key,
            analyst=None,
            stock_name=None,
        ))
        _, a2 = await upsert_report(session, make_parsed(
            title_normalized=key,
            analyst=None,
            stock_name=None,
        ))

    assert a1 == "inserted"
    assert a2 != "inserted"


@pytest.mark.asyncio
async def test_missing_title_normalized_skipped():
    """title_normalized 없으면 저장 건너뜀."""
    from db.session import AsyncSessionLocal
    from storage.report_repo import upsert_report

    async with AsyncSessionLocal() as session:
        report, action = await upsert_report(session, make_parsed(title_normalized=None))

    assert action == "skipped"
    assert report is None
