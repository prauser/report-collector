# STEP 01 — DB 모델 + Alembic 마이그레이션

## 목표
- SQLAlchemy 모델 확정 (expression index 포함)
- Alembic 초기 마이그레이션 생성 및 적용
- docker-compose PostgreSQL 정상 구동 확인

## 사전 조건
- Docker 실행 중
- `config/.env` 작성 완료 (.env.example 참고)
- Python venv 생성 + requirements.txt 설치

## 환경 세팅 순서

```bash
cd report-collector
python -m venv .venv
source .venv/Scripts/activate   # Windows
pip install -r requirements.txt

docker-compose up -d
```

## 수정 대상 파일

### db/models.py
현재 `UniqueConstraint("uix_report_dedup")`은 expression index를 지원 못하므로 제거.
모델에서는 단순 컬럼 정의만 유지.

```python
# __table_args__에서 아래 제거
UniqueConstraint("broker", "report_date", "title_normalized", name="uix_report_dedup"),
```

### db/migrations/ 초기화

```bash
alembic -c db/migrations/alembic.ini init db/migrations
# 이미 env.py 있으므로 versions/ 폴더만 생성되면 됨
mkdir db/migrations/versions

alembic -c db/migrations/alembic.ini revision --autogenerate -m "initial"
```

### 생성된 versions/xxxx_initial.py 에 추가

autogenerate 후 파일 열어서 upgrade() 안에 expression index 수동 추가:

```python
def upgrade() -> None:
    # ... (자동 생성된 create_table 구문들) ...

    # expression index (COALESCE로 NULL 처리)
    op.execute("""
        CREATE UNIQUE INDEX uix_report_dedup
        ON reports (
            broker,
            report_date,
            COALESCE(analyst, ''),
            COALESCE(stock_name, ''),
            title_normalized
        )
    """)

    # updated_at 자동 갱신 트리거
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_reports_updated_at
        BEFORE UPDATE ON reports
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_reports_updated_at ON reports")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at")
    op.execute("DROP INDEX IF EXISTS uix_report_dedup")
    # ... (자동 생성된 drop_table 구문들) ...
```

```bash
alembic -c db/migrations/alembic.ini upgrade head
```

## 테스트 코드

### tests/test_db_setup.py

```python
"""DB 스키마 검증 테스트."""
import asyncio
import pytest
from sqlalchemy import text
from db.session import engine


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
async def test_tables_exist():
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
        """))
        tables = {row[0] for row in result}
    assert "reports" in tables
    assert "stock_codes" in tables
    assert "channels" in tables


@pytest.mark.asyncio
async def test_unique_index_exists():
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'reports' AND indexname = 'uix_report_dedup'
        """))
        rows = result.fetchall()
    assert len(rows) == 1, "uix_report_dedup 인덱스가 없음"


@pytest.mark.asyncio
async def test_dedup_index_rejects_duplicate():
    """같은 키로 두 번 INSERT 시 두 번째는 무시되어야 함."""
    from sqlalchemy.dialects.postgresql import insert
    from db.models import Report
    from db.session import AsyncSessionLocal
    from datetime import date

    values = dict(
        broker="테스트증권",
        report_date=date(2026, 1, 1),
        analyst=None,
        stock_name=None,
        title="테스트제목",
        title_normalized="테스트제목",
        source_channel="@test",
        raw_text="raw",
    )

    async with AsyncSessionLocal() as session:
        stmt = insert(Report).values(**values).on_conflict_do_nothing()
        await session.execute(stmt)
        await session.execute(stmt)  # 두 번째 - 무시
        await session.commit()

        from sqlalchemy import select, func
        count = await session.scalar(
            select(func.count()).where(
                Report.broker == "테스트증권",
                Report.title_normalized == "테스트제목",
            )
        )
    assert count == 1, f"중복 제거 실패: {count}건 저장됨"


@pytest.mark.asyncio
async def test_updated_at_trigger():
    """updated_at 트리거 동작 확인."""
    from db.models import Report
    from db.session import AsyncSessionLocal
    from sqlalchemy import select, update
    from datetime import date
    import asyncio

    async with AsyncSessionLocal() as session:
        # 삽입
        from sqlalchemy.dialects.postgresql import insert
        stmt = insert(Report).values(
            broker="트리거테스트",
            report_date=date(2026, 1, 2),
            title="트리거테스트제목",
            title_normalized="트리거테스트제목",
            source_channel="@test",
            raw_text="raw",
        ).returning(Report)
        row = (await session.execute(stmt)).scalar_one()
        await session.commit()
        created = row.updated_at

        await asyncio.sleep(0.1)

        await session.execute(
            update(Report).where(Report.id == row.id).values(opinion="매수")
        )
        await session.commit()

        refreshed = await session.get(Report, row.id)
        assert refreshed.updated_at > created, "updated_at 트리거 미동작"
```

### 실행

```bash
pip install pytest pytest-asyncio
pytest tests/test_db_setup.py -v
```

## 검증 체크리스트

- [ ] `docker-compose up -d` 후 PostgreSQL 컨테이너 healthy
- [ ] `alembic upgrade head` 성공
- [ ] `psql`로 접속해서 3개 테이블 확인
- [ ] `uix_report_dedup` 인덱스 존재 확인
- [ ] pytest 3개 테스트 모두 PASS

## 완료 기준 → STEP 02 진입

모든 체크리스트 통과 시.

## 이슈/메모

- `updated_at` onupdate는 SQLAlchemy가 Python 레벨에서만 처리함. DB 트리거도 같이 걸어두면 raw SQL UPDATE에도 반영됨 → 마이그레이션에 트리거 추가 권장
- asyncpg는 `TIMESTAMPTZ` 반환 시 timezone-aware datetime 반환. 비교 시 주의
