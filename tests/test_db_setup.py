"""STEP 01 - DB 스키마 검증 테스트."""
import asyncio
import uuid
import pytest
from sqlalchemy import text


async def test_tables_exist(engine):
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        ))
        tables = {row[0] for row in result}
    assert "reports" in tables
    assert "stock_codes" in tables
    assert "channels" in tables


async def test_unique_constraint_exists(engine):
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='reports'::regclass AND contype='u'"
        ))
        names = {row[0] for row in result}
    assert "uix_report_dedup" in names, f"uix_report_dedup 없음. 존재: {names}"


async def test_dedup_constraint_rejects_duplicate(session_factory):
    """같은 키로 두 번 INSERT 시 두 번째는 무시."""
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy import select, func
    from db.models import Report
    from datetime import date

    key = "dedup_test_001"
    values = dict(
        broker="테스트증권",
        report_date=date(2026, 1, 1),
        analyst=None,
        stock_name=None,
        title="중복테스트제목",
        title_normalized=key,
        source_channel="@test",
        raw_text="raw",
    )

    async with session_factory() as session:
        stmt = insert(Report).values(**values).on_conflict_do_nothing(
            constraint="uix_report_dedup"
        )
        await session.execute(stmt)
        await session.execute(stmt)
        await session.commit()

        count = await session.scalar(
            select(func.count()).where(Report.title_normalized == key)
        )
    assert count == 1, f"중복 제거 실패: {count}건"


async def test_null_dedup_treated_as_equal(session_factory):
    """analyst=None, stock_name=None 두 건도 중복으로 처리 (NULLS NOT DISTINCT)."""
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy import select, func
    from db.models import Report
    from datetime import date

    key = "null_dedup_test_001"
    values = dict(
        broker="널테스트증권",
        report_date=date(2026, 1, 2),
        analyst=None,
        stock_name=None,
        title="널중복테스트",
        title_normalized=key,
        source_channel="@test",
        raw_text="raw",
    )

    async with session_factory() as session:
        stmt = insert(Report).values(**values).on_conflict_do_nothing(
            constraint="uix_report_dedup"
        )
        await session.execute(stmt)
        await session.execute(stmt)
        await session.commit()

        count = await session.scalar(
            select(func.count()).where(Report.title_normalized == key)
        )
    assert count == 1, f"NULL 중복 제거 실패: {count}건"


async def test_updated_at_trigger(session_factory):
    """updated_at 트리거 동작 확인."""
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy import select, update
    from db.models import Report
    from datetime import date

    key = f"trigger_test_{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        stmt = insert(Report).values(
            broker="트리거테스트증권",
            report_date=date(2026, 1, 3),
            title="트리거테스트",
            title_normalized=key,
            source_channel="@test",
            raw_text="raw",
        ).returning(Report)
        row = (await session.execute(stmt)).scalar_one()
        await session.commit()
        created_updated_at = row.updated_at

    await asyncio.sleep(0.5)

    async with session_factory() as session:
        await session.execute(
            update(Report)
            .where(Report.title_normalized == key)
            .values(opinion="매수")
        )
        await session.commit()

        refreshed = await session.scalar(
            select(Report).where(Report.title_normalized == key)
        )
    assert refreshed.updated_at > created_updated_at, "updated_at 트리거 미동작"
