"""종목 관련 엔드포인트."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.layer2_helpers import _display_title
from api.schemas import (
    StockHistoryItem,
    StockHistoryResponse,
    StockListItem,
    StockListResponse,
)
from db.models import Report, ReportAnalysis, ReportStockMention

router = APIRouter(prefix="/stocks", tags=["stocks"])

# 유효한 종목코드: 6자리 숫자
_VALID_CODE_RE = re.compile(r"^\d{6}$")


def _is_valid_code(code: str) -> bool:
    return bool(_VALID_CODE_RE.match(code))


@router.get("", response_model=StockListResponse)
async def list_stocks(
    search: str | None = Query(None, description="종목명 또는 코드 검색"),
    sort: str | None = Query("report_count", description="정렬: report_count | latest_date"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """유효한 6자리 종목코드별 집계 목록."""

    # report_stock_mentions에서 유효한 code만 추출, 최신 리포트 기준 정보 집계
    # stock_name은 가장 최신 report의 company_name (rsm) 기준
    # avg_sentiment는 report_analysis.analysis_data->thesis->sentiment 평균
    #
    # main aggregation
    stmt = (
        select(
            ReportStockMention.stock_code,
            func.count(ReportStockMention.report_id.distinct()).label("report_count"),
            func.max(Report.report_date).label("latest_report_date"),
            # avg sentiment from report_analysis JSONB
            func.avg(
                func.cast(
                    ReportAnalysis.analysis_data["thesis"]["sentiment"].astext,
                    Float,
                )
            ).label("avg_sentiment"),
        )
        .join(Report, Report.id == ReportStockMention.report_id)
        .outerjoin(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(ReportStockMention.stock_code.regexp_match(r"^\d{6}$"))
        .group_by(ReportStockMention.stock_code)
    )

    if search:
        # Escape LIKE metacharacters before building pattern
        escaped = search.replace("%", r"\%").replace("_", r"\_")
        # filter by code prefix or name (via report.stock_name on the report table)
        # We do a subquery: find stock_codes that match either code or company_name
        stmt = stmt.where(
            (ReportStockMention.stock_code.like(f"%{escaped}%"))
            | (
                ReportStockMention.stock_code.in_(
                    select(ReportStockMention.stock_code)
                    .where(
                        and_(
                            ReportStockMention.stock_code.regexp_match(r"^\d{6}$"),
                            ReportStockMention.company_name.ilike(f"%{escaped}%"),
                        )
                    )
                )
            )
        )

    # sorting
    if sort == "latest_date":
        stmt = stmt.order_by(desc("latest_report_date"), desc("report_count"))
    else:
        stmt = stmt.order_by(desc("report_count"), desc("latest_report_date"))

    # count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.scalar(count_stmt)) or 0

    # paginate
    paged_stmt = stmt.offset(offset).limit(limit)
    rows = (await db.execute(paged_stmt)).all()

    # get stock names: batch fetch company_name for each code from the latest mention
    codes = [row.stock_code for row in rows]
    name_map: dict[str, str | None] = {}
    if codes:
        # For each code, pick company_name from the most recent report
        name_stmt = (
            select(
                ReportStockMention.stock_code,
                ReportStockMention.company_name,
            )
            .join(Report, Report.id == ReportStockMention.report_id)
            .where(ReportStockMention.stock_code.in_(codes))
            .order_by(ReportStockMention.stock_code, desc(Report.report_date), desc(Report.id))
            .distinct(ReportStockMention.stock_code)
        )
        name_rows = (await db.execute(name_stmt)).all()
        for nr in name_rows:
            name_map[nr.stock_code] = nr.company_name

    items = [
        StockListItem(
            stock_code=row.stock_code,
            stock_name=name_map.get(row.stock_code),
            report_count=row.report_count,
            latest_report_date=row.latest_report_date,
            avg_sentiment=float(row.avg_sentiment) if row.avg_sentiment is not None else None,
        )
        for row in rows
    ]

    return StockListResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/{code}/history", response_model=StockHistoryResponse)
async def get_stock_history(
    code: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """특정 종목 관련 리포트 시계열."""
    if not _is_valid_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code: must be 6 digits")

    # total (also serves as existence check)
    total_stmt = (
        select(func.count(Report.id.distinct()))
        .select_from(ReportStockMention)
        .join(Report, Report.id == ReportStockMention.report_id)
        .where(ReportStockMention.stock_code == code)
    )
    total = (await db.scalar(total_stmt)) or 0
    if not total:
        raise HTTPException(status_code=404, detail=f"Stock {code} not found")

    # fetch reports
    reports_stmt = (
        select(Report, ReportAnalysis)
        .join(ReportStockMention, ReportStockMention.report_id == Report.id)
        .outerjoin(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(ReportStockMention.stock_code == code)
        .order_by(desc(Report.report_date), desc(Report.id))
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(reports_stmt)
    rows = result.all()

    items = []
    for report, ra in rows:
        layer2_summary: str | None = None
        layer2_sentiment: float | None = None
        if ra is not None:
            thesis = (ra.analysis_data or {}).get("thesis") or {}
            layer2_summary = thesis.get("summary")
            sentiment_val = thesis.get("sentiment")
            layer2_sentiment = float(sentiment_val) if sentiment_val is not None else None

        items.append(
            StockHistoryItem(
                report_id=report.id,
                broker=report.broker,
                report_date=report.report_date,
                title=_display_title(report, ra),
                opinion=report.opinion,
                target_price=report.target_price,
                layer2_summary=layer2_summary,
                layer2_sentiment=layer2_sentiment,
            )
        )

    # fetch stock name from the most recent mention
    stock_name: str | None = None
    name_stmt = (
        select(ReportStockMention.company_name)
        .join(Report, Report.id == ReportStockMention.report_id)
        .where(ReportStockMention.stock_code == code)
        .order_by(desc(Report.report_date), desc(Report.id))
        .limit(1)
    )
    stock_name = await db.scalar(name_stmt)

    return StockHistoryResponse(
        stock_code=code,
        stock_name=stock_name,
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )
