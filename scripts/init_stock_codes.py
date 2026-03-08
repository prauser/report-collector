"""KRX 상장 종목 초기 로드.

pykrx 라이브러리 사용: pip install pykrx
"""
import asyncio
import structlog
from datetime import date

from sqlalchemy.dialects.postgresql import insert
from db.models import StockCode
from db.session import AsyncSessionLocal
from parser.normalizer import normalize_stock_name

log = structlog.get_logger(__name__)


async def load_stock_codes() -> None:
    try:
        from pykrx import stock as krx
    except ImportError:
        log.error("pykrx_not_installed", hint="pip install pykrx")
        return

    today = date.today().strftime("%Y%m%d")
    records: list[dict] = []

    for market in ("KOSPI", "KOSDAQ"):
        tickers = krx.get_market_ticker_list(today, market=market)
        for code in tickers:
            name = krx.get_market_ticker_name(code)
            records.append({
                "code": code,
                "name": name,
                "name_normalized": normalize_stock_name(name),
                "market": market,
                "is_active": True,
            })

    log.info("stock_codes_loaded", count=len(records))

    async with AsyncSessionLocal() as session:
        stmt = insert(StockCode).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["code"],
            set_={"name": stmt.excluded.name, "name_normalized": stmt.excluded.name_normalized, "is_active": True},
        )
        await session.execute(stmt)
        await session.commit()

    log.info("stock_codes_saved", count=len(records))


if __name__ == "__main__":
    asyncio.run(load_stock_codes())
