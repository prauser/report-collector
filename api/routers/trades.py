"""매매 내역 API 엔드포인트."""
from __future__ import annotations

import dataclasses
from datetime import date, datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.schemas import (
    IndicatorResponse,
    OhlcvResponse,
    PositionResponse,
    TradeBase,
    TradeDetailResponse,
    TradeIndicatorResponse,
    TradeListResponse,
    TradePairResponse,
    TradeResponse,
    TradeStatsResponse,
    TradeUpdateRequest,
    TradeUploadResponse,
)
from db.models import PriceCache, Trade, TradeIndicator, TradePair
from db.session import AsyncSessionLocal
from trades.csv_parsers import detect_broker, get_parser
from trades.csv_parsers.common import TradeRow
from trades.trade_repo import (
    TradeFilters,
    count_trades,
    get_chart_data,
    get_trade,
    get_trade_stats,
    get_trades,
    update_trade_reason,
    update_trade_review,
    upsert_trades,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/trades", tags=["trades"])
ohlcv_router = APIRouter(prefix="/ohlcv", tags=["ohlcv"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _row_to_trade_base(row: TradeRow) -> TradeBase:
    return TradeBase(
        symbol=row.symbol,
        name=row.name,
        side=row.side,
        traded_at=row.traded_at,
        price=row.price,
        quantity=row.quantity,
        amount=row.amount,
        broker=row.broker,
        account_type=row.account_type,
        market=row.market,
        fees=row.fees,
    )


def _trade_to_response(t: Trade) -> TradeResponse:
    return TradeResponse(
        id=t.id,
        symbol=t.symbol,
        name=t.name,
        side=t.side,
        traded_at=t.traded_at,
        price=t.price,
        quantity=t.quantity,
        amount=t.amount,
        broker=t.broker,
        account_type=t.account_type,
        market=t.market,
        fees=t.fees,
        reason=t.reason,
        review=t.review,
        created_at=t.created_at,
    )


def _indicator_to_response(ind: TradeIndicator) -> TradeIndicatorResponse:
    return TradeIndicatorResponse(
        id=ind.id,
        trade_id=ind.trade_id,
        stoch_k_d=ind.stoch_k_d,
        rsi_14=ind.rsi_14,
        macd=ind.macd,
        ma_position=ind.ma_position,
        bb_position=ind.bb_position,
        volume_ratio=ind.volume_ratio,
        snapshot_text=ind.snapshot_text,
    )


def _pair_to_response(pair: TradePair) -> TradePairResponse:
    return TradePairResponse(
        id=pair.id,
        buy_trade_id=pair.buy_trade_id,
        sell_trade_id=pair.sell_trade_id,
        profit_rate=pair.profit_rate,
        holding_days=pair.holding_days,
        matched_qty=pair.matched_qty,
        buy_amount=pair.buy_amount,
        sell_amount=pair.sell_amount,
        buy_fee=pair.buy_fee,
        sell_fee=pair.sell_fee,
    )


def _stoch_set_to_dict(s) -> dict:
    """Convert StochSet dataclass to plain dict for JSON serialization."""
    if dataclasses.is_dataclass(s) and not isinstance(s, type):
        return dataclasses.asdict(s)
    return s


def _serialize_stochastic(stoch: dict) -> dict:
    """Recursively convert stochastic dict to JSON-serializable form."""
    result = {}
    for timeframe, sets in stoch.items():
        result[timeframe] = [_stoch_set_to_dict(s) for s in sets]
    return result


# ---------------------------------------------------------------------------
# Background task helpers
# ---------------------------------------------------------------------------

async def _bg_ohlcv_and_match(symbols: list[str]) -> None:
    """BackgroundTask: OHLCV 수집 + FIFO 매칭. 자체 세션 사용."""
    from trades.ohlcv import fetch_ohlcv_batch
    from trades.pairing import match_trades_fifo

    try:
        async with AsyncSessionLocal() as session:
            log.info("bg_ohlcv_start", symbols=symbols)
            await fetch_ohlcv_batch(session, symbols)
            log.info("bg_ohlcv_done", symbols=symbols)

        # Separate session for each symbol match to avoid lock contention
        for symbol in symbols:
            try:
                async with AsyncSessionLocal() as session:
                    await match_trades_fifo(symbol, session)
                    log.info("bg_fifo_done", symbol=symbol)
            except Exception as exc:
                log.error("bg_fifo_error", symbol=symbol, error=str(exc))
    except Exception as exc:
        log.error("bg_ohlcv_match_error", error=str(exc))


# ---------------------------------------------------------------------------
# POST /trades/upload
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=TradeUploadResponse)
async def upload_trades(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    dry_run: bool = Query(False, description="true이면 DB 저장 없이 파싱 결과만 반환"),
    broker: str | None = Query(None, description="브로커 명시 (미지정 시 자동 감지)"),
    db: AsyncSession = Depends(get_db),
):
    """CSV 파일을 업로드하여 매매 내역을 저장한다.

    - dry_run=true이면 파싱 결과만 반환 (DB 저장 안 함)
    - broker 미지정 시 헤더로 자동 감지
    - 저장 성공 시 OHLCV 수집 + FIFO 매칭을 BackgroundTask로 트리거
    """
    content = await file.read()

    # broker 감지 / 검증
    detected = broker or detect_broker(content)
    if detected == "unknown":
        raise HTTPException(
            status_code=422,
            detail=(
                "브로커를 자동으로 감지하지 못했습니다. "
                "?broker=mirae|kiwoom|samsung 을 명시하거나, 올바른 CSV 파일을 업로드하세요."
            ),
        )

    try:
        parser = get_parser(detected)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        rows: list[TradeRow] = parser.parse(content)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"CSV 파싱 오류: {exc}") from exc

    if dry_run:
        return TradeUploadResponse(
            inserted=0,
            skipped=0,
            preview=[_row_to_trade_base(r) for r in rows],
        )

    result = await upsert_trades(db, rows)

    # Trigger OHLCV collection + FIFO matching in background
    if result["inserted"] > 0:
        symbols = list({r.symbol for r in rows})
        background_tasks.add_task(_bg_ohlcv_and_match, symbols)

    return TradeUploadResponse(
        inserted=result["inserted"],
        skipped=result["skipped"],
        preview=None,
    )


# ---------------------------------------------------------------------------
# GET /trades/stats  — must be BEFORE /{id} to avoid route conflict
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=TradeStatsResponse)
async def trade_stats(
    symbol: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    broker: str | None = Query(None),
    side: str | None = Query(None),
    account_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """매매 통계: 총 거래수, 매수/매도 건수, 총 금액, 종목별 빈도."""
    filters = TradeFilters(
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        broker=broker,
        side=side,
        account_type=account_type,
    )
    stats = await get_trade_stats(db, filters)
    return TradeStatsResponse(**stats)


# ---------------------------------------------------------------------------
# GET /trades/chart-data — must be BEFORE /{id}
# ---------------------------------------------------------------------------

@router.get("/chart-data", response_model=list[TradeResponse])
async def chart_data(
    symbol: str = Query(..., description="종목 코드"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """특정 종목의 매매 내역 (차트 마커용). traded_at ASC 정렬."""
    trades = await get_chart_data(db, symbol=symbol, date_from=date_from, date_to=date_to)
    return [_trade_to_response(t) for t in trades]


# ---------------------------------------------------------------------------
# GET /trades/pairs — FIFO 매칭 결과 목록  (must be BEFORE /{trade_id})
# ---------------------------------------------------------------------------

@router.get("/pairs", response_model=list[TradePairResponse])
async def list_trade_pairs(
    symbol: str | None = Query(None, description="종목 코드 필터 (없으면 전체)"),
    db: AsyncSession = Depends(get_db),
):
    """FIFO 매칭 결과 목록 조회.

    symbol을 지정하면 해당 종목의 매칭 결과만 반환.
    """
    if symbol:
        # Join trade_pairs with trades to filter by symbol
        buy_ids_stmt = sa_select(Trade.id).where(
            Trade.symbol == symbol,
            Trade.side == "buy",
        )
        sell_ids_stmt = sa_select(Trade.id).where(
            Trade.symbol == symbol,
            Trade.side == "sell",
        )
        buy_ids_result = await db.execute(buy_ids_stmt)
        sell_ids_result = await db.execute(sell_ids_stmt)
        buy_ids = [r for r, in buy_ids_result]
        sell_ids = [r for r, in sell_ids_result]

        if not buy_ids and not sell_ids:
            return []

        stmt = sa_select(TradePair).where(
            TradePair.buy_trade_id.in_(buy_ids) | TradePair.sell_trade_id.in_(sell_ids)
        )
    else:
        stmt = sa_select(TradePair)

    result = await db.execute(stmt)
    pairs = result.scalars().all()
    return [_pair_to_response(p) for p in pairs]


# ---------------------------------------------------------------------------
# GET /trades/positions — 평균단가 포지션 (must be BEFORE /{trade_id})
# ---------------------------------------------------------------------------

@router.get("/positions", response_model=list[PositionResponse])
async def list_positions(
    db: AsyncSession = Depends(get_db),
):
    """평균단가 기반 미실현 포지션 목록. 잔여 수량이 있는 종목만 반환."""
    from trades.pairing import calculate_avg_cost

    # Get all distinct symbols
    stmt = sa_select(Trade.symbol).distinct()
    result = await db.execute(stmt)
    symbols = [row for row, in result]

    positions = []
    for sym in symbols:
        pos = await calculate_avg_cost(sym, db)
        if pos.remaining_qty > 0:
            # Convert Decimal prices in open_lots to float for JSON serialization
            open_lots = [
                {
                    "buy_trade_id": lot["buy_trade_id"],
                    "qty": lot["qty"],
                    "price": float(lot["price"]),
                }
                for lot in pos.open_lots
            ]
            positions.append(
                PositionResponse(
                    symbol=pos.symbol,
                    avg_cost=pos.avg_cost,
                    remaining_qty=pos.remaining_qty,
                    open_lots=open_lots,
                )
            )

    return positions


# ---------------------------------------------------------------------------
# GET /trades
# ---------------------------------------------------------------------------

@router.get("", response_model=TradeListResponse)
async def list_trades(
    symbol: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    broker: str | None = Query(None),
    side: str | None = Query(None),
    account_type: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """매매 목록 조회 (필터 + 페이지네이션)."""
    filters = TradeFilters(
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        broker=broker,
        side=side,
        account_type=account_type,
        offset=offset,
        limit=limit,
    )
    total = await count_trades(db, filters)
    trades = await get_trades(db, filters)
    return TradeListResponse(
        items=[_trade_to_response(t) for t in trades],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /trades/{trade_id}/indicators — on-demand 지표 계산
# ---------------------------------------------------------------------------

@router.get("/{trade_id}/indicators", response_model=IndicatorResponse)
async def get_trade_indicators(
    trade_id: int,
    db: AsyncSession = Depends(get_db),
):
    """매매 시점의 기술지표를 price_cache에서 계산하여 반환.

    price_cache에 해당 종목/날짜 데이터가 없으면 404.
    """
    from trades.indicators import calculate_indicators_for_trade, generate_snapshot_text

    trade = await get_trade(db, trade_id=trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")

    target_date = trade.traded_at.date() if hasattr(trade.traded_at, "date") else trade.traded_at

    try:
        result = await calculate_indicators_for_trade(trade.symbol, target_date, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    snapshot = generate_snapshot_text(result)

    return IndicatorResponse(
        stochastic=_serialize_stochastic(result.stochastic),
        ma=result.ma,
        bb=result.bb,
        volume_ratio=result.volume_ratio,
        candle=result.candle,
        snapshot_text=snapshot,
    )


# ---------------------------------------------------------------------------
# GET /trades/{trade_id}
# ---------------------------------------------------------------------------

@router.get("/{trade_id}", response_model=TradeDetailResponse)
async def get_trade_detail(
    trade_id: int,
    db: AsyncSession = Depends(get_db),
):
    """매매 상세 조회 (기술적 지표 포함)."""
    trade = await get_trade(db, trade_id=trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")

    indicator = None
    ind_result = await db.execute(
        sa_select(TradeIndicator).where(TradeIndicator.trade_id == trade_id)
    )
    ind_obj = ind_result.scalar_one_or_none()
    if ind_obj is not None:
        indicator = _indicator_to_response(ind_obj)

    base = _trade_to_response(trade)
    return TradeDetailResponse(
        **base.model_dump(),
        indicator=indicator,
    )


# ---------------------------------------------------------------------------
# PATCH /trades/{trade_id}/reason
# ---------------------------------------------------------------------------

@router.patch("/{trade_id}/reason", response_model=TradeResponse)
async def patch_trade_reason(
    trade_id: int,
    body: TradeUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """매매 이유 수정."""
    if body.reason is None:
        raise HTTPException(status_code=400, detail="reason 필드가 필요합니다.")
    try:
        trade = await update_trade_reason(db, trade_id=trade_id, reason=body.reason)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trade not found")
    return _trade_to_response(trade)


# ---------------------------------------------------------------------------
# PATCH /trades/{trade_id}/review
# ---------------------------------------------------------------------------

@router.patch("/{trade_id}/review", response_model=TradeResponse)
async def patch_trade_review(
    trade_id: int,
    body: TradeUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """복기 메모 수정."""
    if body.review is None:
        raise HTTPException(status_code=400, detail="review 필드가 필요합니다.")
    try:
        trade = await update_trade_review(db, trade_id=trade_id, review=body.review)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trade not found")
    return _trade_to_response(trade)


# ---------------------------------------------------------------------------
# OHLCV router endpoints
# ---------------------------------------------------------------------------

@ohlcv_router.get("/{symbol}", response_model=list[OhlcvResponse])
async def get_ohlcv(
    symbol: str,
    from_date: date | None = Query(None, alias="from", description="시작일 YYYY-MM-DD"),
    to_date: date | None = Query(None, alias="to", description="종료일 YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
):
    """price_cache에서 OHLCV 데이터 조회 (차트용).

    from/to 날짜 필터 지원.
    """
    stmt = sa_select(PriceCache).where(PriceCache.symbol == symbol)
    if from_date:
        stmt = stmt.where(PriceCache.date >= from_date)
    if to_date:
        stmt = stmt.where(PriceCache.date <= to_date)
    stmt = stmt.order_by(PriceCache.date.asc())

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        OhlcvResponse(
            date=row.date,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
        )
        for row in rows
    ]


@ohlcv_router.post("/refresh")
async def refresh_ohlcv(
    background_tasks: BackgroundTasks,
):
    """price_cache에 이미 있는 모든 종목의 OHLCV를 최신 날짜까지 갱신.

    BackgroundTask로 실행 (non-blocking).
    """
    async def _do_refresh() -> None:
        from trades.ohlcv import refresh_cached_symbols
        try:
            async with AsyncSessionLocal() as session:
                result = await refresh_cached_symbols(session)
                log.info("ohlcv_refresh_complete", symbols=len(result))
        except Exception as exc:
            log.error("ohlcv_refresh_error", error=str(exc))

    background_tasks.add_task(_do_refresh)
    return {"status": "refresh_triggered"}
