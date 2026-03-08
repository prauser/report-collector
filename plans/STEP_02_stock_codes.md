# STEP 02 — 종목코드 초기 로드 (KRX)

## 목표
- pykrx로 KOSPI/KOSDAQ 종목 전체 로드 → stock_codes 테이블 저장
- 종목명 → 코드 매핑 함수 구현 및 캐시 전략 확정

## 사전 조건
- STEP 01 완료 (DB 테이블 존재)

## 추가 패키지

```bash
pip install pykrx
```

requirements.txt에도 추가:
```
pykrx==1.0.47
```

## 구현 대상

### scripts/init_stock_codes.py
현재 코드에서 보완:
- 섹터 정보도 함께 로드 (pykrx `get_market_sector_classifications`)
- name_normalized 생성

### storage/stock_mapper.py (신규)

```python
"""종목명 → 종목코드 매핑 캐시."""
import asyncio
from functools import lru_cache
from sqlalchemy import select
from db.models import StockCode
from db.session import AsyncSessionLocal
from parser.normalizer import normalize_stock_name

_cache: dict[str, str] = {}  # name_normalized → code
_loaded = False


async def load_cache() -> None:
    global _loaded
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(StockCode.name_normalized, StockCode.code)
            .where(StockCode.is_active.is_(True))
        )).all()
    _cache.clear()
    for name_norm, code in rows:
        if name_norm:
            _cache[name_norm] = code
    _loaded = True


async def get_code(stock_name: str) -> str | None:
    if not _loaded:
        await load_cache()
    return _cache.get(normalize_stock_name(stock_name))


async def reload_cache() -> None:
    """stock_codes 갱신 후 호출."""
    global _loaded
    _loaded = False
    await load_cache()
```

### parser/repostory.py 수정
파싱 후 stock_mapper를 통해 코드 보완:
- 이미 텍스트에 `(005930)` 형식이 있으면 그대로 사용
- 없으면 stock_mapper로 조회

**단, 파서는 순수 파싱만 담당 (DB 조회 없음)**
→ stock_mapper 조회는 `listener.py` / `backfill.py` 에서 파싱 후 보완

```python
# listener.py에서
parsed = parse_message(text, channel, message_id)
if parsed and parsed.stock_name and not parsed.stock_code:
    parsed.stock_code = await stock_mapper.get_code(parsed.stock_name)
```

## 테스트 코드

### tests/test_stock_codes.py

```python
"""종목코드 로드 및 매핑 테스트."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_stock_mapper_get_code():
    """캐시가 채워진 상태에서 조회."""
    from storage.stock_mapper import get_code, _cache
    _cache.clear()
    _cache["삼성전자"] = "005930"
    _cache["sk하이닉스"] = "000660"

    result = await get_code("삼성전자")
    assert result == "005930"


@pytest.mark.asyncio
async def test_stock_mapper_normalize():
    """띄어쓰기/괄호 있어도 매핑."""
    from storage.stock_mapper import get_code, _cache
    from parser.normalizer import normalize_stock_name
    _cache.clear()
    _cache[normalize_stock_name("SK하이닉스")] = "000660"

    assert await get_code("SK하이닉스") == "000660"
    assert await get_code("SK 하이닉스") == "000660"


@pytest.mark.asyncio
async def test_stock_mapper_unknown():
    from storage.stock_mapper import get_code, _cache
    _cache.clear()
    result = await get_code("존재하지않는종목xyz")
    assert result is None


@pytest.mark.asyncio
async def test_init_stock_codes_db(tmp_path):
    """실제 DB에 저장 후 조회 (통합 테스트 - pykrx 없으면 skip)."""
    pytest.importorskip("pykrx")

    from scripts.init_stock_codes import load_stock_codes
    await load_stock_codes()

    from db.session import AsyncSessionLocal
    from db.models import StockCode
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(StockCode))
    assert count > 2000, f"종목 수 이상: {count}"
```

### 실행

```bash
# 실제 KRX 로드 (인터넷 필요, 수십 초 소요)
python scripts/init_stock_codes.py

# 테스트
pytest tests/test_stock_codes.py -v
```

## 검증 체크리스트

- [ ] `init_stock_codes.py` 실행 후 stock_codes 테이블에 2500+ 건
- [ ] KOSPI / KOSDAQ 모두 포함 확인
- [ ] `get_code("삼성전자")` → "005930" 반환
- [ ] pytest 모두 PASS

## 완료 기준 → STEP 03 진입

체크리스트 통과 시.

## 이슈/메모

- pykrx는 영업일 기준으로 동작. 주말에 당일 날짜로 조회하면 에러날 수 있음 → 가장 최근 영업일로 fallback 처리 필요
- 종목명 동음이의 (예: "동양" - 여러 종목) → 완전일치 우선, 그래도 복수면 첫 번째 반환하거나 null 처리
- KRX 데이터는 주기적 갱신 필요 (상장폐지/신규상장) → 추후 cron 스크립트로
