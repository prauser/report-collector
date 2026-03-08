# STEP 05 — DB upsert + 중복 제거

## 목표
- `upsert_report()` 로직 완성 및 edge case 검증
- ON CONFLICT 동작 확인 (expression index와 연동)
- 저장 통계 로깅 확인

## 사전 조건
- STEP 01 완료 (uix_report_dedup 인덱스 존재)
- STEP 04 완료 (ParsedReport 구조 확정)

## 핵심 이슈: ON CONFLICT + expression index

PostgreSQL에서 expression index를 `ON CONFLICT` 타겟으로 쓰려면
`constraint=` (이름) 방식이 필요한데, **expression index는 constraint가 아님**.

해결 방법: 마이그레이션에서 `UNIQUE` constraint로 만들거나, 아래처럼 처리.

### 옵션 A (권장): partial unique constraint로 변환

```sql
-- 마이그레이션에서
ALTER TABLE reports ADD CONSTRAINT uix_report_dedup
UNIQUE NULLS NOT DISTINCT (broker, report_date, analyst, stock_name, title_normalized);
-- PostgreSQL 15+에서 NULLS NOT DISTINCT 지원 → NULL을 동일 값으로 취급
```

```python
# upsert에서
stmt = insert(Report).values(**values).on_conflict_do_update(
    constraint="uix_report_dedup",
    set_=update_set,
)
```

### 옵션 B: SELECT → INSERT/UPDATE 분리

```python
# expression index는 유지하되 ON CONFLICT 대신 명시적 체크
existing = await session.scalar(
    select(Report).where(
        Report.broker == values["broker"],
        Report.report_date == values["report_date"],
        func.coalesce(Report.analyst, '') == func.coalesce(values.get("analyst"), ''),
        func.coalesce(Report.stock_name, '') == func.coalesce(values.get("stock_name"), ''),
        Report.title_normalized == values["title_normalized"],
    )
)
if existing:
    # UPDATE
else:
    # INSERT
```

**→ 옵션 A (NULLS NOT DISTINCT)가 PostgreSQL 15에서 깔끔하게 지원됨. 이걸로 진행.**

## 구현 대상

### db/migrations/versions/xxxx_initial.py 수정

```python
# expression index 대신 constraint로 변경
op.execute("""
    ALTER TABLE reports ADD CONSTRAINT uix_report_dedup
    UNIQUE NULLS NOT DISTINCT (broker, report_date, analyst, stock_name, title_normalized)
""")
```

### storage/report_repo.py 완성

```python
async def upsert_report(session: AsyncSession, parsed: ParsedReport) -> tuple[Report | None, str]:
    if not parsed.title_normalized:
        log.warning("missing_title_normalized", title=parsed.title[:50])
        return None, "skipped"

    broker = parsed.broker or parsed.source_channel  # broker 없으면 채널명으로 대체

    values = { ... }  # 현재 코드 유지

    stmt = (
        insert(Report)
        .values(**values)
        .on_conflict_do_update(
            constraint="uix_report_dedup",
            set_={
                # pdf_url, opinion 등 추가 정보가 있을 때만 업데이트
                "pdf_url": case(
                    (stmt.excluded.pdf_url.isnot(None), stmt.excluded.pdf_url),
                    else_=Report.pdf_url,
                ),
                "opinion": case(
                    (stmt.excluded.opinion.isnot(None), stmt.excluded.opinion),
                    else_=Report.opinion,
                ),
                "target_price": case(
                    (stmt.excluded.target_price.isnot(None), stmt.excluded.target_price),
                    else_=Report.target_price,
                ),
                "raw_text": stmt.excluded.raw_text,
                "source_channel": stmt.excluded.source_channel,  # 마지막 수집 채널 기록
            }
        )
        .returning(Report.id, Report.created_at, Report.updated_at)
    )

    row = (await session.execute(stmt)).one_or_none()
    await session.commit()

    if row is None:
        return None, "skipped"

    action = "inserted" if row.created_at == row.updated_at else "updated"
    return row, action
```

## 테스트 코드

### tests/test_storage.py

```python
"""storage/report_repo.py 통합 테스트."""
import pytest
from datetime import date
from parser.base import ParsedReport


def make_parsed(**kwargs) -> ParsedReport:
    defaults = dict(
        title="테스트리포트",
        title_normalized="테스트리포트",
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
        report, action = await upsert_report(session, make_parsed(title_normalized="신규리포트abc"))
    assert action == "inserted"
    assert report is not None


@pytest.mark.asyncio
async def test_duplicate_is_skipped():
    from db.session import AsyncSessionLocal
    from storage.report_repo import upsert_report

    parsed = make_parsed(title_normalized="중복테스트xyz", pdf_url=None, opinion=None)

    async with AsyncSessionLocal() as session:
        _, a1 = await upsert_report(session, parsed)
        _, a2 = await upsert_report(session, parsed)

    assert a1 == "inserted"
    assert a2 in ("updated", "skipped")  # 변경 없으면 updated or skipped


@pytest.mark.asyncio
async def test_cross_channel_updates_pdf_url():
    """두 번째 채널에서 같은 리포트 + PDF URL 있으면 업데이트."""
    from db.session import AsyncSessionLocal
    from storage.report_repo import upsert_report
    from db.models import Report
    from sqlalchemy import select

    key = "crosschannel테스트"

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

    key = "산업리포트중복테스트"

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
    assert a2 != "inserted"  # 중복으로 처리


@pytest.mark.asyncio
async def test_missing_title_normalized_skipped():
    """title_normalized 없으면 저장 건너뜀."""
    from db.session import AsyncSessionLocal
    from storage.report_repo import upsert_report

    async with AsyncSessionLocal() as session:
        report, action = await upsert_report(session, make_parsed(title_normalized=None))

    assert action == "skipped"
    assert report is None
```

### 실행

```bash
pytest tests/test_storage.py -v
```

## 검증 체크리스트

- [ ] 마이그레이션에 `NULLS NOT DISTINCT` constraint 적용
- [ ] 신규 삽입 PASS
- [ ] 동일 키 중복 삽입 시 skipped/updated PASS
- [ ] 크로스 채널 PDF URL 업데이트 PASS
- [ ] analyst=None, stock_name=None 중복 처리 PASS
- [ ] pytest 모두 PASS

## 완료 기준 → STEP 06 진입

체크리스트 통과 시.

## 이슈/메모

- `NULLS NOT DISTINCT`는 PostgreSQL 15+. docker-compose에서 `postgres:15` 사용 중이므로 OK
- `case()` import: `from sqlalchemy import case`
- returning().one_or_none() 이 None이 되는 케이스: DO NOTHING이 발동된 경우. DO UPDATE는 항상 RETURNING이 있음
