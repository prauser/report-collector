"""매매 내역 API 엔드포인트."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.schemas import (
    TradeBase,
    TradeDetailResponse,
    TradeIndicatorResponse,
    TradeListResponse,
    TradeResponse,
    TradeStatsResponse,
    TradeUpdateRequest,
    TradeUploadResponse,
)
from db.models import Trade, TradeIndicator
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

router = APIRouter(prefix="/trades", tags=["trades"])


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


# ---------------------------------------------------------------------------
# POST /trades/upload
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=TradeUploadResponse)
async def upload_trades(
    file: UploadFile,
    dry_run: bool = Query(False, description="true이면 DB 저장 없이 파싱 결과만 반환"),
    broker: str | None = Query(None, description="브로커 명시 (미지정 시 자동 감지)"),
    db: AsyncSession = Depends(get_db),
):
    """CSV 파일을 업로드하여 매매 내역을 저장한다.

    - dry_run=true이면 파싱 결과만 반환 (DB 저장 안 함)
    - broker 미지정 시 헤더로 자동 감지
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
