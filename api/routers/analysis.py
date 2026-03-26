"""섹터 분석 엔드포인트."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Float, and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.schemas import (
    SectorListItem,
    SectorListResponse,
    SectorStockItem,
    SectorStockResponse,
    SectorTopStock,
)
from db.models import Report, ReportAnalysis, ReportSectorMention, ReportStockMention

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/sectors", response_model=SectorListResponse)
async def list_sectors(
    db: AsyncSession = Depends(get_db),
):
    """섹터별 집계: 리포트 수, 평균 심리, 상위 종목."""

    # 섹터별 집계: report_count, avg_sentiment
    sector_stmt = (
        select(
            ReportSectorMention.sector,
            func.count(ReportSectorMention.report_id.distinct()).label("report_count"),
            func.avg(
                func.cast(
                    ReportAnalysis.analysis_data["thesis"]["sentiment"].astext,
                    Float,
                )
            ).label("avg_sentiment"),
        )
        .join(Report, Report.id == ReportSectorMention.report_id)
        .outerjoin(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(ReportSectorMention.sector != "")
        .group_by(ReportSectorMention.sector)
        .order_by(desc("report_count"))
    )

    sector_rows = (await db.execute(sector_stmt)).all()

    if not sector_rows:
        return SectorListResponse(items=[])

    # 섹터별 상위 5개 종목 (유효 코드만)
    sector_names = [row.sector for row in sector_rows]

    # 섹터 내 종목 집계: report_sector_mentions → reports → report_stock_mentions
    top_stocks_stmt = (
        select(
            ReportSectorMention.sector,
            ReportStockMention.stock_code,
            ReportStockMention.company_name,
            func.count(ReportStockMention.report_id.distinct()).label("cnt"),
        )
        .join(Report, Report.id == ReportSectorMention.report_id)
        .join(ReportStockMention, ReportStockMention.report_id == Report.id)
        .where(
            and_(
                ReportSectorMention.sector.in_(sector_names),
                ReportStockMention.stock_code.regexp_match(r"^\d{6}$"),
            )
        )
        .group_by(ReportSectorMention.sector, ReportStockMention.stock_code, ReportStockMention.company_name)
        .order_by(ReportSectorMention.sector, desc("cnt"))
    )

    top_rows = (await db.execute(top_stocks_stmt)).all()

    # group top stocks by sector — keep top 5 per sector
    sector_top: dict[str, list[SectorTopStock]] = {s.sector: [] for s in sector_rows}
    seen: dict[str, set[str]] = {s.sector: set() for s in sector_rows}

    for row in top_rows:
        sector = row.sector
        code = row.stock_code
        if code in seen[sector]:
            continue
        seen[sector].add(code)
        if len(sector_top[sector]) < 5:
            sector_top[sector].append(
                SectorTopStock(
                    stock_code=code,
                    stock_name=row.company_name,
                    report_count=row.cnt,
                )
            )

    items = [
        SectorListItem(
            sector_name=row.sector,
            report_count=row.report_count,
            avg_sentiment=float(row.avg_sentiment) if row.avg_sentiment is not None else None,
            top_stocks=sector_top.get(row.sector, []),
        )
        for row in sector_rows
    ]

    return SectorListResponse(items=items)


@router.get("/sector/{name}", response_model=SectorStockResponse)
async def get_sector_stocks(
    name: str,
    db: AsyncSession = Depends(get_db),
):
    """특정 섹터에 언급된 종목들의 비교 데이터."""

    # sector 존재 확인
    exists_stmt = select(func.count()).where(ReportSectorMention.sector == name)
    count = await db.scalar(exists_stmt)
    if not count:
        raise HTTPException(status_code=404, detail=f"Sector '{name}' not found")

    # 섹터 리포트 ID 집합
    sector_report_ids_select = (
        select(ReportSectorMention.report_id)
        .where(ReportSectorMention.sector == name)
    )

    # 해당 섹터 리포트에 등장한 유효 종목 집계
    # avg_sentiment, latest opinion/target_price 필요
    stocks_stmt = (
        select(
            ReportStockMention.stock_code,
            ReportStockMention.company_name,
            func.count(ReportStockMention.report_id.distinct()).label("report_count"),
            func.avg(
                func.cast(
                    ReportAnalysis.analysis_data["thesis"]["sentiment"].astext,
                    Float,
                )
            ).label("avg_sentiment"),
            func.max(Report.report_date).label("latest_date"),
        )
        .join(Report, Report.id == ReportStockMention.report_id)
        .outerjoin(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(
            and_(
                ReportStockMention.report_id.in_(sector_report_ids_select),
                ReportStockMention.stock_code.regexp_match(r"^\d{6}$"),
            )
        )
        .group_by(ReportStockMention.stock_code, ReportStockMention.company_name)
        .order_by(desc("report_count"))
    )

    stock_rows = (await db.execute(stocks_stmt)).all()

    if not stock_rows:
        return SectorStockResponse(sector_name=name, items=[])

    # get latest opinion/target_price per stock (from reports in this sector)
    codes = list({row.stock_code for row in stock_rows})

    latest_sub = (
        select(
            ReportStockMention.stock_code,
            func.max(Report.report_date).label("max_date"),
        )
        .join(Report, Report.id == ReportStockMention.report_id)
        .where(
            and_(
                ReportStockMention.report_id.in_(sector_report_ids_select),
                ReportStockMention.stock_code.in_(codes),
            )
        )
        .group_by(ReportStockMention.stock_code)
        .subquery("latest_date_sub")
    )

    opinion_stmt = (
        select(
            ReportStockMention.stock_code,
            Report.opinion,
            Report.target_price,
        )
        .join(Report, Report.id == ReportStockMention.report_id)
        .join(
            latest_sub,
            and_(
                latest_sub.c.stock_code == ReportStockMention.stock_code,
                latest_sub.c.max_date == Report.report_date,
            ),
        )
        .where(
            and_(
                ReportStockMention.report_id.in_(sector_report_ids_select),
                ReportStockMention.stock_code.in_(codes),
            )
        )
        .order_by(ReportStockMention.stock_code, desc(Report.id))
        .distinct(ReportStockMention.stock_code)
    )

    opinion_rows = (await db.execute(opinion_stmt)).all()
    opinion_map: dict[str, tuple[str | None, int | None]] = {}
    for row in opinion_rows:
        opinion_map[row.stock_code] = (row.opinion, row.target_price)

    # deduplicate by stock_code (company_name can vary; take latest)
    seen_codes: set[str] = set()
    items: list[SectorStockItem] = []
    for row in stock_rows:
        code = row.stock_code
        if code in seen_codes:
            continue
        seen_codes.add(code)
        opinion, target_price = opinion_map.get(code, (None, None))
        items.append(
            SectorStockItem(
                stock_code=code,
                stock_name=row.company_name,
                report_count=row.report_count,
                avg_sentiment=float(row.avg_sentiment) if row.avg_sentiment is not None else None,
                latest_opinion=opinion,
                latest_target_price=target_price,
            )
        )

    return SectorStockResponse(sector_name=name, items=items)
