"""KRX 상장 종목 초기 로드.

pykrx 라이브러리 사용: pip install pykrx
"""
import asyncio
import structlog
from datetime import date, timedelta

from sqlalchemy.dialects.postgresql import insert
from db.models import StockCode
from db.session import AsyncSessionLocal
from parser.normalizer import normalize_stock_name

log = structlog.get_logger(__name__)


def _find_valid_date(krx) -> str | None:
    """pykrx에서 데이터를 가져올 수 있는 최근 영업일을 찾는다. 최대 10일 전까지 시도."""
    d = date.today()
    for _ in range(10):
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        ds = d.strftime("%Y%m%d")
        tickers = krx.get_market_ticker_list(ds, market="KOSPI")
        if tickers:
            return ds
        d -= timedelta(days=1)
    return None


def _fetch_from_krx() -> list[dict]:
    """pykrx로 KRX에서 종목 데이터를 가져온다."""
    try:
        from pykrx import stock as krx
    except ImportError:
        log.error("pykrx_not_installed", hint="pip install pykrx")
        return []

    biz_date = _find_valid_date(krx)
    if not biz_date:
        log.warning("krx_no_valid_date", hint="pykrx에서 최근 10일간 유효한 데이터 없음")
        return []

    log.info("fetching_stock_codes", date=biz_date)
    records: list[dict] = []

    for market in ("KOSPI", "KOSDAQ"):
        tickers = krx.get_market_ticker_list(biz_date, market=market)
        for code in tickers:
            name = krx.get_market_ticker_name(code)
            records.append({
                "code": code,
                "name": name,
                "name_normalized": normalize_stock_name(name),
                "market": market,
                "is_active": True,
            })
    return records


def _seed_records() -> list[dict]:
    """KRX API 불가 시 주요 종목 시드 데이터."""
    seeds = [
        ("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI"),
        ("373220", "LG에너지솔루션", "KOSPI"), ("207940", "삼성바이오로직스", "KOSPI"),
        ("005380", "현대자동차", "KOSPI"), ("006400", "삼성SDI", "KOSPI"),
        ("051910", "LG화학", "KOSPI"), ("035420", "NAVER", "KOSPI"),
        ("000270", "기아", "KOSPI"), ("035720", "카카오", "KOSPI"),
        ("005490", "POSCO홀딩스", "KOSPI"), ("055550", "신한지주", "KOSPI"),
        ("105560", "KB금융", "KOSPI"), ("003670", "포스코퓨처엠", "KOSPI"),
        ("028260", "삼성물산", "KOSPI"), ("012330", "현대모비스", "KOSPI"),
        ("066570", "LG전자", "KOSPI"), ("003550", "LG", "KOSPI"),
        ("034730", "SK", "KOSPI"), ("032830", "삼성생명", "KOSPI"),
        ("086790", "하나금융지주", "KOSPI"), ("015760", "한국전력", "KOSPI"),
        ("017670", "SK텔레콤", "KOSPI"), ("030200", "KT", "KOSPI"),
        ("033780", "KT&G", "KOSPI"), ("010130", "고려아연", "KOSPI"),
        ("018260", "삼성에스디에스", "KOSPI"), ("011200", "HMM", "KOSPI"),
        ("034020", "두산에너빌리티", "KOSPI"), ("009150", "삼성전기", "KOSPI"),
        ("247540", "에코프로비엠", "KOSDAQ"), ("086520", "에코프로", "KOSDAQ"),
        ("041510", "에스엠", "KOSDAQ"), ("263750", "펄어비스", "KOSDAQ"),
        ("293490", "카카오게임즈", "KOSDAQ"), ("035900", "JYP Ent.", "KOSDAQ"),
        ("352820", "하이브", "KOSPI"), ("112040", "위메이드", "KOSDAQ"),
        ("003490", "대한항공", "KOSPI"), ("090430", "아모레퍼시픽", "KOSPI"),
    ]
    return [
        {"code": code, "name": name, "name_normalized": normalize_stock_name(name),
         "market": market, "is_active": True}
        for code, name, market in seeds
    ]


async def load_stock_codes() -> None:
    records = _fetch_from_krx()
    if not records:
        log.info("using_seed_data", hint="KRX API 불가, 시드 데이터 사용")
        records = _seed_records()

    if not records:
        log.warning("no_stock_codes_found")
        return

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
