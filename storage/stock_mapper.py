"""종목명 → 종목코드 매핑 캐시."""
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
