"""종목코드 로드 및 매핑 테스트."""
import pytest

import storage.stock_mapper as mapper
from parser.normalizer import normalize_stock_name


@pytest.mark.asyncio
async def test_stock_mapper_get_code():
    """캐시가 채워진 상태에서 조회."""
    mapper._loaded = True
    mapper._cache.clear()
    mapper._cache["삼성전자"] = "005930"
    mapper._cache["sk하이닉스"] = "000660"

    result = await mapper.get_code("삼성전자")
    assert result == "005930"


@pytest.mark.asyncio
async def test_stock_mapper_normalize():
    """띄어쓰기/괄호 있어도 매핑."""
    mapper._loaded = True
    mapper._cache.clear()
    mapper._cache[normalize_stock_name("SK하이닉스")] = "000660"

    assert await mapper.get_code("SK하이닉스") == "000660"
    assert await mapper.get_code("SK 하이닉스") == "000660"


@pytest.mark.asyncio
async def test_stock_mapper_unknown():
    mapper._loaded = True
    mapper._cache.clear()
    result = await mapper.get_code("존재하지않는종목xyz")
    assert result is None


@pytest.mark.asyncio
async def test_init_stock_codes_db():
    """DB에 종목코드 저장 후 조회 (통합 테스트)."""
    from scripts.init_stock_codes import load_stock_codes
    await load_stock_codes()

    from db.session import AsyncSessionLocal
    from db.models import StockCode
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(StockCode))
    # 시드 데이터 또는 KRX 데이터가 로드되었는지 확인
    assert count >= 30, f"종목 수 이상: {count}"


@pytest.mark.asyncio
async def test_stock_mapper_load_from_db():
    """DB에서 캐시 로드 후 매핑 동작 확인."""
    import storage.stock_mapper as m
    m._loaded = False
    m._cache.clear()
    await m.load_cache()
    assert m._loaded is True
    # 시드 데이터에 삼성전자가 있어야 함
    result = await m.get_code("삼성전자")
    assert result == "005930"
